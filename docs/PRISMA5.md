# ПРИЗМА 5: безопасный приём контактного заявления

`POST /api/crm/integrations/solo_pr/diagnostics` — отдельный контракт с `PRISMA5_ENABLED=1`, новым `PRISMA_CRM_SECRET` и allowlist `PRISMA_PROJECT_IDS`.

Подпись v1 связывает timestamp/nonce/POST/path/body digest. Nonce хранится в durable inbox, один запрос нельзя использовать повторно. Для потерянного ACK отправитель повторяет то же event_id/body с новым nonce; конфликт тела даёт 409. Повторы completion/delete сериализуются PostgreSQL transaction advisory lock по diagnostic_id.

ContactPoint создаётся на **новом provisional Person** по source-native ссылке `solo_pr/prisma5:<UUID>`. Email `verified=false`; совпадение с чужим ContactPoint создаёт MergeReview, не перезаписывает имя и Consent владельца. Checkbox маркетинга — consent receipt в истории, не разрешение на рассылку без доказательства владения адресом. Аккаунт, OIDC, AccessGrant и enrollment не создаются.

По явно изменённому требованию владельца Interaction хранит полный неизменяемый отчёт: методологию, вопросы и вступление конкретной публикации CMS, открытые ответы, личную измеримую цель, значения, контакты и тексты/hash/версию/время согласий. Автоматического срока очистки нет. В inbox completion остаются hash и ссылки, без второй копии полной анкеты. Лимит подписанного intake — 512 KiB; новые снимки валидируются на соответствие person claim, consent и summary. Старые minimal events по-прежнему принимаются для совместимости retries.

`prisma5.snapshot` добавляет архив ранее сохранённой анкеты отдельным event_id, не меняя исходный completion body/hash. `prisma5.verified` отдельно записывает proof email/Telegram и ContactVerification. Email proof связывается только с email этого диагностического Person; Telegram proof — с разрешённым ботом и личным chat_id == user_id. Рекламный checkbox сохраняется как intent; без связи с исходной owner-сессией подтверждение адреса не превращает его в marketing permission. Прежние отозванные permissions остаются отозванными.

`app/prisma_telegram.py` вызывается из обычного Telegram webhook только после штатной проверки secret и normalize private update. Он передаёт одноразовый start challenge на сайт с request-bound HMAC; чужой @username не является доказательством. Существующий бот и его прежний project_id не перепривязываются. Дополнительные настройки CRM: `PRISMA_TELEGRAM_CONNECTION_ID`, `PRISMA_SITE_URL`, прежний выделенный `PRISMA_CRM_SECRET`.

`prisma5.deleted` создаёт durable `subject.delete / pending_review`, видимый в существующем `/api/crm/integrations/privacy-requests`. Это запрос на рассмотрение, не автоматическое стирание коммерческого ledger. Поздний completion после такого tombstone отклоняется. `tests/test_prisma5_diagnostics.py` проверяет email collision, consent preservation, replay/tampering, project allowlist и delete-before-completion.
