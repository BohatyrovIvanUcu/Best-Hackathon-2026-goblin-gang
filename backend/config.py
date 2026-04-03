from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(slots=True)
class Settings:
    app_name: str
    app_host: str
    app_port: int
    debug: bool
    database_path: Path
    cors_origins: list[str]


def get_settings() -> Settings:
    root_dir = Path(__file__).resolve().parent.parent
    _load_env_file(root_dir / ".env")

    app_name = os.getenv("APP_NAME", "LogiFlow Backend")
    app_host = os.getenv("APP_HOST", "127.0.0.1")
    app_port = int(os.getenv("APP_PORT", "8000"))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    database_path = Path(os.getenv("DATABASE_PATH", "data/logiflow.db"))
    if not database_path.is_absolute():
        database_path = root_dir / database_path

    cors_origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    cors_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]

    return Settings(
        app_name=app_name,
        app_host=app_host,
        app_port=app_port,
        debug=debug,
        database_path=database_path,
        cors_origins=cors_origins,
    )
