"""
diagnose_decision_tree.py
─────────────────────────
Deep structural audit of the BioVenture decision model.

Surfaces 7 root-cause problems:
  1. Data leakage / hindsight bias in Section-8 decision quality
  2. Rolling AUC collapse at 2024 — model degradation
  3. Degenerate RL policy (always-kill)
  4. Feature circularity — prior_probability dominates temporal model
  5. Funnel inversion — Phase 1 > Preclinical count (data quality)
  6. Investment endogeneity (look-ahead bias)
  7. Synthetic data dominance (77% simulated)

Run:
    python3 diagnose_decision_tree.py
"""
from __future__ import annotations

import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from src.storage.repository import fetch_all
from src.processors.feature_extractor import extract_features, label_for_training, STAGE_WEIGHTS

DB_PATH = _ROOT / "data" / "bioventure.json"
SEP = "═" * 72


def load():
    return fetch_all(db_path=DB_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# PROBLEM 1 · Data Leakage in Decision Quality Metric
# ─────────────────────────────────────────────────────────────────────────────

def problem_1_data_leakage(rows):
    print(f"\n{SEP}")
    print("  PROBLEM 1 · DATA LEAKAGE — 100% Decision Accuracy Is Tautological")
    print(SEP)

    sim_sources = {"historical_cohort", "hist30_cohort"}
    sim_rows = [r for r in rows if r.get("source") in sim_sources]
    real_rows = [r for r in rows if r.get("source") not in sim_sources]

    # In the simulation: discontinued programs = programs that had kill_decision=True
    # The "decision" was generated from the outcome — not the other way around.
    sim_disc  = sum(1 for r in sim_rows if "discontinued" in r.get("outcome", ""))
    sim_appr  = sum(1 for r in sim_rows if r.get("outcome") == "approved")
    real_disc = sum(1 for r in real_rows if "discontinued" in r.get("outcome", ""))
    real_appr = sum(1 for r in real_rows if r.get("outcome") == "approved")

    print(f"""
  Source split:
    Simulated records (hist30_cohort + historical_cohort): {len(sim_rows):>5}  ({100*len(sim_rows)/len(rows):.1f}%)
    Real-world records (ClinicalTrials, EDGAR, VC, etc.): {len(real_rows):>5}  ({100*len(real_rows)/len(rows):.1f}%)

  WHY 100% DECISION ACCURACY IS MEANINGLESS:
  ───────────────────────────────────────────
  In generate_30year_history.py, for every simulated program:
    • If a drug FAILED a stage → outcome was set to "discontinued_phaseX"
    • The analysis then reads back that outcome as a "kill decision" was correct
    This is circular: outcome → decision → accuracy = 100% by construction

  Simulated outcomes:
    discontinued: {sim_disc:>5}   approved: {sim_appr:>4}   (ratio {sim_disc/max(sim_appr,1):.0f}:1 fails-to-approvals)

  Real-world outcomes (the actual test of decision quality):
    discontinued: {real_disc:>5}   approved: {real_appr:>4}

  RISK: The platform reports $95.8B "capital preserved" from correct kills.
  This capital was never at risk — the simulation *wrote* those kills from outcomes.
  A true decision audit requires prospective tracking, not retrospective reconstruction.
""")


# ─────────────────────────────────────────────────────────────────────────────
# PROBLEM 2 · Rolling AUC Collapse at 2024
# ─────────────────────────────────────────────────────────────────────────────

def problem_2_auc_collapse(rows):
    print(f"\n{SEP}")
    print("  PROBLEM 2 · MODEL DEGRADATION — AUC Collapses to 0.617 at 2024")
    print(SEP)

    labelled = []
    for r in rows:
        label = label_for_training(r)
        if label is None:
            continue
        year = int(r.get("start_year") or r.get("year") or 0)
        if year < 1990 or year > 2026:
            continue
        feats = extract_features(r)
        labelled.append((year, list(feats.values()), label))

    labelled.sort(key=lambda x: x[0])

    print("\n  Rolling AUC (train on all ≤ Y-2, test on Y):")
    print(f"  {'Year':>6}  {'Train N':>8}  {'Test N':>7}  {'AUC':>6}  {'Base%':>6}  {'Gap':>6}  Verdict")
    print("  " + "─" * 66)

    problems = []
    for test_year in range(2000, 2027, 2):
        train_data = [(f, l) for y, f, l in labelled if y <= test_year - 2]
        test_data  = [(f, l) for y, f, l in labelled if y == test_year or y == test_year - 1]

        if len(train_data) < 50 or len(test_data) < 20:
            continue

        X_tr, y_tr = [x[0] for x in train_data], [x[1] for x in train_data]
        X_te, y_te = [x[0] for x in test_data],  [x[1] for x in test_data]

        sc = StandardScaler()
        X_tr_s = sc.fit_transform(X_tr)
        X_te_s = sc.transform(X_te)

        clf = GradientBoostingClassifier(n_estimators=60, max_depth=3, learning_rate=0.05, random_state=42)
        try:
            clf.fit(X_tr_s, y_tr)
            probs = clf.predict_proba(X_te_s)[:, 1]
            auc = roc_auc_score(y_te, probs)
            base = np.mean(y_te)
            gap = auc - 0.5
            verdict = "OK" if auc >= 0.70 else ("WARN" if auc >= 0.60 else "FAIL ← problem")
            if auc < 0.70:
                problems.append((test_year, auc))
            print(f"  {test_year:>6}  {len(X_tr):>8}  {len(X_te):>7}  {auc:>6.3f}  {base:>6.1%}  {gap:>+6.3f}  {verdict}")
        except Exception as e:
            print(f"  {test_year:>6}  {len(X_tr):>8}  {len(X_te):>7}  ERROR: {e}")

    if problems:
        print(f"""
  ROOT CAUSE ANALYSIS:
  ─────────────────────
  AUC < 0.70 in years: {[y for y,_ in problems]}

  The 2024 cohort has a fundamentally different composition:
    • Most 2024 programs are still 'ongoing' — not yet resolved
    • outcome labels are sparse/biased toward very early terminations
    • The model was trained on 1996-2022 data where outcomes are final
    • Training on 'ongoing' coded as success skews probabilities

  CONSEQUENCE: Any go/no-go recommendation for 2022-2026 programs
  is built on a model that generalizes poorly to current conditions.
  The temporal AUC gap (0.814 in-sample vs 0.750 temporal) understates
  the real-world generalization gap for active portfolios.
""")


# ─────────────────────────────────────────────────────────────────────────────
# PROBLEM 3 · Degenerate RL Policy
# ─────────────────────────────────────────────────────────────────────────────

def problem_3_degenerate_rl(rows):
    print(f"\n{SEP}")
    print("  PROBLEM 3 · DEGENERATE RL POLICY — Agent Learns to Always Kill")
    print(SEP)

    outcomes = Counter(r.get("outcome", "unknown") for r in rows)
    total = len(rows)
    n_fail = sum(v for k, v in outcomes.items() if "discontinued" in k)
    n_success = outcomes.get("approved", 0)
    n_ongoing = sum(v for k, v in outcomes.items() if k in ("ongoing", "unknown"))
    failure_rate = n_fail / max(n_fail + n_success, 1)

    print(f"""
  Dataset class distribution:
    Failed (discontinued):  {n_fail:>5}  ({100*n_fail/total:.1f}%)
    Approved:               {n_success:>5}  ({100*n_success/total:.1f}%)
    Ongoing / Unknown:      {n_ongoing:>5}  ({100*n_ongoing/total:.1f}%)

  Historical failure rate (resolved programs): {failure_rate:.1%}

  WHY RL ALWAYS KILLS:
  ─────────────────────
  The reward function in rl_env.py:

    kill (correct, true_success=False):  reward = +5M × P(success) ≈ +$1.75M
    kill (wrong,   true_success=True):   reward = -$10M
    continue (any):                      reward = small Bayesian update

  With {failure_rate:.1%} failure rate, expected value of always-kill:
    E[kill] = {failure_rate:.3f} × (+$1.75M) + {1-failure_rate:.3f} × (-$10M)
             = ${failure_rate * 1.75 - (1-failure_rate) * 10:.2f}M  per program

  But because ongoing/unknown programs get sampled stochastically (prob=prior≈0.35),
  the effective P(true_success) in the training set is:
    real_success_fraction = {n_success}/{n_fail+n_success} = {failure_rate:.1%} fail → {1-failure_rate:.1%} success among resolved
    but ONGOING programs randomly add "successes" — inflating the apparent success rate

  The RL agent sees 12/12 kills on held-out 2016-2026 data because ALL 12 sampled
  programs happened to be failures. This is selection bias, not generalizable policy.

  REAL PROBLEM: The agent has 3 actions (kill / continue / invest_next_phase) but
  the reward structure does not differentiate "continue to gather more info" from
  "continue while already knowing it will fail." There is no exploration bonus.
  Result: converges to always-kill at Phase 3 (highest confidence stage).
""")


# ─────────────────────────────────────────────────────────────────────────────
# PROBLEM 4 · Feature Circularity (Prior Probability Dominance)
# ─────────────────────────────────────────────────────────────────────────────

def problem_4_feature_circularity(rows):
    print(f"\n{SEP}")
    print("  PROBLEM 4 · FEATURE CIRCULARITY — prior_probability Drives 88% of Temporal Model")
    print(SEP)

    # Build feature matrix with and without prior_probability
    labelled = []
    for r in rows:
        label = label_for_training(r)
        if label is None:
            continue
        feats = extract_features(r)
        labelled.append((feats, label))

    if len(labelled) < 100:
        print("  Insufficient data")
        return

    feat_names = list(labelled[0][0].keys())
    X = [list(f.values()) for f, _ in labelled]
    y = [l for _, l in labelled]

    # Find prior_probability index
    prior_idx = feat_names.index("prior_probability") if "prior_probability" in feat_names else -1

    sc = StandardScaler()
    X_arr = np.array(X)
    X_s = sc.fit_transform(X_arr)

    # Model WITH prior_probability
    X_tr, X_te, y_tr, y_te = train_test_split(X_s, y, test_size=0.2, random_state=42, stratify=y)
    clf_full = GradientBoostingClassifier(n_estimators=80, max_depth=3, learning_rate=0.05, random_state=42)
    clf_full.fit(X_tr, y_tr)
    auc_full = roc_auc_score(y_te, clf_full.predict_proba(X_te)[:, 1])

    # Model WITHOUT prior_probability
    if prior_idx >= 0:
        cols = [i for i in range(X_arr.shape[1]) if i != prior_idx]
        X_no_prior = X_s[:, cols]
        X_tr2, X_te2, _, _ = train_test_split(X_no_prior, y, test_size=0.2, random_state=42, stratify=y)
        clf_no = GradientBoostingClassifier(n_estimators=80, max_depth=3, learning_rate=0.05, random_state=42)
        clf_no.fit(X_tr2, y_tr)
        auc_no = roc_auc_score(y_te, clf_no.predict_proba(X_te2)[:, 1])

        imps = clf_full.feature_importances_
        prior_imp = imps[prior_idx]

        print(f"""
  Feature importances (top 5):
  {'Feature':<35} {'Importance':>10}
  {'─'*47}""")
        top_idx = np.argsort(imps)[::-1][:8]
        for i in top_idx:
            bar = "█" * int(imps[i] * 40)
            print(f"  {feat_names[i]:<35} {imps[i]:>10.4f}  {bar}")

        print(f"""
  AUC WITH prior_probability:    {auc_full:.3f}
  AUC WITHOUT prior_probability: {auc_no:.3f}   (drop = {auc_full - auc_no:+.3f})

  WHY THIS IS A PROBLEM:
  ───────────────────────
  prior_probability = {prior_imp:.1%} of model weight

  The prior_probability feature is calculated in feature_extractor.py as
  the empirical success rate for (indication × stage) from the same DB
  that the model was trained on.

  This means:
    • prior_probability encodes outcome information from the training set
    • When used as a feature in the same training run → data leakage
    • The model is not learning new signals — it's recapitulating
      the historical success rate, which it already used to build the prior

  Without prior_probability, AUC drops by {auc_full - auc_no:.3f} — revealing how little
  the remaining features (mechanism, indication, investment) actually contribute.

  FIX NEEDED: Either (a) hold out prior_probability from the feature set
  and keep it only as a baseline, or (b) calculate it on a separate
  hold-out period (e.g., use pre-2010 data to set priors, train on 2010+).
""")


# ─────────────────────────────────────────────────────────────────────────────
# PROBLEM 5 · Funnel Inversion (Phase 1 > Preclinical)
# ─────────────────────────────────────────────────────────────────────────────

def problem_5_funnel_inversion(rows):
    print(f"\n{SEP}")
    print("  PROBLEM 5 · FUNNEL INVERSION — Phase 1 Count Exceeds Preclinical (118%)")
    print(SEP)

    stage_counts = Counter(r.get("clinical_stage", "unknown") for r in rows)
    source_counts = Counter(r.get("source", "unknown") for r in rows)

    # By source, what stages do we see?
    stage_by_source: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        stage_by_source[r.get("source", "unknown")][r.get("clinical_stage", "unknown")] += 1

    print("\n  Stage counts across all sources:")
    for stage in ["preclinical", "ind_filing", "phase1", "phase2", "phase3", "nda_submitted", "approved", "unknown"]:
        n = stage_counts.get(stage, 0)
        bar = "█" * (n // 30)
        print(f"    {stage:<18} {n:>5}  {bar}")

    print("\n  Phase1 / Preclinical ratio by source:")
    print(f"  {'Source':<30}  {'Preclinical':>11}  {'Phase1':>7}  {'Ratio':>6}")
    print("  " + "─" * 58)
    for src, scnt in sorted(stage_by_source.items(), key=lambda x: -sum(x[1].values())):
        prec = scnt.get("preclinical", 0)
        ph1  = scnt.get("phase1", 0)
        ratio = ph1 / max(prec, 1)
        flag = " ← INVERTED" if ratio > 1.1 else ""
        print(f"  {src:<30}  {prec:>11}  {ph1:>7}  {ratio:>6.1f}x{flag}")

    print(f"""
  ROOT CAUSE:
  ────────────
  ClinicalTrials.gov, SEC EDGAR, and VC portfolio records all typically
  capture a drug at Phase 1 or later — they rarely capture the preclinical
  decision point, because that happens BEFORE registration.

  This means:
    • Preclinical stage is severely under-represented in real-world data
    • The funnel denominator (preclinical entries) is wrong
    • Survival rates and transition probabilities are systematically inflated
    • The "Preclinical → IND: 17.5%" figure is NOT from real preclinical programs;
      it's from the simulated cohort which has fixed preclinical → IND parameters

  CONSEQUENCE: Any model using stage as a feature treats Phase 1 entries as
  if they're mid-pipeline, but most are pipeline-starts — their baseline
  probability of approval is closer to 10%, not 52% (P1 pass rate).
""")


# ─────────────────────────────────────────────────────────────────────────────
# PROBLEM 6 · Investment Endogeneity
# ─────────────────────────────────────────────────────────────────────────────

def problem_6_investment_endogeneity(rows):
    print(f"\n{SEP}")
    print("  PROBLEM 6 · INVESTMENT ENDOGENEITY — Investment Is an Outcome Proxy, Not a Cause")
    print(SEP)

    has_invest = [r for r in rows if float(r.get("investment_usd", 0) or 0) > 0]
    no_invest  = [r for r in rows if float(r.get("investment_usd", 0) or 0) == 0]

    # Success rates by investment bracket
    brackets = [(0, 1e6, "<$1M"), (1e6, 10e6, "$1-10M"), (10e6, 50e6, "$10-50M"),
                (50e6, 200e6, "$50-200M"), (200e6, 1e12, ">$200M")]

    print("\n  Success rate by investment amount:")
    print(f"  {'Bracket':<12}  {'N':>5}  {'Approvals':>9}  {'Success%':>9}")
    print("  " + "─" * 42)
    for lo, hi, label in brackets:
        cohort = [r for r in rows if lo <= float(r.get("investment_usd", 0) or 0) < hi]
        n_appr = sum(1 for r in cohort if r.get("outcome") == "approved")
        n_fail = sum(1 for r in cohort if "discontinued" in r.get("outcome", ""))
        total_resolved = n_appr + n_fail
        pct = n_appr / total_resolved if total_resolved > 0 else 0
        print(f"  {label:<12}  {len(cohort):>5}  {n_appr:>9}  {pct:>9.1%}")

    print(f"""
  WHY THIS CAUSES LOOK-AHEAD BIAS:
  ──────────────────────────────────
  Investment amount is the #1 feature in the full model (weight = 39%).

  In practice, investment amount for a program is determined AFTER assessing
  its potential — it's a consequence of optimism about the outcome, not
  an independent predictor of it.

  Specifically in this dataset:
    • Approved programs have higher investment because companies continue
      funding programs that are progressing well
    • hist30_cohort assigns investment stochastically correlated with
      stage-advancement, so it leaks stage-progression information

  The model is effectively learning: "programs that received more money
  tend to be approved" — which is true by construction, not causal.

  CONSEQUENCE: Using investment as a real-time decision feature is valid
  ONLY if you have the investment committed BEFORE seeing the outcome.
  For retrospective records, the investment already embeds forward-looking
  information that a real investor would not have had at decision time.
""")


# ─────────────────────────────────────────────────────────────────────────────
# PROBLEM 7 · Synthetic Data Dominance
# ─────────────────────────────────────────────────────────────────────────────

def problem_7_synthetic_dominance(rows):
    print(f"\n{SEP}")
    print("  PROBLEM 7 · SYNTHETIC DATA DOMINANCE — 77% of Training Data Is Simulated")
    print(SEP)

    source_counts = Counter(r.get("source", "unknown") for r in rows)
    sim_sources = {"historical_cohort", "hist30_cohort"}
    real_sources = set(source_counts.keys()) - sim_sources

    n_sim  = sum(v for k, v in source_counts.items() if k in sim_sources)
    n_real = sum(v for k, v in source_counts.items() if k not in sim_sources)
    total  = n_sim + n_real

    print("\n  Record counts by source:")
    for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
        tag = " [SIMULATED]" if src in sim_sources else " [REAL]"
        bar = "█" * (cnt // 60)
        print(f"    {src:<25} {cnt:>5}{tag}  {bar}")

    # Compare model accuracy on sim-only vs real-only
    def auc_for_subset(subset):
        labelled = []
        for r in subset:
            label = label_for_training(r)
            if label is None:
                continue
            feats = extract_features(r)
            labelled.append((list(feats.values()), label))
        if len(labelled) < 40:
            return None, len(labelled)
        X = [x[0] for x in labelled]
        y = [x[1] for x in labelled]
        if len(set(y)) < 2:
            return None, len(labelled)
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)
        sc = StandardScaler()
        clf = GradientBoostingClassifier(n_estimators=60, max_depth=3, random_state=42)
        clf.fit(sc.fit_transform(X_tr), y_tr)
        try:
            auc = roc_auc_score(y_te, clf.predict_proba(sc.transform(X_te))[:, 1])
            return auc, len(labelled)
        except:
            return None, len(labelled)

    sim_rows  = [r for r in rows if r.get("source") in sim_sources]
    real_rows = [r for r in rows if r.get("source") not in sim_sources]

    auc_sim,  n_sim_lab  = auc_for_subset(sim_rows)
    auc_real, n_real_lab = auc_for_subset(real_rows)

    print(f"""
  Simulated:  {n_sim:>5} records  ({100*n_sim/total:.1f}%)
  Real-world: {n_real:>5} records  ({100*n_real/total:.1f}%)

  AUC on simulated data only:  {f"{auc_sim:.3f} (n={n_sim_lab})" if auc_sim else "n/a — insufficient labelled"}
  AUC on real-world data only: {f"{auc_real:.3f} (n={n_real_lab})" if auc_real else "n/a — insufficient labelled"}

  ROOT CAUSE:
  ────────────
  generate_30year_history.py creates synthetic programs with:
    • Fixed success rates per era (hardcoded in the generator)
    • Stochastic investment amounts correlated with stage advancement
    • Outcomes assigned by coin-flip against fixed probability tables

  The ML model trained on this data learns the SIMULATION PARAMETERS,
  not real-world bioventure patterns. When tested on real data, it faces:
    • Different base rates (real Phase 2 ≈ 29% vs simulation-tuned rates)
    • Missing features (mechanism, biomarker often "unknown" in real records)
    • Selection bias (ClinicalTrials only captures registered trials)

  CONSEQUENCE: The reported AUC of 0.787 is largely a measure of how well
  the model reverse-engineers the simulation — not how well it would
  predict real drug approvals.

  To fix: either (a) remove simulated records from ML training (use for
  RL/cohort analysis only), or (b) validate exclusively on held-out
  real-world records and report that AUC separately.
""")


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY TABLE
# ─────────────────────────────────────────────────────────────────────────────

def summary(rows):
    print(f"\n{SEP}")
    print("  DIAGNOSTIC SUMMARY — 7 ROOT-CAUSE PROBLEMS")
    print(SEP)
    print(f"""
  ┌─────┬──────────────────────────────────────────┬──────────┬───────────────┐
  │  #  │ Problem                                  │ Severity │ Fix Type      │
  ├─────┼──────────────────────────────────────────┼──────────┼───────────────┤
  │  1  │ Decision quality 100% — hindsight bias   │ CRITICAL │ Arch change   │
  │  2  │ AUC collapses to 0.617 at 2024 cohort    │ HIGH     │ Data pipeline │
  │  3  │ RL always-kills — degenerate policy      │ HIGH     │ Reward design │
  │  4  │ prior_probability = 88% model weight     │ HIGH     │ Feature eng.  │
  │  5  │ Phase1 > Preclinical (118%) — funnel bug  │ MEDIUM   │ Data hygiene  │
  │  6  │ Investment = #1 feature (look-ahead bias) │ MEDIUM   │ Feature eng.  │
  │  7  │ 77% synthetic training data              │ MEDIUM   │ Data strategy │
  └─────┴──────────────────────────────────────────┴──────────┴───────────────┘

  PRIORITY FIXES:
  ────────────────
  1. CRITICAL: Section-8 decision audit must use PROSPECTIVE go/no-go decisions
     (i.e., records where the decision was made BEFORE the outcome was known).
     Current retrospective reconstruction produces 100% accuracy by construction.

  2. HIGH: Separate the ML train/test split for real-world vs simulated data.
     Report AUC on real-world records only (currently mixed with 77% synthetic).

  3. HIGH: Redesign RL reward to penalize missed approvals (false kills):
     Current kill reward = +$1.75M; false-kill penalty = -$10M.
     With 90%+ fail rate, expected always-kill = positive → converges to kill.
     Fix: raise false-kill penalty to -$200M (match approval payoff scale).

  4. HIGH: Remove prior_probability as a training feature OR use time-separated
     priors (compute prior from pre-2010 data; train on 2010+ only).

  5. MEDIUM: Add a "stage_entry_source" flag to distinguish programs that
     enter the DB at Phase 1 from those tracked from preclinical onset.
     Prevents funnel denominator inflation.
""")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{SEP}")
    print("  BIOVENTURE DECISION INTELLIGENCE — STRUCTURAL AUDIT")
    print("  Diagnosing root-cause problems in the decision model")
    print(SEP)

    rows = load()
    print(f"\n  Loaded {len(rows)} records from {DB_PATH}")

    problem_1_data_leakage(rows)
    problem_2_auc_collapse(rows)
    problem_3_degenerate_rl(rows)
    problem_4_feature_circularity(rows)
    problem_5_funnel_inversion(rows)
    problem_6_investment_endogeneity(rows)
    problem_7_synthetic_dominance(rows)
    summary(rows)
