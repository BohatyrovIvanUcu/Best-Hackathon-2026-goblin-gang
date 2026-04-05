from __future__ import annotations

import json
from pathlib import Path

from backend.config import get_settings
from backend.database import import_demo_data


def main() -> int:
    settings = get_settings()
    project_root = Path(__file__).resolve().parent.parent
    counts = import_demo_data(
        database_path=settings.database_path,
        data_dir=project_root / "demo_data",
    )
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
