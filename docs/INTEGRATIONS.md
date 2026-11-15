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
```

Сбой job/outbox **не** удаляет Payment.

## Wire contract v1 (dual-read / dual-emit)

Межрепо HTTP без shared DB. Приём **и** старых, **и** новых имён; исходящие шлют оба варианта.

| Направление | Поля | Auth |
|---|---|---|
| solo_pr → voronka `lead.created` | `type`\|`kind`, `idempotency_key`\|`event_id`, nested `data` или flat | Bearer **или** HMAC `X-Voronka-*` / `X-Solo-*` (`sha256=<hex>`, base `{ts}.{body}`, skew ≤300s) |
| voronka → solo_pr `deal.paid` / `access.*` | emit `type` **и** `kind`, `event_id` **и** `idempotency_key`; product + optional `course_id`/`club`/`commerce_product_id`/`external_id` | `X-Voronka-*` **и** `X-Solo-*` + Bearer |
| voronka → lms_end enrollment | LMS `EnrollmentBody`: `schema_version`, `action` grant\|revoke, `keycloak_sub`/email/`voronka_profile_id`, `course_id`\|`commerce_product_id`, `capability=course.view` (+ internal `resource`/`revoke`/`kind`) | Bearer + `X-Voronka-*` (+ `X-Lms-*` aliases) |
| lms_end → voronka completion | accept `course_id`/`commerce_product_id`/`resource`, email/`voronka_profile_id`/`keycloak_sub` | Bearer **или** `X-Voronka-*` / `X-Lms-*` |

## Фискализация / live

`ROBOKASSA_LIVE_READY` остаётся `0`, пока не закрыт чек-лист в [OPERATIONS.md](./OPERATIONS.md). Адаптер **не** передаёт `Receipt`.

## AI

Провайдеры: RAGA / OpenRouter / Ollama. Правила: global → project → connection. Черновик всегда `sent: false`.

## Media / campaigns

Campaign payload может включать `asset_id` (`video_note`/`video`). Preflight блокирует чужой `file_id` без локального файла. MAX/VK: capability matrix — native video_note **unavailable**.

## Outreach

`GET /api/crm/outreach-batches/{id}/export` — тексты+ссылки для ручной SIM. `OUTREACH_MAX_ENABLED=0` по умолчанию.

## СИГНАЛ 2.0 — визуальная студия рассылок (план интеграции)

**Техническое задание:** [СИГНАЛ 2.0 — единый визуальный коммуникационный кабинет](https://github.com/voodoo2serg/solo_pr/blob/main/docs/SIGNAL_TZ_INTEGRATION_v2.md) (репозиторий `solo_pr`, `docs/SIGNAL_TZ_INTEGRATION_v2.md`).

СИГНАЛ отвечает за адаптивный визуальный редактор сообщений, изображения и видео, фирменные шаблоны, предпросмотр email/Telegram/MAX/VK, версии и согласование. **Voronka остаётся владельцем CRM, контактов, `Identity`, `Connection`, медиабанка ботов, кампаний, `Outbox` и фактической доставки в мессенджеры.** Email-кампании и списки планируется вести в listmonk; Mailu или настроенный SMTP используется только для доставки почты. Solo-PR сохраняет обработку первичных регистраций/согласий и существующий signed outbox.

Интеграция должна выполняться через ограниченные M2M API, без общей БД, без копирования клиентских профилей и без второго отправителя Telegram/MAX/VK. До реального запуска обязательны проверки согласий, совместимости медиа, `preflight`, повторного запуска и доступов.

**SIG-4 (реализовано):** scoped HMAC API `/api/crm/m2m/*` — см. `docs/M2M_COMMUNICATIONS_OPENAPI.yaml`. Env: `SIGNAL_M2M_SECRET`, `SIGNAL_M2M_PROJECT_ALLOWLIST`, `SIGNAL_M2M_SCOPES`. Staff `crm_session` не используется.

**Wave C (2026-11-15):** Person graph + receivables ledger in Voronka — см. [WAVE_C_CUSTOMER_GRAPH.md](./WAVE_C_CUSTOMER_GRAPH.md). M2M HMAC transitional (owner F); additive `person:read` / `timeline:read`. Money SoR remains Voronka (`OrderLine`/`Receivable`/`PaymentAllocation`/`Refund`).
