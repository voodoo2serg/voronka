#!/usr/bin/env python3
"""ARCH-L2: backfill Person on existing linked crm_oidc_links (dry-run / apply).

Does not create Accounts. Does not merge by email. Safe to re-run.
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app import person_graph
from app.crm_models import Account, OidcLink
from app.db import Session


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write missing person_id values")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with Session.begin() as db:
        before = person_graph.oidc_person_coverage(db)
        print(
            "before "
            f"persons_with_oidc={before['persons_with_oidc']} "
            f"links_missing_person={before['links_missing_person']} "
            f"oidc_links_linked={before['oidc_links_linked']}"
        )
        missing = list(
            db.scalars(
                select(OidcLink).where(
                    OidcLink.provisioning_status == "linked",
                    OidcLink.person_id.is_(None),
                )
            )
        )
        print(f"candidates={len(missing)}")
        if not args.apply:
            print("dry_run_ok: pass --apply to attach Person rows")
            return
        attached = 0
        for link in missing:
            display = ""
            email = link.email_hint or ""
            if link.account_id:
                account = db.get(Account, link.account_id)
                if account:
                    display = account.login or ""
                    email = email or account.email or ""
            person, changed = person_graph.ensure_person_for_oidc_link(
                db, link, display_name=display, email_hint=email,
            )
            if changed:
                attached += 1
            print(f"link_id={link.id} person_id={person.id} attached={changed}")
        after = person_graph.oidc_person_coverage(db)
        print(
            f"apply_ok attached={attached} "
            f"persons_with_oidc={after['persons_with_oidc']} "
            f"links_missing_person={after['links_missing_person']}"
        )


if __name__ == "__main__":
    main()
