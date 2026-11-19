# OPS-104 — Event reconciliation (voronka)

**Issue:** [voronka#13](https://github.com/voodoo2serg/voronka/issues/13) · Alias PROD-004  
**Canon:** [solo_pr OPS_104_EVENT_RECONCILIATION.md](https://github.com/voodoo2serg/solo_pr/blob/main/docs/signal/OPS_104_EVENT_RECONCILIATION.md)

## Scope

Voronka is SoR for money and access. This pass extends the existing commerce bus (`crm_entitlement_jobs`, `crm_integration_outbox`, `crm_integration_inbox`) — **no second domain bus**.

| Scope | Detects | Mutate action |
|-------|---------|---------------|
| `payment` | Payment without job; failed/exhausted entitlement; missing/dead `deal.paid` | queue / reprocess job; requeue DLQ |
| `enrollment` | Paid `AccessGrant` without `enrollment.desired`; dead LMS/solo access rows | enqueue stable enrollment; requeue DLQ |
| `publish` | Dead `course.access_granted`; stuck scenario.publish inbox | requeue Signal notify; wi-ted remote_status → **Signal** |

**Never** changes Payment amount/status from SuccessURL or reconcile apply.

## Stable enrollment idempotency

`enrollment.desired:{grant_id}` (and `enrollment.revoked:{grant_id}`). Revision lives in payload only. Legacy `:rN` suffixes are still detected and requeued.

## APIs

| Method | Path | Auth |
|--------|------|------|
| GET | `/api/crm/projects/{pid}/reconcile/preview?scope=` | CRM owner |
| POST | `/api/crm/projects/{pid}/reconcile/apply` | CRM owner + `acknowledge_mutate` |
| GET | `/api/crm/projects/{pid}/integration-outbox/dlq` | CRM owner |
| GET | `/api/crm/m2m/projects/{pid}/integrations/status` | Signal M2M scope `integrations:status:read` |

Worker auto-retry remains capped at **12** attempts → `dead`. Manual apply resets attempts for a fresh capped window.

## Metrics

`queue_metrics` adds `integration_outbox_dead` and `entitlement_jobs_failed`.

## Migration

`0010_ops104_reconcile` — nullable `correlation_id` on outbox / inbox / entitlement_jobs.

## Acceptance (code)

- [x] Dry-run preview before mutate
- [x] Replay ×3 entitlement → one `enrollment.desired`
- [x] DLQ list with `correlation_id`; capped auto-retry
- [x] Apply does not create a second Payment
- [x] M2M aggregate for OPS-105 (no secrets)
- [ ] Live: stop LMS after sandbox pay → recover (needs DEPLOYED + LMS sandbox)
- [ ] Ledger == QA-E2E-001 projection (needs PAY-101 ready)
- [ ] wi-ted status → Signal without duplicate post (Signal/WIT side)

Leave GitHub #13 open until MERGED → DEPLOYED → smoke.
