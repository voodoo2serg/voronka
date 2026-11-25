# Прогрев (outreach) — deprecated product (CRM-R8)

**Дата:** 2026-11-09 (MVP) · **Deprecated as CRM product:** 2026-11-25 (CRM-R8).

Staff UI (nav «Прогрев», кнопка «Прогрев (dry_run)», секция партий) **удалены**. Warm-up — обычный commercial Project с именем «Прогрев» (seed idempotent, `is_system=false`) + конструктор / рассылки проекта. Это **не** специальный модуль.

Ниже — legacy API / модель для ops и тестов; не staff workflow.

## Бывшая продуктовая воронка (архив)

```text
Profile.phone (импорт / старые покупатели)
  → OutreachBatch (dry_run / manual)
  → текст приглашения + Entry deep link на официальный MAX или Telegram бот
  → пользователь жмёт Start
  → Identity + ensure_profile + CRM Inbox / воронка
```

Прогрев **не** является peer официального Bot API и **не** идёт через `Campaign` / `available_identity` / `Outbox`.

## Модель (legacy)

| Сущность | Назначение |
|---|---|
| `OutreachBatch` | Партия: проект, Entry официального бота, daily_cap (1–50), mode (`dry_run`/`live`), transport (`manual`/`pymax`), state |
| `OutreachRecipient` | Получатель: phone snapshot, invite_text, bot_link, state (`pending`→`prepared`/`invited`/`started`/…) |

Инварианты:

1. Импортированный телефон ≠ право писать ботом; Campaign по-прежнему требует Identity.
2. `Profile.stopped` и исход «stopped» исключают получателя из prepare.
3. Prepare **не** создаёт строки Outbox.
4. `entry_connection_id` — только `telegram`/`max` с `role=bot` (не outreach-аккаунт).

## API (legacy, не CRM product)

- `POST /api/crm/projects/{pid}/outreach-batches` — черновик + получатели
- `GET /api/crm/projects/{pid}/outreach-batches`
- `GET /api/crm/outreach-batches/{id}`
- `POST /api/crm/outreach-batches/{id}/prepare` — dry_run: тексты и ссылки, Activity/Event
- `POST /api/crm/outreach-batches/{id}/recipients/{rid}/outcome` — ручная отметка invited/started/skipped/failed/stopped
- `POST /api/crm/outreach-batches/{id}/control` — pause/resume/cancel
- `GET /api/crm/outreach-batches/{id}/export` — тексты+ссылки для ручной SIM

## Transport

- Модуль `app/outreach_transport.py`: `DryRunTransport` по умолчанию; `PyMaxTransport` отказывается без `OUTREACH_MAX_ENABLED=1`.
- `OUTREACH_MAX_ENABLED=0` по умолчанию.
