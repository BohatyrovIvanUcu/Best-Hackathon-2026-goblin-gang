from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.router import api_router
from backend.config import get_settings
from backend.database import initialize_database

settings = get_settings()
project_root = Path(__file__).resolve().parent.parent
frontend_dir = project_root / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database(settings.database_path)
    yield


app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.get("/", tags=["system"])
async def root() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")


@app.get("/app", tags=["system"])
async def app_shell() -> FileResponse:
    return FileResponse(frontend_dir / "index.html")
