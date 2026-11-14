"""Voronka ops portal HTTP routers (voronka.solo-pr.ru /ops/)."""
from fastapi import APIRouter

from app.ops_routers.portal import router as portal_router
from app.ops_routers.wizards import router as wizards_router


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/ops", tags=["Ops"])
    router.include_router(portal_router)
    router.include_router(wizards_router)
    return router
