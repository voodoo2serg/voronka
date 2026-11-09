"""Staff CRM API entrypoint. Routers live under app.crm_routers; this module keeps test patches stable."""
from app.crm_deps import Session  # noqa: F401 — patched by tests/test_crm.py
from app.crm_routers import build_router
from app.crm_routers.auth_staff import mail_code  # noqa: F401 — patched by recovery tests

router = build_router()
