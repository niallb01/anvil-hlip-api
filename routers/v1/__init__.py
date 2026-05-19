from fastapi import APIRouter

from routers.v1.health import router as health_router
from routers.v1.oauth import router as oauth_router
from routers.v1.webhook import router as webhook_router

router = APIRouter()
router.include_router(health_router)
router.include_router(oauth_router)
router.include_router(webhook_router)
