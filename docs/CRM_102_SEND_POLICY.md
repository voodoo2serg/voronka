# CRM-102 / MAIL-101 — send-policy SoR

Voronka is the **only** marketing consent / suppression SoR. Mailu = SMTP transport. **No listmonk.**

## Endpoints

| Method | Path | Auth |
|--------|------|------|
| POST | `/api/crm/projects/{pid}/send-policy/check` | staff CRM |
| POST | `/api/crm/m2m/projects/{pid}/send-policy/check` | Signal HMAC (`person:read`) |

### Body

```json
{
  "person_id": "…",
  "contact_point_id": null,
  "channel": "email",
  "purpose": "marketing",
  "connection_id": null,
  "missing_policy": "deny"
}
```

### Response

```json
{
  "allowed": false,
  "reason": "consent_revoked",
  "channel": "email",
  "purpose": "marketing",
  "sor": "voronka",
  "listmonk": false
}
```

## Rules

- `purpose=transactional` is never blocked solely by marketing opt-out.  
- `missing_policy=deny` for marketing email send-time; messenger dialogs may use `soft_allow` via `marketing_allowed`.  
- Unsubscribe / bounce evidence → `Consent` rows (`set_consent`) with immutable evidence string (MAIL-101 path).  
- All senders (Signal, Voronka worker, future adapters) **must** call check at queue time and at send time.
