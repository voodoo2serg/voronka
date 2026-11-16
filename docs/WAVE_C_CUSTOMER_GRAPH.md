> **Актуальность SIGNAL v5.2 (16.11.2026):** Канонические платформенные владельцы данных, SSO, Umami, suppression, Media Registry, RAGA Brand Voice и исполнимые тикеты — в [единой архитектуре solo_pr](https://github.com/voodoo2serg/solo_pr/blob/main/docs/signal/PLATFORM_ACTUAL_ARCHITECTURE_v5_2.md). Этот файл сохраняется как описание *локального модуля или исторического этапа*. При расхождениях статусов, доменов, прежних планов listmonk/SSO и ролей источников истины приоритет имеет v5.2; факт DEPLOYED/E2E_PASS должен подтверждаться текущими smoke/CI, а не текстом старого документа.

## Актуальность SIGNAL v5.2

План: [тикеты v5.2](https://github.com/voodoo2serg/solo_pr/blob/main/docs/signal/CONNECTIVITY_TICKETS_v5_2.md). Не изменять старую историю миграций и релизов ради документации.

# Wave C — Customer graph + money (Voronka)

**Date:** 2026-11-15  
**Tickets:** CRM-001, CRM-005, CRM-002, CRM-003, CRM-006, PAY-002, CRM-007, CRM-008, AUTH-004  
**Constraints:** additive Alembic only; HMAC M2M kept; money SoR = Voronka; CRM UI not rewritten; no listmonk.

## What landed

| Ticket | Delivery |
|--------|----------|
| **CRM-001** | `crm_persons`, `crm_person_links`, `crm_merge_reviews`; `contacts.person_id` / `crm_profiles.person_id`; auto Person on profile create; merge queue with apply + **undo** |
| **CRM-005** | `crm_contact_points` + `crm_contact_verifications`; hashed `address_hash`; backfill from email/phone/Identity; verify API |
| **CRM-002** | `GET /api/crm/projects/{pid}/persons/{id}/timeline` (+ M2M twin); correlates interactions, activities, deals, orders, payments, agreements, journeys |
| **CRM-003** | `crm_consents` by contact_point × purpose; revoke/deny pauses journeys + sets `profile.stopped`; `available_identity` blocks marketing send |
| **CRM-006** | `crm_interactions`, `crm_communications`, `crm_delivery_events`, `crm_replies`; idempotent `external_event_key`; accepted ≠ read |
| **PAY-002** | `crm_order_lines`, `crm_receivables`, `crm_payment_allocations`, `crm_refunds`; wired on order create + manual/Robokassa payments; `GET /orders/{id}/ledger` |
| **CRM-007** | `crm_journeys` / `crm_journey_steps`; create with N steps; override + opt-out on consent deny |
| **CRM-008** | `crm_agreements` separate from Deal; won deal does **not** imply signed agreement; explicit sign endpoint |
| **AUTH-004** | `crm_oidc_links` issuer+sub → account/contact/person/profile; **no** email-only bind; conflict on rebind; full OIDC login deferred to AUTH-002 |

## Migration

- `migrations/versions/0008_wave_c_customer_graph.py` (`0007` → `0008`)
- Nullable FKs only on existing tables; no drops

## M2M contract stability (Signal Wave D)

**Unchanged:** `/api/crm/m2m/*` campaign/asset/segment/hub paths, HMAC headers, jti replay, existing scopes.

**Additive scopes (optional):** `person:read`, `timeline:read`, `consent:write`.

**New routes:**

- `GET /api/crm/m2m/projects/{pid}/persons/{person_id}`
- `GET /api/crm/m2m/projects/{pid}/persons/{person_id}/timeline`

Signal must **not** invent Person tables; read Voronka.

## Staff CRM APIs (session auth)

- Person ensure / get / contact-points / verify / merges
- Timeline, consents, interactions, delivery-events
- Journeys + override
- Agreements + sign
- Order ledger
- OIDC link bridge

## Explicit non-goals / deferred

- Full Keycloak OIDC staff login UI (needs AUTH-002)
- listmonk
- Rewriting CRM frontend for Person graph
- OAuth2 client-credentials (SIG-004 later; HMAC transitional per owner F)
- Dual money SoR / shared Postgres with Signal

## Tests

`tests/test_wave_c_customer_graph.py` — person/merge/consent/timeline/journey/agreement/ledger/OIDC/M2M.
