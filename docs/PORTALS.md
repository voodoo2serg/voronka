# Порталы и host-routing (CRM + Voronka ops)

| Хост | Назначение | Upstream |
|------|------------|----------|
| `crm.solo-pr.ru` | CRM Workspace (`/crm/`) | voronka `:18080` |
| `voronka.solo-pr.ru` | Ops `/ops/` + Mini App `/` + hooks | voronka `:18080` |
| `cms.solo-pr.ru` | CMS (solo_pr) | solo_pr |
| `lms.solo-pr.ru` | LMS stub / OpenOlat | stub → LMS |

## Поведение CRM-хоста

- `Host: crm.solo-pr.ru` + `/` → **302** `/crm/` (nginx example + app middleware).
- Cookie `crm_session` — host-only, без `Domain=.solo-pr.ru`.
- Кросс-ссылки CMS · Voronka · LMS в chrome (`GET /api/crm/portals`).
- ResultURL Robokassa: `{CRM_PUBLIC_URL}/api/crm/robokassa/result`.

## Поведение Voronka-хоста (этап 3)

- `Host: voronka.solo-pr.ru` + `/` → **Mini App** `admin.html` (Telegram WebApp; sslip URL совместимы).
- Browser staff UI: **`/ops/`** (Next static export, stub login owner/editor).
- `/health` ok; `/hooks/{cid}` проксируется (меньший body limit в nginx).
- Язык chrome: Боты · Сценарии · Рассылки · Поддержка · AI (без «продажи и обучение»).
- Deep-link «Открыть CRM» → `crm.solo-pr.ru`.
- Cookie та же `crm_session` (zone = UI, не отдельный IdP).

## Эндпоинты CRM portal (этап 2)

| Method | Path | Назначение |
|--------|------|------------|
| GET | `/api/crm/portals` | Chrome links |
| GET | `/api/crm/projects/{pid}/payment-providers` | Robokassa status + YooKassa UI-каркас |
| GET | `/api/crm/projects/{pid}/mail-status` | Mailu webmail + SMTP flags |
| GET | `/api/crm/projects/{pid}/analytics?from=&to=` | Период: money / contacts / learning / comms |
| GET | `/api/crm/projects/{pid}/contacts?source=` | `telegram\|max\|site\|import\|purchase` |
| GET | `/api/crm/staff/cms-matrix` | Чеклист синхронизации с CMS SPO-23 |
| POST | `/api/crm/yookassa/webhook` | **501** stub |

## Эндпоинты Ops portal (этап 3)

| Method | Path | Назначение |
|--------|------|------------|
| GET | `/api/ops/portals` | Chrome links + IA labels |
| GET | `/api/ops/projects/{pid}/identities` | Messenger audience (Identity) |
| POST | `/api/ops/projects/{pid}/wizard/channel-dm` | Канал → wait → DM funnel |
| POST | `/api/ops/projects/{pid}/wizard/support` | Support menu pipeline |
| POST | `/api/crm/projects/{pid}/campaigns` | + `identity_ids` для мгновенных blasts |

Nginx: [deploy/nginx-crm.solo-pr.ru.conf](../deploy/nginx-crm.solo-pr.ru.conf) · [deploy/nginx-voronka.solo-pr.ru.conf](../deploy/nginx-voronka.solo-pr.ru.conf).
