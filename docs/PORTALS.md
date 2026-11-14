# Порталы и host-routing (CRM)

| Хост | Назначение | Upstream |
|------|------------|----------|
| `crm.solo-pr.ru` | CRM Workspace (`/crm/`) | voronka `:18080` |
| `voronka.solo-pr.ru` | Боты / Mini App / ops | voronka `:18080` |
| `cms.solo-pr.ru` | CMS (solo_pr) | solo_pr |
| `lms.solo-pr.ru` | LMS stub / OpenOlat | stub → LMS |

## Поведение CRM-хоста

- `Host: crm.solo-pr.ru` + `/` → **302** `/crm/` (nginx example + app middleware).
- Cookie `crm_session` — host-only, без `Domain=.solo-pr.ru`.
- Кросс-ссылки CMS · Voronka · LMS в chrome (`GET /api/crm/portals`).
- ResultURL Robokassa: `{CRM_PUBLIC_URL}/api/crm/robokassa/result`.

## Эндпоинты портала (этап 2)

| Method | Path | Назначение |
|--------|------|------------|
| GET | `/api/crm/portals` | Chrome links |
| GET | `/api/crm/projects/{pid}/payment-providers` | Robokassa status + YooKassa UI-каркас |
| GET | `/api/crm/projects/{pid}/mail-status` | Mailu webmail + SMTP flags |
| GET | `/api/crm/projects/{pid}/analytics?from=&to=` | Период: money / contacts / learning / comms |
| GET | `/api/crm/projects/{pid}/contacts?source=` | `telegram\|max\|site\|import\|purchase` |
| GET | `/api/crm/staff/cms-matrix` | Чеклист синхронизации с CMS SPO-23 |
| POST | `/api/crm/yookassa/webhook` | **501** stub |

Пример nginx: [deploy/nginx-crm.solo-pr.ru.conf](../deploy/nginx-crm.solo-pr.ru.conf).
