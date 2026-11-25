# Интеграции voronka (контракты)

См. также [DATA_BOUNDARIES.md](./DATA_BOUNDARIES.md).

## Money → entitlement → neighbors

```text
Robokassa ResultURL
  → ProviderEvent (redacted, unique event_key)
  → Payment (live only; test invoices do not create Payment)
  → EntitlementJob (idempotent per payment_id)
  → IntegrationOutbox: deal.paid → solo_pr
  → если есть AccessGrant и порог оплаты:
       access.granted → solo_pr
       enrollment.desired → lms_end (capability course.view|course.enter)
       course.access_granted → Signal CE (event_type; event_id=`course_access_granted:{grant_id}`)
```

Signal CE `event_id` must match `[a-zA-Z0-9:_-]{8,120}` (no dots). `event_type` stays `course.access_granted`.
Legacy dotted outbox/Interaction keys `course.access_granted:{grant_id}` are **adopted** to the CE-safe form on enqueue and on OPS-104 `reconcile/apply` requeue (no second row / timeline duplicate).

Сбой job/outbox **не** удаляет Payment.

**OPS-104 reconcile:** dry-run `GET /api/crm/projects/{pid}/reconcile/preview`, mutate `POST …/reconcile/apply` (`acknowledge_mutate`), DLQ `GET …/integration-outbox/dlq`, M2M aggregate `GET /api/crm/m2m/projects/{pid}/integrations/status` (`integrations:status:read`). Enrollment `event_id` стабилен: `enrollment.desired:{grant_id}` (revision только в payload). `scope=enrollment` also detects dead Signal `course.access_granted` via modern + legacy keys. Подробности: [OPS_104_EVENT_RECONCILIATION.md](./OPS_104_EVENT_RECONCILIATION.md).

## Wire contract v1 (dual-read / dual-emit)

Межрепо HTTP без shared DB. Приём **и** старых, **и** новых имён; исходящие шлют оба варианта.

| Направление | Поля | Auth |
|---|---|---|
| solo_pr / RAGA → voronka `lead.created` | `type`\|`kind`, `idempotency_key`\|`event_id`, nested `data` или flat; `source=solo_pr\|raga\|raga_site` | Bearer **или** HMAC `X-Voronka-*` / `X-Solo-*` (`sha256=<hex>`, base `{ts}.{body}`, skew ≤300s). Paths: `/integrations/leads` и legacy `/integrations/solo_pr/leads` |
| voronka → solo_pr `deal.paid` / `access.*` | emit `type` **и** `kind`, `event_id` **и** `idempotency_key`; product + optional `course_id`/`club`/`commerce_product_id`/`external_id` | `X-Voronka-*` **и** `X-Solo-*` + Bearer |
| voronka → lms_end enrollment | LMS `EnrollmentBody`: `schema_version`, `action` grant\|revoke, `keycloak_sub`/email/`voronka_profile_id`, `course_id`\|`commerce_product_id`, `capability=course.view` (+ internal `resource`/`revoke`/`kind`) | Bearer + `X-Voronka-*` (+ `X-Lms-*` aliases) |
| lms_end → voronka completion | accept `course_id`/`commerce_product_id`/`resource`, email/`voronka_profile_id`/`keycloak_sub` | Bearer **или** `X-Voronka-*` / `X-Lms-*` |

## Фискализация / live

`ROBOKASSA_LIVE_READY` остаётся `0`, пока не закрыт чек-лист в [OPERATIONS.md](./OPERATIONS.md) и нет письменного решения владельца. **Не** включать из деплоя/кода «для удобства».

Адаптер **передаёт** параметр `Receipt` (номенклатура 54-FZ) в `POST /orders/{id}/robokassa`: JSON в запросе и в контрольной подписи `MerchantLogin:OutSum:InvId:Receipt:Пароль#1` (Receipt однократно URL-encoded в подписи). В GET checkout URL Receipt **дважды** URL-encoded на проводе (`%257B…`), чтобы после одного decode совпадал с подписью. Env (имена, без секретов): `ROBOKASSA_RECEIPT_TAX` (обязателен для честной ставки; default `none`), опционально `ROBOKASSA_RECEIPT_SNO`, `ROBOKASSA_RECEIPT_PAYMENT_METHOD`, `ROBOKASSA_RECEIPT_PAYMENT_OBJECT`. Модуль: `app/robokassa_receipt.py`.

## AI

Провайдеры (env → `/api/crm/ai/status` → Ops **AiSection**): `raga` (`RAG_API_*` + `RAG_PROJECT_MODELS`), `openrouter` (`OPENROUTER_*`), `ollama` (`OLLAMA_*`), `openai_compatible` (`OPENAI_COMPAT_BASE_URL` / `OPENAI_COMPAT_MODEL` / optional `OPENAI_COMPAT_API_KEY`). Правила: global → project → connection. Черновик всегда `sent: false`; `auto_send` отсутствует. Узлы `llm_reply` в графе — draft-first (`draft_only` / `human_approval`).

**Company setup:** `GET/POST /api/crm/projects/{pid}/company-setup` — project AiRule + checklist согласий / Signal welcome·inquiry·`course.access_granted` / AccessGrant path. Не включает production mass-send.

## Media / campaigns

Campaign payload может включать `asset_id` (`video_note`/`video`). Preflight блокирует чужой `file_id` без локального файла. MAX/VK: capability matrix — native video_note **unavailable**.

**Media SoR Wave B:** Signal Media Registry = originals SoR. Voronka bank is a **delivery cache** bridged by `signal_asset_id` ↔ Voronka `assets.id`. CRM soft-refs store Registry `asset_id` only (`POST …/persons/{id}/media-refs`). Fetch: Registry presign (`MEDIA_REGISTRY_*`) or Signal internal content (`SIGNAL_INTERNAL_MEDIA_*`). `POST …/assets/ensure-from-signal` + campaign preflight/launch rewrite missing bank ids. **Never** a second CRM media library; Telegram `file_id` stays private inside Voronka. Cutover flag `MEDIA_WRITE_ORIGINALS_TO_SIGNAL` lives on Signal — Voronka must not flip it.

**B-02 CRM embedded медиатека:** staff chrome lists/creates Signal assets via BFF `GET|POST /api/crm/projects/{pid}/signal-media*` (crm_session → `SIGNAL_INTERNAL_MEDIA_*` → Signal ` /internal/media/project-assets|create|…`). UI section `#media` / nav «Медиатека»; constructor CTA «Из медиатеки». Optional `SIGNAL_MEDIA_DEFAULT_BRAND_ID` when multiple Signal brands share a Project. **CRM-R2:** CMS chain cart is no longer a staff path — constructor «+» uses meta `node_kinds` in CRM.

## Outreach

`GET /api/crm/outreach-batches/{id}/export` — тексты+ссылки для ручной SIM. `OUTREACH_MAX_ENABLED=0` по умолчанию.

## СИГНАЛ 2.0 — визуальная студия рассылок (план интеграции)

**Техническое задание:** [СИГНАЛ 2.0 — единый визуальный коммуникационный кабинет](https://github.com/voodoo2serg/solo_pr/blob/main/docs/SIGNAL_TZ_INTEGRATION_v2.md) (репозиторий `solo_pr`, `docs/SIGNAL_TZ_INTEGRATION_v2.md`).

СИГНАЛ отвечает за адаптивный визуальный редактор сообщений, изображения и видео, фирменные шаблоны, предпросмотр email/Telegram/MAX/VK, версии и согласование. **Voronka остаётся владельцем CRM, контактов, `Identity`, `Connection`, медиабанка ботов, кампаний, `Outbox` и фактической доставки в мессенджеры.** Email-кампании и списки планируется вести в listmonk; Mailu или настроенный SMTP используется только для доставки почты. Solo-PR сохраняет обработку первичных регистраций/согласий и существующий signed outbox.

Интеграция должна выполняться через ограниченные M2M API, без общей БД, без копирования клиентских профилей и без второго отправителя Telegram/MAX/VK. До реального запуска обязательны проверки согласий, совместимости медиа, `preflight`, повторного запуска и доступов.

**SIG-4 (реализовано):** scoped HMAC API `/api/crm/m2m/*` — см. `docs/M2M_COMMUNICATIONS_OPENAPI.yaml`. Env: `SIGNAL_M2M_SECRET`, `SIGNAL_M2M_PROJECT_ALLOWLIST`, `SIGNAL_M2M_SCOPES`. Staff `crm_session` не используется.

**Wave C (2026-11-15):** Person graph + receivables ledger in Voronka — см. [WAVE_C_CUSTOMER_GRAPH.md](./WAVE_C_CUSTOMER_GRAPH.md). M2M HMAC transitional (owner F); additive `person:read` / `timeline:read`. Money SoR remains Voronka (`OrderLine`/`Receivable`/`PaymentAllocation`/`Refund`).

**CJ-108:** Signal CE writeback — `timeline:write` + `POST /api/crm/m2m/projects/{pid}/communications/ingest` (idempotent Communication/DeliveryEvent/Interaction; no HTML bodies).
