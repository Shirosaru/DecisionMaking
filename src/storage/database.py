from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DB_PATH = Path("data/bioventure.json")


def _load(db_path: Path) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists() or db_path.stat().st_size == 0:
        return {"projects": {}, "model_runs": [], "_next_id": 1}
    with db_path.open("r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"projects": {}, "model_runs": [], "_next_id": 1}


def _save(data: dict[str, Any], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with db_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def initialise_db(db_path: Path = DB_PATH) -> None:
    _load(db_path)  # creates file with empty structure if missing
    _save(_load(db_path), db_path)
