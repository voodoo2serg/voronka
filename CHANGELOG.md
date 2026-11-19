# Changelog

## Unreleased — CJ-108 Person timeline in CRM contact drawer (VR-186)

- `GET /api/crm/contacts/{id}` includes `person_interactions` + `person_communications` when Profile is linked to a Person.
- History queries scoped to the contact's `project_id` (no cross-project Person leak).
- Communications fetched by IDs referenced from the returned interactions (aligned sets).
- CRM «История» renders Signal CE writeback (kind / status / body_preview) ahead of manual activities.
- Tests: CJ-108 ingest contact detail + project-scope / aligned-comms coverage.

## Unreleased — PAY-101 Robokassa Receipt (VR-187)

- Robokassa checkout sends fiscal `Receipt` (54-FZ nomenclature) and includes URL-encoded Receipt in `SignatureValue`.
- Module `app/robokassa_receipt.py`; env names: `ROBOKASSA_RECEIPT_TAX`, `ROBOKASSA_RECEIPT_SNO`, optional payment method/object.
- Checkout GET URL **double-encodes** Receipt on the wire (`%257B…`); signature keeps single-encoded form (Robokassa official examples).
- CRM fiscal checklist honesty: `receipt_in_adapter=true`; shop fiscal / staging / owner approval remain open; **`ROBOKASSA_LIVE_READY` not flipped**.
- Tests: `tests/test_robokassa_receipt.py`.

## Unreleased — Media SoR Wave B (VR-180)

- Consume Signal/Registry `asset_id` via M2M: `POST …/assets/ensure-from-signal`, import accepts `signal_asset_id`.
- Campaign CRM + M2M preflight/launch rewrite Signal ids → Voronka bank when fetch configured.
- CRM soft-attach: staff + M2M `…/persons/{id}/media-refs` + fetch-by-asset_id content proxy.
- Env (names only): `MEDIA_REGISTRY_*`, `SIGNAL_INTERNAL_MEDIA_*`. No cutover flag flip.
- Tests: `tests/test_media_sor_wave_b.py`.

## Unreleased — PDialog P0 scenario publish (signal#20)

- M2M scopes `scenario:publish|pause|status` (+ `scenario:*`); default grant includes `scenario:*`.
- `POST /api/crm/m2m/projects/{pid}/scenarios/versions` — accept/store immutable Funnel+Version from Signal (idempotent `external_idempotency_key`).
- Runtime-ready graphs (TYPES, including VR-124 `llm_reply`/`escalate`/`operator_pause`) activate after `validate_bindings`. Remaining forward-compat descriptors (`send_template`/`tag_contact`) stay **stored inactive** (`activated=false`; do not set `published_version_id`).
- `POST .../executions/{execution_id}/pause` and `GET .../scenarios/versions/{id}/status`.
- `validate_published_graph` reuses classic invariants and extends allowed types for forward-compat descriptors; rejects `auto_send`.
- Mini App `/api/funnels/{id}/publish` remains strict `validate_graph` (no second canvas).

## Unreleased — CAMP-101 segment export (M2M)

- **CAMP-101:** `POST /api/crm/m2m/projects/{pid}/segments/export` — paginated sendable
  Person + ContactPoint export for Signal mass campaigns (`contact:segment:read`).
  Default `only_sendable=true` filters via `send_allowed` (`purpose=marketing`,
  `missing_policy=deny`). Profile-only `GET …/contacts` remains unchanged.

## Unreleased — IMPORT-101 audience import (Person + Consent)

- Admin/ops CSV import: `POST /api/crm/projects/{pid}/audience-imports/dry-run`, `POST …/audience-imports`, `POST /api/crm/audience-imports/{id}/commit`.
- Creates/updates `Person` + `ContactPoint` (email / telegram id / username) idempotently; marketing Consent is either `unknown` (quarantine, only if missing) or `granted` (explicit evidence only). Quarantine preserves granted/denied/revoked.
- Ambiguity: all matched people + all owners of an address; multi-owner / cross-person → reject (no silent merge).
- Commit requires current consent_mode/evidence matching dry-run batch (409 otherwise); CRM UI invalidates preview on mode/evidence change.
- `send_allowed(..., purpose=marketing, missing_policy=deny)` stays closed for quarantine rows — no mass send.
- CRM UI «Импорт данных» → mode «Аудитория Person+Consent» with dry-run counts.
- Tests: `tests/test_audience_import.py`.

## Unreleased — CJ-108 SIGNAL_M2M_SCOPES (env allowlist)

- `.env.example` `SIGNAL_M2M_SCOPES` now includes `timeline:write` (already in `DEFAULT_SCOPES`; explicit env on VPS was missing it → ingest 403).

## Unreleased — CRM-102 / MAIL-101 send-policy (v5.2)

- **CRM-102:** `POST /api/crm/projects/{pid}/send-policy/check` (staff) and M2M twin under `/api/crm/m2m/...` — contact+channel+purpose → `{allowed, reason}`; Voronka remains consent SoR; **no listmonk**.
- `person_graph.send_allowed(...)` with `missing_policy=soft_allow|deny`; `marketing_allowed` delegates to it.
- Marketing opt-out still does not block `transactional` purpose.

## Unreleased — Wave C migration downgrade fix (2026-11-16)

- Implement real `0008.downgrade()` so CI `alembic downgrade base` can drop `crm_payments` (drops `crm_refunds` / `crm_payment_allocations` first).

## Wave C customer graph + ledger (2026-11-15)

Signal v5.1 overnight Wave C in Voronka. Owner constraints A–G: no listmonk; HMAC M2M kept; money SoR Voronka; additive migrations; CRM UI not broken.

### Added

- **CRM-001 / CRM-005:** `Person`, `PersonLink`, `MergeReview`, `ContactPoint`, `ContactVerification`; profiles/contacts gain nullable `person_id`; auto-provision on contact create; merge apply/undo.
- **CRM-002:** Customer 360 timeline API (staff + M2M).
- **CRM-003:** `Consent` by channel purpose; marketing deny/revoke blocks `available_identity` and pauses journeys.
- **CRM-006:** `Interaction`, `Communication`, `DeliveryEvent`, `Reply` with webhook-style dedup keys.
- **CRM-007:** `Journey` / `JourneyStep` warmup sequencing + manual override.
- **CRM-008:** `Agreement` distinct from Deal; explicit sign with evidence.
- **PAY-002:** `OrderLine`, `Receivable`, `PaymentAllocation`, `Refund`; ledger summary endpoint; hooks on order/payment/Robokassa.
- **AUTH-004 (scaffold):** `OidcLink` issuer+sub mapping; no email-only ACL elevation.
- Alembic **`0008_wave_c_customer_graph`**.
- Docs: `docs/WAVE_C_CUSTOMER_GRAPH.md`.
- Tests: `tests/test_wave_c_customer_graph.py`.

### M2M (stable for Signal Wave D)

- Existing HMAC campaign/asset/hub routes and scopes **unchanged**.
- Additive scopes: `person:read`, `timeline:read`, `timeline:write`, `consent:write`.
- Additive routes: M2M person + timeline under `/api/crm/m2m/projects/{pid}/persons/...`; CJ-108 `POST …/communications/ingest`.

### Deferred

- Full Keycloak interactive CRM login (AUTH-002 dependency).
- CRM UI redesign around Person graph.
- OAuth2 client-credentials replacement for HMAC (SIG-004 later).
