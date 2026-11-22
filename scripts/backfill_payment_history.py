"""Backfill deal.paid Person Interactions for sandbox/manual payments (A9-03).

Dry-run by default; pass --apply to write. Idempotent via external_event_key.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app import integrations
from app.crm_models import Interaction, Order, Payment, Profile
from app.db import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write missing deal.paid rows")
    args = parser.parse_args(argv)
    written = 0
    scanned = 0
    skipped = 0
    with Session.begin() as db:
        payments = list(
            db.scalars(select(Payment).where(Payment.kind == "payment").order_by(Payment.created_at))
        )
        for payment in payments:
            scanned += 1
            order = db.get(Order, payment.order_id)
            if order is None:
                continue
            profile = db.get(Profile, order.profile_id)
            if profile is None:
                continue
            key = f"deal.paid:{payment.id}"
            prior = db.scalar(
                select(Interaction).where(
                    Interaction.source_service == "voronka",
                    Interaction.external_event_key == key,
                )
            )
            if prior:
                skipped += 1
                print(f"skip_exists payment={payment.id}")
                continue
            if not args.apply:
                print(
                    f"would_write payment={payment.id} order={order.id} "
                    f"profile={profile.id} evidence={str(payment.evidence or '')[:60]!r}"
                )
                written += 1
                continue
            integrations.record_payment_interaction(
                db,
                payment_id=payment.id,
                order=order,
                profile=profile,
                financial={"paid_minor": payment.amount_minor},
                provider=payment.provider,
                evidence=payment.evidence or "",
            )
            print(f"wrote payment={payment.id}")
            written += 1
    print(
        f"{'applied' if args.apply else 'dry_run'} "
        f"scanned={scanned} rows={written} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
