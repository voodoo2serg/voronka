# SIGNAL platform v5.2 — задачи репозитория voronka

**Каноническая архитектура:** https://github.com/voodoo2serg/solo_pr/blob/main/docs/signal/PLATFORM_ACTUAL_ARCHITECTURE_v5_2.md

**Единый backlog / зависимости и приёмка:** https://github.com/voodoo2serg/solo_pr/blob/main/docs/signal/CONNECTIVITY_TICKETS_v5_2.md

**Статус:** задано к разработке; НЕ означает, что все пункты не реализованы — сначала провести source+CI сверку. Успешный commit/merge != DEPLOYED/E2E_PASS. Платформенный backlog имеет приоритет при противоречиях; этот файл — рабочий локальный срез.

## Роль сервиса

Person/ContactPoint, согласия/suppression, многоканальные касания/путь клиента, договоры и подтверждённые поступления, права на услуги.

## Тикеты

| ID | Приоритет | Работа | Приёмка |
|---|---|---|---|
| AUTH-102 | P0 | OIDC CRM + customer issuer/sub links | No email-only admin promotion |
| CRM-101 | P0 | Единый verified PersonLink и merge review | No IP/email auto-merge |
| CRM-102 | P0 | Consent/suppression SoR | Stop проверяется перед отправкой |
| MAIL-101 | P0 | Email subscriptions/unsubscribe/bounces | Все отправители читают один policy API |
| JOURNEY-101 | P0 | Исполнение versioned Journey graph | Consent gates, delays, retries, audit |
| JOURNEY-103 | P1 | Доказательная timeline всех касаний | Публикация→контакт→договор→оплата |
| PAY-101 | P0 | Robokassa + ЮKassa + ledger E2E | Verified callbacks, частичная оплата и возвраты |
| MEDIA-103 | P1 | Приватные медиа из Registry в сообщения | Scoped links, legacy file_id bridge |

## Исполнительский протокол

1. До реализации сверить с PR/commit/миграциями в main. Статус каждого тикета вести как `NOT_STARTED`, `CODE_READY`, `MERGED`, `DEPLOYED`, `E2E_PASS` со ссылкой на проверку.
2. Согласовать OpenAPI/event contract и migration plan; новых прямых SQL связей с соседними сервисами не создавать.
3. Не писать чувствительные данные в RAGA/Umami; asset-originals в Media Registry; consent and financial truth только в Voronka.
4. Сохранить независимое исполнение сервисов при временной недоступности Signal. Перед опасными миграциями backup+restore и rollback.
5. Обновить канонический status registry в `solo_pr/docs/signal/` вместе с изменением, а не через месяцы после деплоя.

## Производственный backlog после дедупликации (PROD / OPS)

**[Ревизия PROD-001…013 и три новых GitHub Issues](https://github.com/voodoo2serg/solo_pr/blob/main/docs/signal/PRODUCTION_BACKLOG_RECONCILIATION_v5_2.md)** — **источник приоритизации**. Не создавать дубликаты по PROD-002/003/006…012: это уточнения уже существующих `AUTH-*`, `PAY-101`, `OPS-102`, `JOURNEY-*`, `MEDIA-*`, `ANALYTICS-*`, `CRM-102`, `MAIL-*`, `RAGA-*`. Новый runtime backlog: [OPS-103 — Git/VPS drift](https://github.com/voodoo2serg/solo_pr/issues/16), [OPS-104 — event reconciliation](https://github.com/voodoo2serg/voronka/issues/13) (**CODE_READY** — см. [OPS_104_EVENT_RECONCILIATION.md](./OPS_104_EVENT_RECONCILIATION.md); Issue закрывать только после MERGED→DEPLOYED→smoke), [OPS-105 — diagnostic UI](https://github.com/voodoo2serg/signal/issues/13).

## Единая регистрация, восстановление и SSO — AUTH-201…210

**[Актуальное к исполнению ТЗ Keycloak v5.3](https://github.com/voodoo2serg/solo_pr/blob/main/docs/signal/UNIFIED_IDENTITY_AND_ACCESS_V5_3.md)**. Для данного репозитория применяются его AUTH-тикеты из общей матрицы. Единая регистрация и сброс пароля живут в Keycloak, а не копируются в сервисы. Локальные роли и legacy аккаунты связывать через проверенный `issuer + sub`, без выдачи owner по email. Восстановление доступа владельца `serg@vodopetov.ru` — [AUTH-202](https://github.com/voodoo2serg/solo_pr/issues/21), с подтверждением личности и без master-password. **Не считать готовым до E2E AUTH-210.**
