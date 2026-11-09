# Прогрев (outreach): телефонная база → Start официального бота

Дата: 2026-11-09. Статус: MVP dry_run в CRM; live PyMax — заглушка за флагом.

## Продуктовая воронка

```text
Profile.phone (импорт / старые покупатели)
  → OutreachBatch (dry_run / manual)
  → текст приглашения + Entry deep link на официальный MAX или Telegram бот
  → пользователь жмёт Start
  → Identity + ensure_profile + CRM Inbox / воронка
  → Deal / Order / Robokassa (без изменений)
```

Прогрев **не** является peer официального Bot API и **не** идёт через `Campaign` / `available_identity` / `Outbox`.

## Модель

| Сущность | Назначение |
|---|---|
| `OutreachBatch` | Партия: проект, Entry официального бота, daily_cap (1–50), mode (`dry_run`/`live`), transport (`manual`/`pymax`), state |
| `OutreachRecipient` | Получатель: phone snapshot, invite_text, bot_link, state (`pending`→`prepared`/`invited`/`started`/…) |

Инварианты:

1. Импортированный телефон ≠ право писать ботом; Campaign по-прежнему требует Identity.
2. `Profile.stopped` и исход «stopped» исключают получателя из prepare.
3. Prepare **не** создаёт строки Outbox.
4. `entry_connection_id` — только `telegram`/`max` с `role=bot` (не outreach-аккаунт).
5. Официальная доставка остаётся в `app/channels.py`.

## API (CRM)

- `POST /api/crm/projects/{pid}/outreach-batches` — черновик + получатели
- `GET /api/crm/projects/{pid}/outreach-batches`
- `GET /api/crm/outreach-batches/{id}`
- `POST /api/crm/outreach-batches/{id}/prepare` — dry_run: тексты и ссылки, Activity/Event
- `POST /api/crm/outreach-batches/{id}/recipients/{rid}/outcome` — ручная отметка invited/started/skipped/failed/stopped
- `POST /api/crm/outreach-batches/{id}/control` — pause/resume/cancel

После `/start` на официальном боте `runtime.consume` вызывает `mark_started_for_identity` (эвристика по `contact_id` + `entry_connection_id`).

## PyMax / неофициальный транспорт

- Модуль `app/outreach_transport.py`: `DryRunTransport` по умолчанию; `PyMaxTransport` отказывается без `OUTREACH_MAX_ENABLED=1` и всё равно требует отдельной конфигурации (сейчас `pymax_not_configured`).
- Live-отправка **не** нужна для merge и **не** подключена к worker ботов.
- Не добавлять PyMax в `channels.send`.

## Операционные риски

| Риск | Митигация |
|---|---|
| Бан личного номера / ToS | Отдельная SIM; только invite-to-bot; готовность потерять аккаунт |
| 152-ФЗ / антиспам | Единый `Profile.stopped`, Activity/Event с текстом и batch_id; телефон ≠ согласие |
| Хрупкость неофициального API | Feature-flag off; поломка PyMax не роняет bot delivery |
| Смешение с Campaign | Отдельные таблицы и роутер; тесты на отсутствие Outbox |

См. также раздел в [OPERATIONS.md](OPERATIONS.md).
