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
