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

## Фискализация / live

`ROBOKASSA_LIVE_READY` остаётся `0`, пока не закрыт чек-лист в [OPERATIONS.md](./OPERATIONS.md). Адаптер **не** передаёт `Receipt`.

## AI

Провайдеры: RAGA / OpenRouter / Ollama. Правила: global → project → connection. Черновик всегда `sent: false`.

## Media / campaigns

Campaign payload может включать `asset_id` (`video_note`/`video`). Preflight блокирует чужой `file_id` без локального файла. MAX/VK: capability matrix — native video_note **unavailable**.

## Outreach

`GET /api/crm/outreach-batches/{id}/export` — тексты+ссылки для ручной SIM. `OUTREACH_MAX_ENABLED=0` по умолчанию.
