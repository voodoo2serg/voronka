"""CRM HTTP routers: auth/staff, contacts, commerce, campaigns, outreach, integrations."""
from fastapi import APIRouter

from app.crm_routers.auth_staff import router as auth_staff_router
from app.crm_routers.contacts import router as contacts_router
from app.crm_routers.commerce import router as commerce_router
from app.crm_routers.campaigns import router as campaigns_router
from app.crm_routers.outreach import router as outreach_router
from app.crm_routers.integrations import router as integrations_router
from app.crm_routers.connections_hub import router as connections_hub_router
from app.crm_routers.ai_config import router as ai_config_router


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/crm", tags=["CRM"])
    router.include_router(auth_staff_router)
    router.include_router(contacts_router)
    router.include_router(commerce_router)
    router.include_router(campaigns_router)
    router.include_router(outreach_router)
    router.include_router(integrations_router)
    router.include_router(connections_hub_router)
    router.include_router(ai_config_router)
    return router
