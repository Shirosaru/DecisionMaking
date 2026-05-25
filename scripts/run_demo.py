#!/usr/bin/env python3
"""
BioVenture Decision Intelligence Platform — Full Demo Run

This script performs a complete end-to-end demonstration:
  1. DATA COLLECTION  — download slides + collect from all 5 sources
  2. DATA REPOSITORY  — show what's stored on disk
  3. ANALYSIS         — kill rates, phase transitions, cost analytics
  4. ML TRAINING      — supervised success predictor
  5. RL DEMO          — portfolio decision policy
  6. SUMMARY REPORT   — comprehensive stats + findings
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("demo")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

DB_PATH    = _ROOT / "data" / "bioventure.json"
SLIDES_DIR = _ROOT / "data" / "slides" / "edgar"

SEPARATOR = "\n" + "═" * 66

def section(title: str) -> None:
    print(SEPARATOR)
    print(f"  {title}")
    print("═" * 66)

# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1 — DATA COLLECTION
# ─────────────────────────────────────────────────────────────────────────────

def stage_collect(max_per_source: int = 80) -> None:
    section("STAGE 1 · DATA COLLECTION")

    from src.collectors.clinical_trials_collector import ClinicalTrialsCollector
    from src.collectors.sec_edgar_collector import SECEdgarCollector
    from src.collectors.vc_scraper import VCScraper
    from src.collectors.slide_extractor import SlideExtractor
    from src.collectors.slide_downloader import SlideDownloader
    from src.storage.repository import bulk_upsert

    sources = [
        ("ClinicalTrials.gov",            ClinicalTrialsCollector()),
        ("SEC EDGAR 8-K/10-K filings",    SECEdgarCollector()),
        ("PubMed clinical outcomes",      VCScraper()),
        ("GlobeNewsWire press releases",  SlideExtractor()),
        ("EDGAR slide downloads",         SlideDownloader(slides_dir=SLIDES_DIR)),
    ]

    total_new = 0
    summary_rows = []

    for name, collector in sources:
        print(f"\n  ▶ {name}")
        t0 = time.monotonic()
        try:
            records = collector.collect(max_records=max_per_source)
            inserted = bulk_upsert(records, db_path=DB_PATH)
            elapsed = time.monotonic() - t0
            total_new += inserted
            summary_rows.append((name, len(records), inserted, f"{elapsed:.1f}s"))
            print(f"    fetched={len(records)}  new_to_db={inserted}  ({elapsed:.1f}s)")
        except Exception as exc:
            logger.error("    FAILED: %s", exc)
            summary_rows.append((name, 0, 0, "FAILED"))

    print(f"\n  ── Collection summary ──")
    print(f"  {'Source':<35s} {'Fetched':>8} {'New':>6} {'Time':>8}")
    print(f"  {'-'*60}")
    for row in summary_rows:
        print(f"  {row[0]:<35s} {row[1]:>8} {row[2]:>6} {row[3]:>8}")
    print(f"\n  Total new records added: {total_new}")

    # Show slide file repository
    slide_files = list(SLIDES_DIR.glob("*"))
    print(f"\n  ── Slide repository: data/slides/edgar/ ──")
    print(f"  Files downloaded: {len(slide_files)}")
    total_bytes = sum(f.stat().st_size for f in slide_files)
    print(f"  Total size: {total_bytes/1024:.0f} KB")
    for f in sorted(slide_files)[:10]:
        kb = f.stat().st_size / 1024
        print(f"    {f.name:<55s} {kb:>6.0f} KB")
    if len(slide_files) > 10:
        print(f"    ... and {len(slide_files)-10} more files")


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2 — DATA REPOSITORY AUDIT
# ─────────────────────────────────────────────────────────────────────────────

def stage_repository() -> dict:
    section("STAGE 2 · DATA REPOSITORY AUDIT")

    data = json.loads(DB_PATH.read_text())
    projects = data.get("projects", {})
    total = len(projects)

    from collections import Counter
    sources   = Counter(v.get("source","?") for v in projects.values())
    stages    = Counter(v.get("clinical_stage","?") for v in projects.values())
    decisions = Counter(v.get("decision","?") for v in projects.values())
    outcomes  = Counter(v.get("outcome","?") for v in projects.values())
    indications = Counter(
        v.get("indication","unknown") for v in projects.values()
        if v.get("indication","unknown") != "unknown"
    )
    mechanisms = Counter(
        v.get("mechanism","unknown") for v in projects.values()
        if v.get("mechanism","unknown") != "unknown"
    )

    print(f"\n  Total records in DB:  {total}")

    print(f"\n  ── By source ──")
    for src, n in sources.most_common():
        bar = "█" * int(n/total*30)
        print(f"  {src:<30s} {n:>4}  {bar}")

    print(f"\n  ── By clinical stage ──")
    for stage, n in stages.most_common():
        bar = "█" * int(n/total*30)
        print(f"  {stage:<15s} {n:>4}  {bar}")

    print(f"\n  ── Decision split ──")
    for d, n in decisions.most_common():
        print(f"  {d:<12s} {n:>4}  ({n/total:.1%})")

    print(f"\n  ── Top indications ──")
    for ind, n in indications.most_common(8):
        print(f"  {ind:<30s} {n:>4}")

    print(f"\n  ── Top mechanisms ──")
    for mech, n in mechanisms.most_common(8):
        print(f"  {mech:<30s} {n:>4}")

    return {"total": total, "sources": dict(sources), "stages": dict(stages)}


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 3 — ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def stage_analyse() -> None:
    section("STAGE 3 · DECISION ANALYTICS")
    from src.analysis.analytics import print_report
    print_report(db_path=DB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 4 — ML TRAINING
# ─────────────────────────────────────────────────────────────────────────────

def stage_train() -> dict:
    section("STAGE 4 · ML MODEL TRAINING")
    from src.learning.decision_model import SuccessPredictor

    predictor = SuccessPredictor()
    result = predictor.train(db_path=DB_PATH)

    print(f"\n  Model: LR + GradientBoosting ensemble")
    print(f"  Training records: {result['n_train']}  |  Test records: {result['n_test']}")
    print(f"  AUC:      {result['auc']:.3f}")
    print(f"  Accuracy: {result['accuracy']:.3f}")

    print(f"\n  ── Feature importances (GradientBoosting) ──")
    if predictor.is_fitted and predictor.feature_names:
        importances = predictor.gb.feature_importances_
        pairs = sorted(zip(predictor.feature_names, importances), key=lambda x: -x[1])
        for feat, imp in pairs[:12]:
            bar = "█" * int(imp * 40)
            print(f"  {feat:<35s} {imp:.4f}  {bar}")

    # Sample predictions
    print(f"\n  ── Sample predictions on held-out data ──")
    from src.storage.repository import fetch_all
    from src.processors.feature_extractor import extract_features
    import random

    rows = fetch_all(db_path=DB_PATH)
    sample = random.sample([r for r in rows if r.get("outcome","unknown") != "unknown"], min(5, len(rows)))

    for r in sample:
        feats = extract_features(r)
        prob = predictor.predict(r)
        true_succ = r.get("outcome","") in ("approved","ongoing") and r.get("decision","") == "go"
        stage = r.get("clinical_stage","?")
        print(f"  P(success)={prob:.0%}  stage={stage:<10s}  true={'✓' if true_succ else '✗'}  {r.get('title','')[:45]}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 5 — RL DEMO
# ─────────────────────────────────────────────────────────────────────────────

def stage_rl() -> None:
    section("STAGE 5 · REINFORCEMENT LEARNING POLICY DEMO")
    from src.storage.repository import fetch_all
    from src.learning.rl_env import BioVentureEnv, TabularQAgent, run_greedy_episode
    from src.processors.feature_extractor import extract_features

    rows = fetch_all(db_path=DB_PATH)
    for row in rows:
        feats = extract_features(row)
        row["feat_prior"] = feats.get("prior_probability", 0.35)

    env   = BioVentureEnv.from_records(rows)
    agent = TabularQAgent(lr=0.1, gamma=0.95, epsilon=0.25)

    print(f"\n  Training Q-agent for 600 episodes over {len(rows)} records...")
    rewards = agent.train(env, episodes=600)

    avg_early = sum(rewards[:50]) / 50
    avg_late  = sum(rewards[-50:]) / 50
    pct       = (avg_late - avg_early) / (abs(avg_early) + 1) * 100
    print(f"  Reward first-50 avg: ${avg_early:>12,.0f}")
    print(f"  Reward last-50  avg: ${avg_late:>12,.0f}  ({pct:+.1f}% improvement)")

    print(f"\n  ── Greedy policy evaluation (8 episodes) ──")
    print(f"  {'Project':<48s} {'P':>4}  {'Action':<14s}  {'True':>5}  {'OK':>2}")
    print(f"  {'-'*80}")

    n_correct = 0
    for idx in range(8):
        summary = run_greedy_episode(env, agent, episode_idx=idx)
        true_succ = summary.get("true_success")
        hist = summary.get("history", [])
        final_action = hist[-1].get("action","?") if hist else "?"
        final_p = summary.get("final_probability", 0.0)
        proj = summary.get("hypothesis","")[:45]
        correct = (final_action == "kill" and not true_succ) or (final_action != "kill" and true_succ)
        n_correct += int(correct)
        print(f"  {proj:<48s} {final_p:>3.0%}  {final_action:<14s}  {'YES' if true_succ else 'NO ':>5}  {'✓' if correct else '✗':>2}")

    print(f"\n  Correct decisions: {n_correct}/8")

    # Show step trace of best episode
    print(f"\n  ── Step trace of episode 2 ──")
    s = run_greedy_episode(env, agent, episode_idx=2)
    print(f"  Project: {s.get('hypothesis','')[:60]}")
    print(f"  True outcome: {'SUCCESS' if s.get('true_success') else 'FAIL'}")
    for h in s.get("history",[]):
        print(f"    step{h['step']:2d}  {h['action']:<20s}  stage={h.get('stage','?'):<12s}  "
              f"P={h['probability']:.0%}  reward=${h['reward']:>12,.0f}")


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 6 — SUMMARY REPORT
# ─────────────────────────────────────────────────────────────────────────────

def stage_summary(repo_info: dict, train_result: dict) -> None:
    section("STAGE 6 · PLATFORM SUMMARY REPORT")

    slide_files = list(SLIDES_DIR.glob("*"))
    total_bytes = sum(f.stat().st_size for f in slide_files)

    print(f"""
  ┌─────────────────────────────────────────────────────────────┐
  │            BIOVENTURE DECISION INTELLIGENCE                  │
  │                    DEMO RESULTS                             │
  ├─────────────────────────────────────────────────────────────┤
  │  DATA COLLECTION                                            │
  │    Total DB records:    {repo_info['total']:>6}                          │
  │    Sources active:      {len(repo_info['sources']):>6}                          │
  │    Slide files on disk: {len(slide_files):>6}  ({total_bytes/1024:.0f} KB)             │
  │                                                             │
  │  ML MODEL                                                   │
  │    AUC:                 {train_result['auc']:.3f}                         │
  │    Accuracy:            {train_result['accuracy']:.3f}                         │
  │    Training set:        {train_result['n_train']:>6}  records                    │
  │                                                             │
  │  DATA SOURCES                                               │""")
    for src, n in sorted(repo_info["sources"].items(), key=lambda x: -x[1])[:6]:
        print(f"  │    {src:<28s}  {n:>4}  records              │")
    print(f"""  │                                                             │
  │  STAGE COVERAGE                                             │""")
    for stage, n in sorted(repo_info["stages"].items(), key=lambda x: -x[1]):
        print(f"  │    {stage:<15s}  {n:>4}                                  │")
    print(f"""  └─────────────────────────────────────────────────────────────┘

  Platform is live. Re-run `python3 run_demo.py` to refresh data.
  Slide repository: data/slides/edgar/ ({len(slide_files)} files)
""")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    collect = "--no-collect" not in sys.argv

    if collect:
        stage_collect(max_per_source=80)

    repo_info    = stage_repository()
    stage_analyse()
    train_result = stage_train()
    stage_rl()
    stage_summary(repo_info, train_result)


if __name__ == "__main__":
    main()
