import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "engine"))

from contextlib import asynccontextmanager

from fastapi import FastAPI

from db.migrations import run_migrations
from routers.v1 import router as v1_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_migrations()
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(v1_router, prefix="/api/v1")
