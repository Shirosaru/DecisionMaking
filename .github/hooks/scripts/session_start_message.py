#!/usr/bin/env python3
import json
import sys
from pathlib import Path

# Read and ignore stdin payload; keep behavior deterministic and fast.
_ = sys.stdin.read()

# ── live stats ───────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[3]

def _count_slides() -> dict[str, int]:
    slides = _REPO / "data" / "slides"
    counts: dict[str, int] = {}
    for sub in ("edgar", "vc", "conference", "startup"):
        d = slides / sub
        if d.exists():
            counts[sub] = sum(1 for f in d.rglob("*") if f.is_file())
        else:
            counts[sub] = 0
    return counts

def _count_decisions() -> int:
    log = _REPO / "data" / "logs" / "decision_log.jsonl"
    if not log.exists():
        return 0
    try:
        return sum(1 for line in log.read_text().splitlines() if line.strip())
    except Exception:
        return 0

try:
    slides = _count_slides()
    decisions = _count_decisions()
    slide_total = sum(slides.values())
    stats_line = (
        f"Slides on disk — EDGAR: {slides['edgar']}  |  VC: {slides['vc']}  |  "
        f"Conferences: {slides['conference']}  |  Startups: {slides['startup']}  "
        f"(total: {slide_total})   |   Decision-log entries: {decisions}"
    )
except Exception:
    stats_line = "(stats unavailable)"

BANNER = r"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║   ██████╗ ███████╗ ██████╗    ██████╗ ███████╗ ██████╗██╗███████╗██╗ ██████╗║
║   ██╔══██╗██╔════╝██╔════╝    ██╔══██╗██╔════╝██╔════╝██║██╔════╝██║██╔═══██║
║   ██████╔╝█████╗  ██║         ██║  ██║█████╗  ██║     ██║███████╗██║██║   ██║
║   ██╔══██╗██╔══╝  ██║         ██║  ██║██╔══╝  ██║     ██║╚════██║██║██║   ██║
║   ██████╔╝███████╗╚██████╗    ██████╔╝███████╗╚██████╗██║███████║██║╚██████╔╝
║   ╚═════╝ ╚══════╝ ╚═════╝    ╚═════╝ ╚══════╝ ╚═════╝╚═╝╚══════╝╚═╝ ╚═════╝
║                                                                              ║
║   M A C H I N E   L E A R N I N G   F R O M   T O P   B I O   F I R M S    ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  GOAL  Learn GO / NO-GO decision-making patterns from:                       ║
║        • Top-tier bio VC firms  (Flagship, Atlas, Third Rock, ARCH, RA …)   ║
║        • Biotech IR presentations  (EDGAR 8-K EX-99, conference slides)     ║
║        • Startup pitch decks  (JPM, Cowen, Jefferies, ASCO, ASH, ESMO …)   ║
║        • 10-30 yr drug-program histories  (go/no-go outcomes labelled)      ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  KEY COLLECTORS                                                              ║
║    src/collectors/slide_downloader.py   → SEC EDGAR 8-K EX-99 slides        ║
║    src/collectors/vc_website_collector.py → VC firm / biotech IR PDFs/PPTs  ║
║    src/collectors/vc_decision_tracker.py  → Programme-level go/no-go log    ║
║    src/collectors/clinical_trials_collector.py → ClinicalTrials.gov data    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  DATA ON DISK                                                                ║
"""

BANNER += f"║  {stats_line:<76}║\n"
BANNER += (
    "╠══════════════════════════════════════════════════════════════════════════════╣\n"
    "║  AGENT MODE  Autonomous · implement first · validate after · ask only for   ║\n"
    "║              hard blockers or irreversible choices.                          ║\n"
    "╚══════════════════════════════════════════════════════════════════════════════╝\n"
)

try:
    import subprocess, textwrap
    # ── pull best model metrics from bioventure.json ─────────────────────────
    import json as _json
    bv = _REPO / "data" / "bioventure.json"
    if not bv.exists():
        bv = Path("/home2/DecisionMaking/data/bioventure.json")
    best_auc, best_model, n_samples = "—", "—", "—"
    if bv.exists():
        db = _json.loads(bv.read_text())
        runs = db.get("model_runs", [])
        if runs:
            best = max(runs, key=lambda r: r.get("auc_roc") or r.get("metrics", {}).get("auc_roc", 0))
            auc = best.get("auc_roc") or best.get("metrics", {}).get("auc_roc", 0)
            ns  = best.get("n_train") or best.get("metrics", {}).get("n_samples", 0)
            best_auc = f"{auc:.3f}"
            best_model = best.get("model_name", "—")
            n_samples = f"{ns:,}"
        n_projects = len(db.get("projects", {}))
except Exception:
    best_auc = best_model = n_samples = "—"
    n_projects = 0

# ── human-readable console banner (stderr) ───────────────────────────────────
W = 62
def _bar(n, total, w=20):
    filled = int(w * n / max(total, 1))
    return "█" * filled + "░" * (w - filled)

lines = [
    "╔" + "═" * W + "╗",
    "║" + "  REC-DECISION  ·  Bio Decision-Making Engine  ".center(W) + "║",
    "╠" + "═" * W + "╣",
    "║" + "  SLIDE LIBRARY".ljust(W) + "║",
    f"║    EDGAR 8-K investor presentations : {slides['edgar']:>4}  {_bar(slides['edgar'],400)}  ║",
    f"║    VC firm PDFs / blogs             : {slides['vc']:>4}  {_bar(slides['vc'],100)}  ║",
    f"║    Startup IR / earnings            : {slides['startup']:>4}  {_bar(slides['startup'],100)}  ║",
    f"║    Conference slides                : {slides['conference']:>4}  {_bar(slides['conference'],50)}  ║",
    f"║    Total                            : {slide_total:>4}  {_bar(slide_total,600)}  ║",
    "╠" + "═" * W + "╣",
    "║" + "  MODEL  &  DATA".ljust(W) + "║",
    f"║    Projects in DB   : {n_projects:>7,}".ljust(W + 1) + "║",
    f"║    Decision-log     : {decisions:>7,} entries".ljust(W + 1) + "║",
    f"║    Best model       : {best_model}".ljust(W + 1) + "║",
    f"║    Best AUC-ROC     : {best_auc}   (n={n_samples})".ljust(W + 1) + "║",
    "╠" + "═" * W + "╣",
    "║" + "  GOAL: learn GO/NO-GO from top bio VC firms & EDGAR".ljust(W) + "║",
    "╚" + "═" * W + "╝",
]
print("\n".join(lines), file=sys.stderr)

# ── hook JSON (AI context) ────────────────────────────────────────────────────
out = {
    "continue": True,
    "systemMessage": BANNER,
}
print(json.dumps(out))
