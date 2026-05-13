#!/usr/bin/env python3
"""
Focused slide-collection runner.

Downloads PDFs, PPTXs and HTMs from:
  • 150+ VC firm / biotech IR / conference seed URLs  (VCWebsiteCollector)
  • SEC EDGAR 8-K EX-99 investor presentations        (SlideDownloader)

Saves:
  data/slides/vc/           — VC firm content
  data/slides/startup/      — biotech company IR decks
  data/slides/conference/   — conference archives
  data/slides/edgar/        — EDGAR exhibits (already 183 on disk)

Extracted clinical records → data/bioventure.json

Usage:
  python3 run_slide_collection.py              # all sources
  python3 run_slide_collection.py vc           # VC/startup/conference only
  python3 run_slide_collection.py edgar        # EDGAR only
  python3 run_slide_collection.py --max 50     # limit per source
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("slides")

DB_PATH     = Path("data/bioventure.json")
SLIDES_ROOT = Path("data/slides")

# ── ensure output directories exist ──────────────────────────────────────────
for sub in ("vc", "startup", "conference", "edgar"):
    (SLIDES_ROOT / sub).mkdir(parents=True, exist_ok=True)


def _count_slides() -> dict[str, int]:
    counts: dict[str, int] = {}
    for sub in ("edgar", "vc", "startup", "conference"):
        d = SLIDES_ROOT / sub
        counts[sub] = sum(1 for f in d.rglob("*") if f.is_file()) if d.exists() else 0
    return counts


def run_vc_slides(max_records: int = 500) -> int:
    from src.collectors.vc_website_collector import VCWebsiteCollector
    from src.storage.repository import bulk_upsert

    before = _count_slides()
    logger.info("Starting VCWebsiteCollector  (max_records=%d) …", max_records)
    t0 = time.monotonic()

    collector = VCWebsiteCollector(base_slides_dir=SLIDES_ROOT)
    records   = collector.collect(max_records=max_records)
    inserted  = bulk_upsert(records, db_path=DB_PATH)

    after  = _count_slides()
    elapsed = time.monotonic() - t0
    new_files = {k: after[k] - before[k] for k in after}

    logger.info(
        "VCWebsite done — %d records collected, %d new in DB  (%.0fs)",
        len(records), inserted, elapsed,
    )
    logger.info(
        "New files  vc:+%d  startup:+%d  conference:+%d",
        new_files["vc"], new_files["startup"], new_files["conference"],
    )
    return inserted


def run_edgar_slides(max_records: int = 200) -> int:
    from src.collectors.slide_downloader import SlideDownloader
    from src.storage.repository import bulk_upsert

    before_n = sum(1 for f in (SLIDES_ROOT / "edgar").rglob("*") if f.is_file())
    logger.info("Starting SlideDownloader  (max_records=%d) …", max_records)
    t0 = time.monotonic()

    collector = SlideDownloader(slides_dir=SLIDES_ROOT / "edgar")
    records   = collector.collect(max_records=max_records)
    inserted  = bulk_upsert(records, db_path=DB_PATH)

    after_n = sum(1 for f in (SLIDES_ROOT / "edgar").rglob("*") if f.is_file())
    elapsed = time.monotonic() - t0
    logger.info(
        "EDGAR done — %d records, %d new in DB, %d new files  (%.0fs)",
        len(records), inserted, after_n - before_n, elapsed,
    )
    return inserted


def print_summary() -> None:
    counts = _count_slides()
    total  = sum(counts.values())
    print("\n" + "═" * 60)
    print("  SLIDE COLLECTION SUMMARY")
    print("═" * 60)
    for sub, n in counts.items():
        bar = "█" * min(n // 5, 40)
        print(f"  {sub:<12s}  {n:>5d}  {bar}")
    print(f"  {'TOTAL':<12s}  {total:>5d}")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    args = sys.argv[1:]

    # Parse --max N
    max_rec = 500
    if "--max" in args:
        idx = args.index("--max")
        max_rec = int(args[idx + 1])
        args = [a for i, a in enumerate(args) if i not in (idx, idx + 1)]

    mode = args[0] if args else "all"

    print_summary()

    if mode in ("all", "vc"):
        run_vc_slides(max_records=max_rec)

    if mode in ("all", "edgar"):
        run_edgar_slides(max_records=max_rec)

    print_summary()
