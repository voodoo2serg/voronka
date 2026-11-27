# ПРИЗМА 5: безопасный приём контактного заявления

`POST /api/crm/integrations/solo_pr/diagnostics` — отдельный контракт с `PRISMA5_ENABLED=1`, новым `PRISMA_CRM_SECRET` и allowlist `PRISMA_PROJECT_IDS`.

Подпись v1 связывает timestamp/nonce/POST/path/body digest. Nonce хранится в durable inbox, один запрос нельзя использовать повторно. Для потерянного ACK отправитель повторяет то же event_id/body с новым nonce; конфликт тела даёт 409. Повторы completion/delete сериализуются PostgreSQL transaction advisory lock по diagnostic_id.

ContactPoint создаётся на **новом provisional Person** по source-native ссылке `solo_pr/prisma5:<UUID>`. Email `verified=false`; совпадение с чужим ContactPoint создаёт MergeReview, не перезаписывает имя и Consent владельца. Checkbox маркетинга — consent receipt в истории, не разрешение на рассылку без доказательства владения адресом. Аккаунт, OIDC, AccessGrant и enrollment не создаются.

В Interaction — методология, cohort, totals/effort/priorities и evidence согласий. Открытые ответы и значения личной цели запрещены строгой входной схемой. Telegram остаётся непроверенным контактным заявлением. В inbox completion — hash и ссылки, а не дубликат полной анкеты.

`prisma5.deleted` создаёт durable `subject.delete / pending_review`, видимый в существующем `/api/crm/integrations/privacy-requests`. Это запрос на рассмотрение, не автоматическое стирание коммерческого ledger. Поздний completion после такого tombstone отклоняется. `tests/test_prisma5_diagnostics.py` проверяет email collision, consent preservation, replay/tampering, project allowlist и delete-before-completion.
