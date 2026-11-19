"""Create browser credentials for an existing owner. Password is prompted, not an argument.

After AUTH-203 cutover (CRM_OIDC_CUTOVER=1), refuse unless --break-glass is passed.
Never creates a second owner; refuses if an owner Account already exists.
"""
import argparse
import getpass
import os
from sqlalchemy import select
from app.db import Session, Admin
from app.crm_models import Account
from app.crm_oidc_cutover import cutover_enabled
from app.crm_service import uid, password_hash, normalize_email


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--break-glass",
        action="store_true",
        help="Allow local password creation after OIDC cutover (audited ops only)",
    )
    args = parser.parse_args()
    if cutover_enabled() and not args.break_glass:
        raise SystemExit(
            "CRM_OIDC_CUTOVER=1: штатное создание локального пароля владельца отключено. "
            "Используйте Keycloak + scripts.link_crm_oidc, либо --break-glass для аварии."
        )
    login = input("Логин владельца: ").strip().lower()
    email = normalize_email(input("Email (необязательно): "))
    if not 3 <= len(login) <= 120:
        raise SystemExit("Логин: от 3 до 120 символов")
    password = getpass.getpass("Пароль (от 12 символов): ")
    if password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпадают")
    digest = password_hash(password)
    with Session.begin() as db:
        owner = db.get(Admin, int(os.environ["OWNER_TELEGRAM_ID"]))
        if not owner or owner.role != "owner" or not owner.active:
            raise SystemExit("Сначала выполните alembic upgrade head")
        if db.scalar(select(Account).where(Account.admin_id == owner.telegram_id)) or db.scalar(select(Account).where(Account.login == login)):
            raise SystemExit("Аккаунт уже существует; используйте восстановление")
        db.add(Account(id=uid(), admin_id=owner.telegram_id, login=login, password_hash=digest, email=email))
    if args.break_glass and cutover_enabled():
        print("Аварийный аккаунт создан (break-glass). Зафиксируйте причину вне git.")
    else:
        print("Аккаунт создан. Откройте /crm/. Для восстановления сначала подтвердите email.")


if __name__ == "__main__":
    main()
