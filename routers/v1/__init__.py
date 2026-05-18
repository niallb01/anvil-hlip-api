from fastapi import APIRouter

from routers.v1.health import router as health_router

router = APIRouter()
router.include_router(health_router)
