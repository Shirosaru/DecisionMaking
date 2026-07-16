#!/usr/bin/env python3
"""
BioVenture Decision Intelligence Platform — Main Pipeline Runner

Usage:
  python3 run_pipeline.py            # full run (collect + analyse + train)
  python3 run_pipeline.py collect    # data collection only
  python3 run_pipeline.py analyse    # analysis on existing DB
  python3 run_pipeline.py train      # model training on existing DB
  python3 run_pipeline.py rl         # RL training demo on existing DB
    python3 run_pipeline.py company    # company discovery-path analysis
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pipeline")

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_SCRIPTS))

DB_PATH = _ROOT / "data" / "bioventure.json"


# ── Problem Statement ─────────────────────────────────────────────────────────

def print_problem_statement() -> None:
    ps = _ROOT / "PROBLEM_STATEMENT.md"
    if ps.exists():
        print("\n" + "=" * 62)
        print("  PROBLEM STATEMENT")
        print("=" * 62)
        # Print first 40 lines only to keep terminal readable
        lines = ps.read_text().splitlines()
        for line in lines[:40]:
            print(line)
        if len(lines) > 40:
            print(f"\n  ... ({len(lines) - 40} more lines — see PROBLEM_STATEMENT.md)")
        print()


# ── Stage 1: Collect ──────────────────────────────────────────────────────────

def run_collect(max_per_source: int = 100) -> int:
    from pathlib import Path
    from src.collectors.clinical_trials_collector import ClinicalTrialsCollector
    from src.collectors.sec_edgar_collector import SECEdgarCollector
    from src.collectors.vc_scraper import VCScraper
    from src.collectors.slide_extractor import SlideExtractor
    from src.collectors.slide_downloader import SlideDownloader
    from src.collectors.vc_portfolio_collector import VCPortfolioCollector
    from src.collectors.vc_website_collector import VCWebsiteCollector
    from src.collectors.vc_decision_tracker import VCDecisionTracker
    from src.collectors.fda_collector import FDACollector
    from src.collectors.chembl_collector import ChEMBLCollector
    from src.collectors.ema_collector import EMACollector
    from src.storage.repository import bulk_upsert

    total_new = 0

    # Per-source limits — ChEMBL + FDA + ClinicalTrials are largest real-data sources
    source_limits = {
        "ChEMBL drug pipeline":         8000,   # 58K drug-indication pairs, all pharma
        "EMA medicines register":       3000,   # EU approvals + withdrawals/refusals
        "FDA NDA/BLA approvals":        1500,
        "ClinicalTrials.gov":           3000,   # expanded: 8 disease areas + 20 pharma sponsors
        "VC Portfolio (30 firms)":       500,
        "VC decision history":           1000,   # every trial + exit for all portfolio companies
        "VC websites + slides":           300,
        "SEC EDGAR filings":             max_per_source,
        "PubMed clinical outcomes":      max_per_source,
        "GlobeNewsWire press releases":  max_per_source,
        "EDGAR slide downloads":         max_per_source,
    }

    collectors = [
        ("ChEMBL drug pipeline",         ChEMBLCollector()),
        ("EMA medicines register",       EMACollector()),
        ("FDA NDA/BLA approvals",        FDACollector()),
        ("ClinicalTrials.gov",           ClinicalTrialsCollector()),
        ("VC Portfolio (30 firms)",      VCPortfolioCollector()),
        ("VC decision history",          VCDecisionTracker()),
        ("VC websites + slides",          VCWebsiteCollector(base_slides_dir=_ROOT / "data" / "slides")),
        ("SEC EDGAR filings",            SECEdgarCollector()),
        ("PubMed clinical outcomes",     VCScraper()),
        ("GlobeNewsWire press releases", SlideExtractor()),
        ("EDGAR slide downloads",        SlideDownloader(slides_dir=_ROOT / "data" / "slides" / "edgar")),
    ]

    for name, collector in collectors:
        logger.info("── Collecting from: %s", name)
        try:
            limit = source_limits.get(name, max_per_source)
            records = collector.collect(max_records=limit)
            inserted = bulk_upsert(records, db_path=DB_PATH)
            total_new += inserted
            logger.info("   %d collected, %d new", len(records), inserted)
        except Exception as exc:
            logger.error("   Failed: %s", exc)

    return total_new


# ── Stage 2: Analyse ──────────────────────────────────────────────────────────

def run_analyse() -> None:
    from src.analysis.analytics import print_report
    print_report(db_path=DB_PATH)


# ── Stage 2b: Modality trend intelligence ────────────────────────────────────

def run_trend() -> None:
    from src.analysis.analytics import print_trend_report
    print_trend_report(db_path=DB_PATH, top_n=10)


# ── Stage 3: Train supervised model ──────────────────────────────────────────

def run_train() -> None:
    from src.learning.decision_model import SuccessPredictor

    predictor = SuccessPredictor()
    result = predictor.train(db_path=DB_PATH)
    logger.info("Model training result: %s", result)

    if predictor.is_fitted:
        print("\n── Top feature importances ──")
        for feat, imp in predictor.feature_importance()[:10]:
            bar = "█" * int(imp * 50)
            print(f"  {feat:<35s} {imp:.4f}  {bar}")


# ── Stage 4: RL training demo ─────────────────────────────────────────────────

def run_rl() -> None:
    import random
    from src.storage.repository import fetch_all
    from src.learning.rl_env import BioVentureEnv, TabularQAgent, run_greedy_episode
    from src.processors.feature_extractor import extract_features

    rows = fetch_all(db_path=DB_PATH)
    if len(rows) < 5:
        logger.warning("Need at least 5 records in DB for RL demo. Run 'collect' first.")
        return

    # Attach prior probability feature to rows for RL env
    for row in rows:
        feats = extract_features(row)
        row["feat_prior"] = feats.get("prior_probability", 0.35)

    # Use all rows (RL env samples from them randomly)
    env = BioVentureEnv.from_records(rows)
    agent = TabularQAgent(lr=0.1, gamma=0.95, epsilon=0.25)

    logger.info("Training Q-agent for 600 episodes over %d project records...", len(rows))
    rewards = agent.train(env, episodes=600)

    avg_early = sum(rewards[:50]) / 50
    avg_late = sum(rewards[-50:]) / 50
    pct_change = (avg_late - avg_early) / (abs(avg_early) + 1) * 100
    logger.info(
        "Avg reward (first 50 ep): %.2f → Avg reward (last 50 ep): %.2f  (%.1f%%)",
        avg_early, avg_late, pct_change,
    )

    print("\n── Greedy policy evaluation (5 episodes after training) ──")
    n_correct = 0
    for idx in range(5):
        summary = run_greedy_episode(env, agent, episode_idx=idx)
        true_succ = summary.get("true_success")
        final_action = summary.get("history", [{}])[-1].get("action", "?")
        final_p = summary.get("final_probability", 0)
        # A "correct" episode: kill if P<0.3 and true_fail, or continue/invest if P>0.3
        correct = (final_action == "kill" and not true_succ) or (final_action != "kill" and true_succ)
        n_correct += int(correct)
        print(
            f"  ep{idx+1}: {summary.get('hypothesis','')[:45]:<45s} "
            f"P={final_p:.0%} action={final_action:<12s} "
            f"success={'YES' if true_succ else 'NO ':3s} {'✓' if correct else '✗'}"
        )
    print(f"\n  Correct decisions: {n_correct}/5")


# ── Stage 5: Company discovery-path analysis ────────────────────────────────

def run_company_path() -> None:
    from run_company_path_analysis import load_latest_patent_companies, summarize_path
    from src.learning.decision_model import SuccessPredictor
    from src.storage.repository import fetch_all

    rows = fetch_all(db_path=DB_PATH)
    companies = load_latest_patent_companies(rows, limit=15)
    if not companies:
        logger.warning("No latest patent companies found in DB. Run collect first.")
        return

    model = SuccessPredictor()
    model.train(db_path=DB_PATH)
    analyses = []
    for company in companies:
        from run_company_path_analysis import find_company_records

        records = find_company_records(rows, company)
        if not records:
            continue
        analyses.append(summarize_path(company, records, model))

    from run_company_path_analysis import render_html_report
    report_html = render_html_report(analyses)
    report_path = _ROOT / "data" / "reports" / "company_path_report.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_html)
    print(f"Company path report written → {report_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main(mode: str = "full") -> None:
    print_problem_statement()

    if mode in ("full", "collect"):
        logger.info("═══ STAGE 1: Data Collection ═══")
        new_records = run_collect(max_per_source=150)
        logger.info("Collection complete. %d new records added to DB.", new_records)

    if mode in ("full", "analyse"):
        logger.info("═══ STAGE 2: Analysis ═══")
        run_analyse()

    if mode in ("full", "analyse", "trend"):
        logger.info("═══ STAGE 2b: Modality Trend Intelligence ═══")
        run_trend()

    if mode in ("full", "train"):
        logger.info("═══ STAGE 3: Model Training ═══")
        run_train()

    if mode in ("full", "rl"):
        logger.info("═══ STAGE 4: RL Demo ═══")
        run_rl()

    if mode in ("full", "company"):
        logger.info("═══ STAGE 5: Company Discovery-Path Analysis ═══")
        run_company_path()

    logger.info("Pipeline done.")


if __name__ == "__main__":
    mode_arg = sys.argv[1] if len(sys.argv) > 1 else "full"
    valid = {"full", "collect", "analyse", "trend", "train", "rl", "company"}
    if mode_arg not in valid:
        print(f"Unknown mode '{mode_arg}'. Valid: {', '.join(sorted(valid))}")
        sys.exit(1)
    main(mode_arg)
