from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from .database import DB_PATH, _load, _save, initialise_db
from ..collectors.base_collector import RawRecord

logger = logging.getLogger(__name__)

VALID_STAGES = {"preclinical", "ind_filing", "phase1", "phase2", "phase3", "nda_submitted", "approved", "discontinued", "unknown"}
VALID_DECISIONS = {"go", "no-go", "undecided", "acquired"}
VALID_OUTCOMES = {
    "approved", "ongoing", "unknown",
    "discontinued_preclinical",
    "discontinued_ind_filing",
    "discontinued_phase1", "discontinued_p1",
    "discontinued_phase2", "discontinued_p2",
    "discontinued_phase3", "discontinued_p3",
    "discontinued_nda_submitted",
    "discontinued",
}


def upsert_record(record: RawRecord, db_path: Path = DB_PATH) -> int | None:
    """Insert or ignore (on conflict) a RawRecord. Returns new id or None if duplicate."""
    initialise_db(db_path)
    data = _load(db_path)
    key = f"{record.source}::{record.source_id}"
    if key in data["projects"]:
        return None

    row_id = data["_next_id"]
    data["_next_id"] += 1
    data["projects"][key] = {
        "id": row_id,
        "source": record.source,
        "source_id": record.source_id,
        "url": record.url,
        "title": record.title,
        "indication": record.indication,
        "mechanism": record.mechanism,
        "clinical_stage": record.clinical_stage if record.clinical_stage in VALID_STAGES else "unknown",
        "decision": record.decision if record.decision in VALID_DECISIONS else "undecided",
        "outcome": record.outcome if record.outcome in VALID_OUTCOMES else "unknown",
        "investment_usd": record.investment_usd,
        "raw_text": record.raw_text,
        "extra": record.extra,
    }
    _save(data, db_path)
    return row_id


def bulk_upsert(records: list[RawRecord], db_path: Path = DB_PATH) -> int:
    initialise_db(db_path)
    data = _load(db_path)
    inserted = 0
    for rec in records:
        key = f"{rec.source}::{rec.source_id}"
        if key in data["projects"]:
            continue
        row_id = data["_next_id"]
        data["_next_id"] += 1
        data["projects"][key] = {
            "id": row_id,
            "source": rec.source,
            "source_id": rec.source_id,
            "url": rec.url,
            "title": rec.title,
            "indication": rec.indication,
            "mechanism": rec.mechanism,
            "clinical_stage": rec.clinical_stage if rec.clinical_stage in VALID_STAGES else "unknown",
            "decision": rec.decision if rec.decision in VALID_DECISIONS else "undecided",
            "outcome": rec.outcome if rec.outcome in VALID_OUTCOMES else "unknown",
            "investment_usd": rec.investment_usd,
            "raw_text": rec.raw_text,
            "extra": rec.extra,
        }
        inserted += 1
    _save(data, db_path)
    logger.info("Bulk upsert: %d / %d new records inserted", inserted, len(records))
    return inserted


def fetch_all(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    initialise_db(db_path)
    data = _load(db_path)
    return sorted(data["projects"].values(), key=lambda r: r["id"])


def count(db_path: Path = DB_PATH) -> int:
    initialise_db(db_path)
    return len(_load(db_path)["projects"])


def summary_by_stage(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    rows = fetch_all(db_path)
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"n": 0, "kills": 0, "invest_sum": 0.0})
    for r in rows:
        s = r["clinical_stage"]
        buckets[s]["n"] += 1
        if r["decision"] == "no-go":
            buckets[s]["kills"] += 1
        buckets[s]["invest_sum"] += r.get("investment_usd", 0) or 0
    result = []
    for stage, v in sorted(buckets.items(), key=lambda x: -x[1]["n"]):
        result.append({
            "clinical_stage": stage,
            "n": v["n"],
            "kills": v["kills"],
            "avg_investment": round(v["invest_sum"] / v["n"], 2) if v["n"] else 0,
        })
    return result


def save_model_run(name: str, auc: float, accuracy: float,
                   n_train: int, n_test: int, params: dict,
                   db_path: Path = DB_PATH) -> None:
    initialise_db(db_path)
    data = _load(db_path)
    data["model_runs"].append({
        "model_name": name, "auc_roc": auc, "accuracy": accuracy,
        "n_train": n_train, "n_test": n_test, "params": params,
    })
    _save(data, db_path)
