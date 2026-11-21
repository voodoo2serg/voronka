# AUTH-203 — CRM Keycloak OIDC cutover

Browser OIDC Authorization Code + PKCE via CRM BFF (`/api/crm/oidc/*`).
Verified `issuer` + `sub` maps only through `crm_oidc_links` to an **existing** `crm_accounts` row. Email never elevates role or creates a second owner.

## Ops sequence (VPS)

1. Dry-run / apply Keycloak confidential client `voronka-crm`:
   `python3 /opt/voronka/scripts/configure_crm_oidc_client.py`
   `python3 /opt/voronka/scripts/configure_crm_oidc_client.py --apply`
   Merge the generated `/root/voronka-crm-oidc-secret-*.env` into `/opt/voronka/.env` (never commit).
   Bootstrap file emits `CRM_OIDC_CUTOVER=0`; flip to `1` only after link + SSO proof.
2. Rebuild/restart API: `docker compose up -d --build api worker`
3. Smoke: `GET https://crm.solo-pr.ru/api/crm/oidc/status` → `enabled: true`
4. Link existing owner (inventory IDs; dry-run first):
   `docker compose exec api python -m scripts.link_crm_oidc --subject <kc-sub> --account-id <uuid> [--person-id <uuid>] [--email-hint serg@…]`
   `… --apply` then prove browser SSO. Rollback: same args with `--rollback` (revokes link; keeps Account).
5. After linked SSO works, set `CRM_OIDC_CUTOVER=1` and restart. Routine staff password creation stops; local login remains audited break-glass.

## Invariants

- Exactly one CRM `owner`; never create a second via OIDC or email match.
- Legacy `crm_session` cookies keep working (keyed by `telegram_id`); revoke on staff disable / OIDC link revoke.
- MFA deferred to AUTH-210.

## ARCH-L2 — Person on first SSO

Green Keycloak SSO must leave a Voronka `Person` on `crm_oidc_links.person_id` (no empty Person after linked staff login).

| Path | Behavior |
|------|----------|
| Staff callback `/api/crm/oidc/callback` | After verified linked Account, `ensure_person_for_oidc_link` attaches Person + `PersonLink(service=keycloak, native_id=sub)` |
| Learner/product M2M `POST /api/crm/m2m/oidc-person` | Scope `person:write`; creates/ensures OidcLink+Person; **never** invents Account |
| Ops backfill | `python -m scripts.backfill_oidc_persons` (dry-run) / `--apply` |
| Metric | `GET /api/crm/oidc/coverage` and `coverage` on `/api/crm/oidc/status` → `persons_with_oidc` |

Email claim / hint is ContactPoint only — never merges Persons or elevates ACL. Q5=C: RAGA billing stays out of Voronka.
