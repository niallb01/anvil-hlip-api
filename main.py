from contextlib import asynccontextmanager

from fastapi import FastAPI

from routers.v1 import router as v1_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(v1_router, prefix="/api/v1")
