from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.config import get_settings
from backend.database import generate_random_dataset, import_demo_data

router = APIRouter(tags=["upload"])

_REQUIRED_CSV_FILES = (
    "nodes.csv",
    "edges.csv",
    "products.csv",
    "trucks.csv",
    "warehouse_stock.csv",
    "demand.csv",
    "settings.csv",
)


class GenerateRequest(BaseModel):
    n_factories: int | None = None
    n_warehouses: int | None = None
    n_stores: int | None = None
    n_trucks: int | None = None
    seed: int | None = None
    scale: Literal["small", "medium", "large"] | None = None


@router.post("/upload")
async def upload_dataset(
    request: Request,
    filename: str | None = None,
) -> dict[str, object]:
    settings = get_settings()
    archive_bytes = await request.body()
    if not archive_bytes:
        raise HTTPException(status_code=400, detail="Request body must contain a ZIP archive")

    resolved_filename = filename or request.headers.get("x-filename") or "dataset.zip"
    if not resolved_filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Filename must end with .zip")

    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            temp_parent = settings.database_path.parent / "_tmp"
            temp_parent.mkdir(parents=True, exist_ok=True)
            temp_dir = temp_parent / f"logiflow_upload_{uuid4().hex}"
            temp_dir.mkdir(parents=True, exist_ok=False)
            archive.extractall(temp_dir)
            data_dir = _resolve_uploaded_data_dir(temp_dir)
            imported_counts = import_demo_data(settings.database_path, data_dir)
    except zipfile.BadZipFile as error:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid ZIP archive") from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {
        "status": "ok",
        "source": "upload",
        "filename": resolved_filename,
        "imported": imported_counts,
    }


@router.post("/generate")
def generate_dataset(payload: GenerateRequest) -> dict[str, object]:
    settings = get_settings()
    try:
        return generate_random_dataset(
            database_path=settings.database_path,
            n_factories=payload.n_factories,
            n_warehouses=payload.n_warehouses,
            n_stores=payload.n_stores,
            n_trucks=payload.n_trucks,
            seed=payload.seed,
            scale=payload.scale,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/demo/load")
def load_local_demo_data() -> dict[str, object]:
    settings = get_settings()
    project_root = Path(__file__).resolve().parents[3]
    counts = import_demo_data(
        database_path=settings.database_path,
        data_dir=project_root / "demo_data",
    )
    return {
        "status": "ok",
        "source": "demo_data",
        "imported": counts,
    }


def _resolve_uploaded_data_dir(extracted_root: Path) -> Path:
    root_candidate = extracted_root
    if _contains_required_csvs(root_candidate):
        return root_candidate

    for candidate in extracted_root.rglob("*"):
        if candidate.is_dir() and _contains_required_csvs(candidate):
            return candidate

    required = ", ".join(_REQUIRED_CSV_FILES)
    raise FileNotFoundError(f"Uploaded ZIP must contain: {required}")


def _contains_required_csvs(directory: Path) -> bool:
    return all((directory / filename).exists() for filename in _REQUIRED_CSV_FILES)
