# Границы данных: voronka × solo_pr × lms_end

**ADR (VOR-9).** Репозитории и базы **не** сливаются. Деньги и AccessGrant — только voronka. ПДн публичного сайта — только solo_pr (RU cloud). Академический SoR — только lms_end (OpenOlat).

## Что где хранится

| Класс | Владелец | Запрещено |
|---|---|---|
| Боты, воронки, CRM, media volume, Payment/Order/Invoice/AccessGrant, статистика продаж | **voronka** (home) | Писать ledger в SQLite сайта или learning DB |
| Регистрации, согласия 152‑ФЗ, ЛК, CMS, lead store | **solo_pr** (RU cloud) | Shared Postgres с home; Cloudflare D1 для ПДн |
| Курсы, группы OpenOlat, enrollment adapter | **lms_end** (home) | Создавать Robokassa-заказы; FK в CRM money tables |
| AI prompts / draft context | **voronka** | Автоотправка клиенту без действия менеджера |

**Shared Postgres между репо — запрещён.** Связь только через signed HTTPS / M2M Bearer.

Chatwoot и EspoCRM **не** входят в стек.

## API, которые сайт (solo_pr) может вызывать

Без прямого доступа к БД voronka:

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/api/crm/integrations/solo_pr/leads` | `lead.created` (идемпотентно по `event_id`) |
| `POST` | `/api/crm/integrations/commerce/quote` | Checkout quote / Order в ledger voronka |
| `GET\|POST` | `/api/crm/robokassa/result` | ResultURL провайдера (или proxy с сайта на этот URL) |

Auth: `Authorization: Bearer <INTEGRATION_INBOUND_TOKEN|SOLO_PR_WEBHOOK_SECRET>` **или** HMAC `X-Voronka-Timestamp` + `X-Voronka-Signature`.

## Исходящие события voronka

| Событие | Куда | Env |
|---|---|---|
| `deal.paid` | solo_pr | `SOLO_PR_EVENTS_URL` + `SOLO_PR_WEBHOOK_SECRET` |
| `access.granted` / `access.revoked` | solo_pr | то же |
| `enrollment.desired` / `enrollment.revoked` | lms_end | `LMS_END_ENROLLMENT_URL` + `LMS_END_WEBHOOK_SECRET` / `LMS_M2M_TOKEN` |

Очередь: таблица `crm_integration_outbox`, доставка worker'ом, идемпотентность по `event_id`.

## Входящие от LMS

| Событие | Путь |
|---|---|
| `learning.completed` | `POST /api/crm/integrations/lms_end/completions` |

## Миграции

Никаких миграций, связывающих схемы LMS/сайта с `funnel`. Compose voronka не требует Chatwoot/Espo.
