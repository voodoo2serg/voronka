#!/usr/bin/env python3
"""AUTH-203: explicit issuer+sub → existing CRM Account link (dry-run / apply / rollback).

Never invents owner/staff by email. Never creates a second owner.
Default mode is dry-run. Secrets are never printed.
"""
from __future__ import annotations

import argparse
import os

from sqlalchemy import select

from app import auth
from app import person_graph
from app.crm_models import Account, OidcLink
from app.crm_service import uid
from app.db import Admin, Session, now

DEFAULT_ISSUER = "https://auth.solo-pr.ru/realms/solo-pr"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issuer", default=os.getenv("KEYCLOAK_ISSUER", DEFAULT_ISSUER).rstrip("/"))
    parser.add_argument("--subject", required=True, help="Keycloak user id (sub), never email")
    parser.add_argument("--account-id", required=True, help="Existing crm_accounts.id")
    parser.add_argument("--person-id", default="", help="Optional crm_persons.id")
    parser.add_argument("--email-hint", default="", help="Hint only; never used for authorization")
    parser.add_argument("--apply", action="store_true", help="Write link (default: dry-run)")
    parser.add_argument("--rollback", action="store_true", help="Revoke matching link")
    return parser.parse_args()


def inventory(db, issuer: str, subject: str, account_id: str) -> None:
    owners = list(db.scalars(select(Admin).where(Admin.role == "owner", Admin.active.is_(True))))
    print(f"active_owners={len(owners)} ids={[o.telegram_id for o in owners]}")
    account = db.get(Account, account_id)
    if not account:
        raise SystemExit(f"account_missing:{account_id}")
    admin = db.get(Admin, account.admin_id)
    if not admin:
        raise SystemExit(f"admin_missing:{account.admin_id}")
    print(
        f"target_account login={account.login} admin_id={account.admin_id} "
        f"role={admin.role} active={admin.active}"
    )
    if admin.role == "owner" and len(owners) != 1:
        raise SystemExit("owner_invariant_broken: expected exactly one active owner")
    prior = db.scalar(select(OidcLink).where(OidcLink.issuer == issuer, OidcLink.subject == subject))
    if prior:
        print(
            f"existing_link id={prior.id} status={prior.provisioning_status} "
            f"account_id={prior.account_id}"
        )
    else:
        print("existing_link=none")
    linked_to_account = list(
        db.scalars(
            select(OidcLink).where(
                OidcLink.account_id == account_id,
                OidcLink.provisioning_status == "linked",
            )
        )
    )
    print(f"linked_rows_for_account={len(linked_to_account)}")


def apply_link(args: argparse.Namespace) -> None:
    with Session.begin() as db:
        inventory(db, args.issuer, args.subject, args.account_id)
        account = db.get(Account, args.account_id)
        admin = db.get(Admin, account.admin_id)
        prior = db.scalar(
            select(OidcLink).where(OidcLink.issuer == args.issuer, OidcLink.subject == args.subject)
        )
        if prior and prior.account_id and prior.account_id != args.account_id:
            if prior.provisioning_status != "revoked":
                prior.provisioning_status = "conflict"
                raise SystemExit("conflict: subject already linked to another account")
        if not args.apply and not args.rollback:
            print("dry_run_ok: pass --apply to write, or --rollback to revoke")
            return
        if args.rollback:
            if not prior:
                print("rollback_noop: no link")
                return
            prior.provisioning_status = "revoked"
            prior.updated_at = now()
            # Same as HTTP /oidc-links/{id}/revoke: drop active CRM sessions.
            target = db.get(Account, prior.account_id) if prior.account_id else account
            if target:
                auth.revoke_sessions(target.admin_id)
            print(f"rollback_ok link_id={prior.id} sessions_revoked=1")
            return
        if prior:
            if prior.account_id not in (None, args.account_id):
                raise SystemExit("conflict: refuse silent rebind")
            prior.account_id = args.account_id
            if args.person_id:
                prior.person_id = args.person_id
            prior.email_hint = args.email_hint or prior.email_hint
            prior.provisioning_status = "linked"
            prior.updated_at = now()
            row = prior
        else:
            row = OidcLink(
                id=uid(),
                issuer=args.issuer,
                subject=args.subject,
                account_id=args.account_id,
                person_id=args.person_id or None,
                provisioning_status="linked",
                email_hint=args.email_hint,
                created_at=now(),
                updated_at=now(),
            )
            db.add(row)
            db.flush()
        # ARCH-L2: staff link always gets a Person (explicit --person-id or auto-ensure).
        person, attached = person_graph.ensure_person_for_oidc_link(
            db,
            row,
            display_name=account.login or "",
            email_hint=args.email_hint or account.email or "",
        )
        print(
            f"apply_ok link_id={row.id} role={admin.role} "
            f"person_id={person.id} person_attached={attached}"
        )
        cov = person_graph.oidc_person_coverage(db)
        print(
            f"coverage persons_with_oidc={cov['persons_with_oidc']} "
            f"links_missing_person={cov['links_missing_person']}"
        )


def main() -> None:
    args = parse_args()
    if args.apply and args.rollback:
        raise SystemExit("choose either --apply or --rollback")
    if not args.issuer.endswith("/realms/solo-pr"):
        raise SystemExit("issuer must be solo-pr realm")
    if "@" in args.subject:
        raise SystemExit("subject must be Keycloak user id, not email")
    apply_link(args)


if __name__ == "__main__":
    main()
