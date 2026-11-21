# Границы данных: voronka × solo_pr × lms_end

**ADR (VOR-9).** Репозитории и базы **не** сливаются. Деньги и AccessGrant — только voronka. ПДн публичного сайта — только solo_pr (RU cloud). Академический SoR — только lms_end (OpenOlat).

## Что где хранится

| Класс | Владелец | Запрещено |
|---|---|---|
| Боты, воронки, CRM, Person/ContactPoint/Consent/Journey/Agreement, media volume, Payment/Order/OrderLine/Receivable/Allocation/Refund/Invoice/AccessGrant, статистика продаж | **voronka** (home) | Писать ledger или Person graph в Signal/сайте/LMS; shared Postgres |
| Регистрации, согласия 152‑ФЗ, ЛК, CMS, lead store | **solo_pr** (RU cloud) | Shared Postgres с home; Cloudflare D1 для ПДн |
| Курсы, группы OpenOlat, enrollment adapter | **lms_end** (home) | Создавать Robokassa-заказы; FK в CRM money tables |
| AI prompts / draft context | **voronka** | Автоотправка клиенту без действия менеджера |

**Shared Postgres между репо — запрещён.** Связь только через signed HTTPS / M2M Bearer.

## Signal (СИГНАЛ) M2M

Studio/orchestrator at `signal.vodopetov.ru` — **not** a second CRM/Outbox. Scoped HMAC API under `/api/crm/m2m/*` (see `docs/M2M_COMMUNICATIONS_OPENAPI.yaml`). Auth: `SIGNAL_M2M_SECRET` + `X-Signal-*` headers (scopes, jti replay). Staff `crm_session` must not be reused.

**Wave C:** Person/ContactPoint/timeline live in Voronka. Signal reads via additive M2M scopes `person:read` / `timeline:read` — must not invent Person tables. Existing campaign/asset/hub M2M paths remain stable for Signal Wave D.

**CJ-108 / ARCH-L4:** Signal CE may write timeline via `timeline:write` → `POST …/communications/ingest` (Communication + DeliveryEvent + Interaction). Missing project email creates a thin Person+Profile+ContactPoint (`source=signal_ce`) so welcome writeback is not 404. Payload may carry a short plain `body_preview` only — never full HTML/email bodies or secrets.

**Media SoR Wave B:** originals stay in Signal Media Registry. Voronka may cache delivery bytes keyed by Registry `asset_id` (`signal_asset_id` in asset metadata) and soft-attach the same id on Person timeline — **no** parallel CRM originals library; **no** `file_id` over M2M.

Chatwoot и EspoCRM **не** входят в стек.

## API, которые сайт (solo_pr) может вызывать

Без прямого доступа к БД voronka:

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/api/crm/integrations/solo_pr/leads` | `lead.created` (идемпотентно по `event_id`\|`idempotency_key`; dual-read `type`\|`kind`, nested `data`\|flat) |
| `POST` | `/api/crm/integrations/leads` | то же; `source=solo_pr\|raga\|raga_site` (маркетинговые лиды RAGA → Person, без shared DB) |
| `POST` | `/api/crm/integrations/commerce/quote` | Checkout quote / Order в ledger voronka |
| `GET\|POST` | `/api/crm/robokassa/result` | ResultURL провайдера (или proxy с сайта на этот URL) |

Auth: `Authorization: Bearer <INTEGRATION_INBOUND_TOKEN|SOLO_PR_WEBHOOK_SECRET>` **или** HMAC `X-Voronka-*` / `X-Solo-*` / `X-Lms-*` (`Timestamp` + `Signature: sha256=<hex>`).

## Исходящие события voronka

| Событие | Куда | Env |
|---|---|---|
| `deal.paid` | solo_pr | `SOLO_PR_EVENTS_URL` + `SOLO_PR_WEBHOOK_SECRET` |
| `access.granted` / `access.revoked` | solo_pr | то же |
| `enrollment.desired` / `enrollment.revoked` | lms_end | `LMS_END_ENROLLMENT_URL` + `LMS_END_WEBHOOK_SECRET` / `LMS_M2M_TOKEN` |

Очередь: таблица `crm_integration_outbox`, доставка worker'ом, идемпотентность по `event_id`. Outbound dual-emit: `type`+`kind`, `event_id`+`idempotency_key`; solo headers `X-Voronka-*`+`X-Solo-*`; LMS EnrollmentBody (`action`, `course_id`\|`commerce_product_id`, `capability=course.view`) + `X-Voronka-*`/`X-Lms-*`.

## Входящие от LMS

| Событие | Путь |
|---|---|
| `learning.completed` | `POST /api/crm/integrations/lms_end/completions` (dual-read `course_id`/`commerce_product_id`/`resource`, `keycloak_sub`; auth `X-Lms-*` или `X-Voronka-*` / Bearer) |

## Миграции

Никаких миграций, связывающих схемы LMS/сайта с `funnel`. Compose voronka не требует Chatwoot/Espo.
