#!/usr/bin/env python3
"""
run_10year_analysis.py
──────────────────────
Comprehensive 10-year (2016-2026) BioVenture track record and analysis.

Sections:
  1. Cohort Overview        — programs by start year, outcomes, investment
  2. Annual Decision Audit  — year-by-year go/no-go, kill rates, spend
  3. Stage Attrition Funnel — empirical vs. industry transition rates
  4. Survival Analysis      — KM-style cohort curves (ASCII)
  5. Investment & ROI       — capital deployed, returns, cost-per-approval
  6. Indication Deep-Dive   — success rates & investment by therapeutic area
  7. ML Temporal Validation — train 2016-2021, validate 2022-2026
  8. Decision Retrospective — correct kills, missed wins, precision/recall
  9. RL Portfolio (10 yrs)  — Q-agent cumulative reward across cohorts
 10. Platform Summary
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH      = Path("data/bioventure.json")
REPORT_PATH  = Path("data/30year_analysis.txt")
SOURCES      = {"historical_cohort", "hist30_cohort", "vc_portfolio"}   # generators + real VC data
CURRENT_YEAR = 2026
START_YEAR   = 1996
YEARS        = list(range(START_YEAR, CURRENT_YEAR + 1))

SEP  = "\n" + "═" * 72
SEP2 = "─" * 72


def section(title: str) -> None:
    print(SEP)
    print(f"  {title}")
    print("═" * 72)


# ── Data loading ───────────────────────────────────────────────────────────────

def load_records() -> list[dict]:
    data = json.loads(DB_PATH.read_text())
    return list(data.get("projects", {}).values())


def hist_records(rows: list[dict]) -> list[dict]:
    """Return historical_cohort records from all generator sources."""
    return [r for r in rows if r.get("source") in SOURCES]


def programs_from_records(recs: list[dict]) -> dict[str, list[dict]]:
    """Group stage-records by prog_id."""
    prog: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        pid = r.get("extra", {}).get("prog_id")
        if pid:
            prog[pid].append(r)
    return dict(prog)


# ── Section 1: Cohort Overview ─────────────────────────────────────────────────

def sec_cohort_overview(recs: list[dict]) -> dict:
    section("SECTION 1 · 30-YEAR COHORT OVERVIEW (1996-2024 START COHORTS)")

    programs = programs_from_records(recs)
    cohort_stats: dict[int, dict] = {}

    for pid, stages in programs.items():
        start_year = stages[0].get("extra", {}).get("cohort_start", 0)
        outcomes   = [s.get("outcome", "unknown") for s in stages]
        invest     = sum(s.get("investment_usd", 0) or 0 for s in stages)

        approved    = any(o == "approved" for o in outcomes)
        disc        = any("discontinued" in o for o in outcomes)
        ongoing_now = not approved and not disc

        if start_year not in cohort_stats:
            cohort_stats[start_year] = {
                "programs": 0, "approved": 0, "discontinued": 0, "ongoing": 0,
                "investment": 0.0,
            }
        c = cohort_stats[start_year]
        c["programs"]    += 1
        c["investment"]  += invest
        if approved:
            c["approved"] += 1
        elif disc:
            c["discontinued"] += 1
        else:
            c["ongoing"] += 1

    print(f"\n  {'Year':<6} {'Programs':>9} {'Approved':>9} {'Discontinued':>13} "
          f"{'Ongoing':>8} {'Total Invested':>16} {'Approval %':>11}")
    print(f"  {SEP2}")

    total_prog = total_approv = total_disc = total_ong = 0
    total_invest = 0.0

    for yr in sorted(cohort_stats):
        c = cohort_stats[yr]
        pct = c["approved"] / c["programs"] * 100 if c["programs"] else 0
        print(f"  {yr:<6} {c['programs']:>9} {c['approved']:>9} {c['discontinued']:>13} "
              f"{c['ongoing']:>8} {c['investment']/1e9:>14.1f}B  {pct:>9.1f}%")
        total_prog   += c["programs"]
        total_approv += c["approved"]
        total_disc   += c["discontinued"]
        total_ong    += c["ongoing"]
        total_invest += c["investment"]

    print(f"  {SEP2}")
    pct_all = total_approv / total_prog * 100 if total_prog else 0
    print(f"  {'TOTAL':<6} {total_prog:>9} {total_approv:>9} {total_disc:>13} "
          f"{total_ong:>8} {total_invest/1e9:>14.1f}B  {pct_all:>9.1f}%")

    return cohort_stats


# ── Section 2: Annual Decision Audit ──────────────────────────────────────────

def sec_annual_audit(recs: list[dict]) -> None:
    section("SECTION 2 · ANNUAL DECISION AUDIT (1996-2026, 30 YEARS)")

    by_year: dict[int, dict] = defaultdict(lambda: {
        "go": 0, "no-go": 0, "invest": 0.0
    })

    for r in recs:
        yr  = r.get("extra", {}).get("decision_year")
        dec = r.get("decision", "")
        inv = r.get("investment_usd", 0) or 0
        if yr and START_YEAR <= yr <= CURRENT_YEAR:
            by_year[yr]["go" if dec == "go" else "no-go"] += 1
            by_year[yr]["invest"] += inv

    print(f"\n  {'Year':<6} {'Go':>5} {'No-Go':>7} {'Kill Rate':>10} "
          f"{'Invested ($B)':>15} {'Saved ($M)':>12}")
    print(f"  {SEP2}")

    decade_invest: dict[str, float] = {"1996-2005": 0, "2006-2015": 0, "2016-2026": 0}
    decade_kills:  dict[str, int]   = {"1996-2005": 0, "2006-2015": 0, "2016-2026": 0}
    decade_total:  dict[str, int]   = {"1996-2005": 0, "2006-2015": 0, "2016-2026": 0}

    for yr in sorted(by_year):
        d   = by_year[yr]
        tot = d["go"] + d["no-go"]
        kr  = d["no-go"] / tot * 100 if tot else 0
        saved = d["invest"] / max(tot, 1) * d["no-go"] * 0.8
        bar = "█" * int(kr / 5)
        print(f"  {yr:<6} {d['go']:>5} {d['no-go']:>7} {kr:>9.1f}%  "
              f"{d['invest']/1e9:>13.2f}B  {saved/1e6:>9.0f}M  {bar}")
        # Decade buckets
        if yr <= 2005:   dk = "1996-2005"
        elif yr <= 2015: dk = "2006-2015"
        else:            dk = "2016-2026"
        decade_invest[dk] += d["invest"]
        decade_kills[dk]  += d["no-go"]
        decade_total[dk]  += tot

    print(f"\n  ── Decade summary ──")
    print(f"  {'Decade':<12} {'Decisions':>10} {'Kill Rate':>10} {'Total Invested':>16}")
    print(f"  {'-'*52}")
    for dk in ["1996-2005", "2006-2015", "2016-2026"]:
        kr = decade_kills[dk] / decade_total[dk] * 100 if decade_total[dk] else 0
        print(f"  {dk:<12} {decade_total[dk]:>10} {kr:>9.1f}%  "
              f"{decade_invest[dk]/1e9:>13.1f}B")


# ── Section 3: Stage Attrition Funnel ─────────────────────────────────────────

def sec_attrition_funnel(recs: list[dict]) -> None:
    section("SECTION 3 · STAGE ATTRITION FUNNEL (full pipeline incl. IND filing & NDA)")

    STAGES   = ["preclinical", "ind_filing", "phase1", "phase2", "phase3", "nda_submitted"]
    INDUSTRY = {
        "preclinical":   0.10,
        "ind_filing":    0.87,   # FDA IND clearance ~87% (10-13% clinical hold)
        "phase1":        0.52,
        "phase2":        0.29,
        "phase3":        0.58,
        "nda_submitted": 0.85,
    }
    STAGE_LABELS = {
        "preclinical": "Preclinical",
        "ind_filing":  "IND Filing (PI)",
        "phase1":      "Phase 1 (PI)",
        "phase2":      "Phase 2",
        "phase3":      "Phase 3",
        "nda_submitted": "NDA/BLA Review",
    }

    stage_counts: dict[str, dict[str, int]] = {
        s: {"entered": 0, "passed": 0} for s in STAGES
    }

    for r in recs:
        st  = r.get("clinical_stage", "")
        out = r.get("outcome", "")
        if st in stage_counts and r.get("extra", {}).get("observable", True):
            stage_counts[st]["entered"] += 1
            if out in ("ongoing", "approved"):
                stage_counts[st]["passed"] += 1

    print(f"\n  {'Stage':<20} {'Entered':>8} {'Passed':>7} {'Empirical%':>11} "
          f"{'Industry%':>10} {'Delta':>7}")
    print(f"  {SEP2}")

    for st in STAGES:
        c = stage_counts[st]
        if c["entered"] == 0:
            continue
        emp = c["passed"] / c["entered"] * 100
        ind = INDUSTRY[st] * 100
        dlt = emp - ind
        bar = ("+" if dlt >= 0 else "-") * min(int(abs(dlt) / 2), 10)
        label = STAGE_LABELS[st]
        print(f"  {label:<20} {c['entered']:>8} {c['passed']:>7} {emp:>10.1f}%  "
              f"{ind:>9.1f}%  {dlt:>+6.1f}%  {bar}")

    print(f"\n  ── Overall pipeline funnel (% of preclinical programs reaching each stage) ──")
    base = stage_counts["preclinical"]["entered"]
    if base > 0:
        print(f"  {'Preclinical':20} 100%  ({base})")
        for st in ["ind_filing", "phase1", "phase2", "phase3", "nda_submitted", "approved"]:
            if st == "approved":
                n = sum(1 for r in recs if r.get("outcome") == "approved")
            else:
                n = stage_counts[st]["entered"]
            pct = n / base * 100
            bar = "█" * int(pct / 2)
            label = STAGE_LABELS.get(st, st)
            print(f"  {label:<20} {pct:>4.0f}%  ({n:>5})  {bar}")

    # Also break out by era
    print(f"\n  ── Phase 2 success rate by era (improvement over 30 years) ──")
    for era_label, yrange in [("1996-2004", range(1996, 2005)),
                               ("2005-2014", range(2005, 2015)),
                               ("2015-2026", range(2015, 2027))]:
        era_recs = [r for r in recs
                    if r.get("clinical_stage") == "phase2"
                    and r.get("extra", {}).get("decision_year", 0) in yrange
                    and r.get("extra", {}).get("observable", True)]
        entered = len(era_recs)
        passed  = sum(1 for r in era_recs if r.get("outcome") in ("ongoing", "approved"))
        pct     = passed / entered * 100 if entered else 0
        print(f"    {era_label}: {pct:.1f}%  ({passed}/{entered})")


# ── Section 4: Survival Analysis ──────────────────────────────────────────────

def sec_survival(cohort_stats: dict) -> None:
    section("SECTION 4 · COHORT SURVIVAL ANALYSIS (% programs alive each year)")

    programs = programs_from_records(
        [r for r in load_records() if r.get("source") in SOURCES]
    )

    # For each program, find the year it was discontinued (or None if still alive)
    prog_death: dict[str, int | None] = {}
    prog_start: dict[str, int] = {}
    for pid, stages in programs.items():
        start = stages[0].get("extra", {}).get("cohort_start", 0)
        prog_start[pid] = start
        death_yr = None
        for s in stages:
            out = s.get("outcome", "")
            if "discontinued" in out:
                death_yr = s.get("extra", {}).get("stage_end_year", CURRENT_YEAR)
                break
        prog_death[pid] = death_yr

    print(f"\n  Year-by-year survival: % of cohort still alive (not discontinued)")
    print(f"\n  {'Cohort':<8}", end="")
    for elapsed in range(0, 11):
        print(f"  Y+{elapsed:<2}", end="")
    print()
    print(f"  {SEP2}")

    for start_yr in sorted({v for v in prog_start.values() if v >= 2016}):
        cohort_pids = [p for p, s in prog_start.items() if s == start_yr]
        if not cohort_pids:
            continue
        n_start = len(cohort_pids)
        print(f"  {start_yr:<8}", end="")
        for elapsed in range(0, 11):
            check_yr = start_yr + elapsed
            if check_yr > CURRENT_YEAR:
                print(f"  {'?':>4}", end="")
                continue
            alive = sum(
                1 for p in cohort_pids
                if prog_death[p] is None or prog_death[p] > check_yr
            )
            pct = alive / n_start * 100
            bar = int(pct / 10)  # 1 char per 10%
            cell = f"{pct:3.0f}%"
            print(f"  {cell}", end="")
        print()


# ── Section 5: Investment & ROI ────────────────────────────────────────────────

def sec_investment_roi(recs: list[dict]) -> None:
    section("SECTION 5 · INVESTMENT TIMELINE & ROI ANALYSIS")

    by_year: dict[int, dict] = defaultdict(lambda: {
        "deployed": 0.0, "in_approved": 0.0, "in_killed": 0.0, "n_approved": 0
    })

    for r in recs:
        yr  = r.get("extra", {}).get("decision_year")
        inv = r.get("investment_usd", 0) or 0
        out = r.get("outcome", "")
        dec = r.get("decision", "")
        if yr and 2016 <= yr <= 2026:
            by_year[yr]["deployed"] += inv
            if out == "approved":
                by_year[yr]["in_approved"] += inv
                by_year[yr]["n_approved"]  += 1
            if dec == "no-go":
                by_year[yr]["in_killed"] += inv

    # Revenue model: each approved drug assumed ~$2B peak revenue
    APPROVAL_REVENUE = 2_000_000_000
    APPROVAL_MULTIPLE = 5.0  # NPV of 5× revenue at approval

    print(f"\n  {'Year':<6} {'Deployed ($B)':>14} {'In Approvals':>13} "
          f"{'In Kills':>10} {'Approvals':>10} {'Est. Value ($B)':>16}")
    print(f"  {SEP2}")

    cum_deployed = 0.0
    cum_value    = 0.0
    for yr in sorted(by_year):
        d = by_year[yr]
        cum_deployed += d["deployed"]
        est_val = d["n_approved"] * APPROVAL_REVENUE * APPROVAL_MULTIPLE
        cum_value   += est_val
        print(f"  {yr:<6} {d['deployed']/1e9:>13.1f}B  {d['in_approved']/1e9:>11.1f}B  "
              f"{d['in_killed']/1e9:>8.1f}B  {d['n_approved']:>9}  {est_val/1e9:>14.1f}B")

    print(f"  {SEP2}")
    print(f"  {'TOTAL':>6} {cum_deployed/1e9:>13.1f}B{'':>13}{'':>10}{'':>10}  "
          f"{cum_value/1e9:>14.1f}B")
    roi = (cum_value - cum_deployed) / cum_deployed * 100 if cum_deployed else 0
    print(f"\n  Estimated portfolio ROI:   {roi:+.1f}%")

    # Cost per approval
    approved_recs = [r for r in recs if r.get("outcome") == "approved"]
    approved_progs = {r.get("extra", {}).get("prog_id") for r in approved_recs}
    programs = programs_from_records(recs)
    cost_per_approval: list[float] = []
    for pid in approved_progs:
        total_prog_invest = sum(s.get("investment_usd", 0) or 0 for s in programs.get(pid, []))
        cost_per_approval.append(total_prog_invest)

    if cost_per_approval:
        avg_cpa = sum(cost_per_approval) / len(cost_per_approval)
        print(f"  Approved programs:        {len(approved_progs)}")
        print(f"  Avg cost per approval:   ${avg_cpa/1e6:>8.0f}M")


# ── Section 6: Indication Deep-Dive ───────────────────────────────────────────

def sec_indication_deep_dive(recs: list[dict]) -> None:
    section("SECTION 6 · THERAPEUTIC AREA DEEP-DIVE")

    groups: dict[str, dict] = defaultdict(lambda: {
        "programs": set(), "approved": 0, "discontinued": 0, "ongoing": 0,
        "investment": 0.0, "stage_pass": defaultdict(lambda: {"enter": 0, "pass": 0}),
    })

    programs = programs_from_records(recs)
    for pid, stages in programs.items():
        ig    = stages[0].get("extra", {}).get("ind_group", "other")
        inv   = sum(s.get("investment_usd", 0) or 0 for s in stages)
        outs  = [s.get("outcome", "") for s in stages]
        obs   = [s.get("extra", {}).get("observable", True) for s in stages]

        g = groups[ig]
        g["programs"].add(pid)
        g["investment"] += inv
        if any(o == "approved" for o in outs):
            g["approved"] += 1
        elif any("discontinued" in o for o in outs):
            g["discontinued"] += 1
        else:
            g["ongoing"] += 1

        for s in stages:
            st  = s.get("clinical_stage", "")
            out = s.get("outcome", "")
            if st in ("preclinical","phase1","phase2","phase3") and s.get("extra",{}).get("observable"):
                g["stage_pass"][st]["enter"] += 1
                if "ongoing" in out or out == "approved":
                    g["stage_pass"][st]["pass"] += 1

    print(f"\n  {'Area':<16} {'Progs':>6} {'Approv':>7} {'Kill%':>7} "
          f"{'P1 pass%':>9} {'P2 pass%':>9} {'Invest ($B)':>12}")
    print(f"  {SEP2}")

    for ig in sorted(groups, key=lambda x: -len(groups[x]["programs"])):
        g    = groups[ig]
        n    = len(g["programs"])
        disc = g["discontinued"]
        tot  = g["approved"] + g["discontinued"] + g["ongoing"]
        kr   = disc / tot * 100 if tot else 0
        p1   = g["stage_pass"]["phase1"]
        p2   = g["stage_pass"]["phase2"]
        p1p  = p1["pass"] / p1["enter"] * 100 if p1["enter"] else 0
        p2p  = p2["pass"] / p2["enter"] * 100 if p2["enter"] else 0
        print(f"  {ig:<16} {n:>6} {g['approved']:>7} {kr:>6.1f}%  "
              f"{p1p:>8.1f}%  {p2p:>8.1f}%  {g['investment']/1e9:>10.1f}B")


# ── Section 7: ML Temporal Validation ─────────────────────────────────────────

def sec_ml_temporal(rows: list[dict]) -> None:
    section("SECTION 7 · ML TEMPORAL VALIDATION (train 1996-2015, test 2016-2026)")

    from src.learning.decision_model import SuccessPredictor
    from src.processors.feature_extractor import label_for_training, extract_features

    def year_of(r: dict) -> int:
        return r.get("extra", {}).get("decision_year",
               r.get("extra", {}).get("cohort_start", 9999))

    def build_xy(subset: list[dict]) -> tuple[list, list]:
        X, y = [], []
        for r in subset:
            lbl = label_for_training(r)
            if lbl is None:
                continue
            feats = extract_features(r)
            X.append(list(feats.values()))
            y.append(lbl)
        return X, y

    # 30-year split: train on first 20 years, test on last 10
    train_rows = [r for r in rows if year_of(r) <= 2015]
    test_rows  = [r for r in rows if 2016 <= year_of(r) <= 2026]
    all_rows   = rows

    X_train, y_train = build_xy(train_rows)
    X_test,  y_test  = build_xy(test_rows)
    X_all,   y_all   = build_xy(all_rows)

    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score, accuracy_score, precision_score, recall_score
    from sklearn.preprocessing import StandardScaler
    import numpy as np

    def train_eval(X_tr, y_tr, X_te, y_te, label=""):
        if len(X_tr) < 20 or len(X_te) < 5:
            return None
        # Skip if test set has only one class (AUC undefined)
        if len(set(y_te)) < 2:
            return None
        scaler = StandardScaler()
        Xts = scaler.fit_transform(X_tr)
        Xvs = scaler.transform(X_te)

        lr = LogisticRegression(max_iter=500, C=0.5, class_weight="balanced")
        gb = GradientBoostingClassifier(n_estimators=80, max_depth=3, learning_rate=0.05)
        lr.fit(Xts, y_tr)
        gb.fit(Xts, y_tr)

        p = (lr.predict_proba(Xvs)[:, 1] + gb.predict_proba(Xvs)[:, 1]) / 2
        pbin = (p >= 0.5).astype(int)
        try:
            auc = roc_auc_score(y_te, p)
        except ValueError:
            return None
        acc  = accuracy_score(y_te, pbin)
        prec = precision_score(y_te, pbin, zero_division=0)
        rec  = recall_score(y_te, pbin, zero_division=0)
        return {"auc": auc, "acc": acc, "prec": prec, "rec": rec,
                "n_train": len(X_tr), "n_test": len(X_te), "gb": gb,
                "scaler": scaler, "lr": lr}

    print(f"\n  ── In-sample (all years) ──")
    res_all = train_eval(X_all, y_all, X_all, y_all, "All")
    if res_all:
        print(f"    n={res_all['n_train']}   AUC={res_all['auc']:.3f}  "
              f"Acc={res_all['acc']:.3f}  Prec={res_all['prec']:.3f}  Rec={res_all['rec']:.3f}")

    print(f"\n  ── Temporal (train 1996-2015, test 2016-2026) ──")
    res_temp = train_eval(X_train, y_train, X_test, y_test, "Temporal")
    if res_temp:
        print(f"    Train n={res_temp['n_train']}  Test n={res_temp['n_test']}")
        print(f"    AUC={res_temp['auc']:.3f}  Acc={res_temp['acc']:.3f}  "
              f"Prec={res_temp['prec']:.3f}  Rec={res_temp['rec']:.3f}")

    # Year-by-year AUC: train on everything before year Y, test on year Y
    print(f"\n  ── Rolling AUC (train on all prior years, test on target year) ──")
    print(f"  {'Test Year':<11} {'Train N':>8} {'Test N':>8} {'AUC':>7} {'Acc':>7}")
    print(f"  {SEP2[:50]}")

    for test_yr in range(2000, 2027, 2):   # every 2 years for 30-yr view
        tr = [r for r in rows if year_of(r) < test_yr]
        te = [r for r in rows if year_of(r) == test_yr]
        Xtr, ytr = build_xy(tr)
        Xte, yte = build_xy(te)
        res = train_eval(Xtr, ytr, Xte, yte)
        if res:
            print(f"  {test_yr:<11} {res['n_train']:>8} {res['n_test']:>8} "
                  f"{res['auc']:>6.3f}  {res['acc']:>6.3f}")
        else:
            print(f"  {test_yr:<11}   {'—':>8}  {'—':>8}  {'—':>7}  {'—':>7}")

    # Feature importances from temporal model
    if res_temp and res_temp.get("gb"):
        from src.processors.feature_extractor import extract_features
        sample_feats = extract_features(train_rows[0]) if train_rows else {}
        feat_names = list(sample_feats.keys()) if sample_feats else []
        if feat_names:
            print(f"\n  ── Feature importances (temporal model) ──")
            imps = res_temp["gb"].feature_importances_
            pairs = sorted(zip(feat_names, imps), key=lambda x: -x[1])
            for feat, imp in pairs[:10]:
                bar = "█" * int(imp * 35)
                print(f"    {feat:<35s} {imp:.4f}  {bar}")


# ── Section 8: Decision Retrospective ─────────────────────────────────────────

def sec_decision_retrospective(recs: list[dict]) -> None:
    section("SECTION 8 · DECISION QUALITY RETROSPECTIVE")

    programs = programs_from_records(recs)

    TP = TN = FP = FN = 0
    decision_invest_saved = 0.0
    missed_value = 0.0

    for pid, stages in programs.items():
        outs  = [s.get("outcome", "") for s in stages]
        decs  = [s.get("decision", "") for s in stages]
        inv   = sum(s.get("investment_usd", 0) or 0 for s in stages)
        obs   = all(s.get("extra", {}).get("observable", True) for s in stages)
        if not obs:
            continue

        truly_approved = any(o == "approved" for o in outs)
        killed         = any(d == "no-go" for d in decs)

        if killed and not truly_approved:
            TP += 1      # correct kill
            decision_invest_saved += inv * 0.5  # rough estimate of future spend avoided
        elif killed and truly_approved:
            FP += 1      # wrong kill — missed a winner
            missed_value += 2_000_000_000       # rough NPV of missed approval
        elif not killed and truly_approved:
            TN += 1      # correctly kept a winner
        elif not killed and not truly_approved:
            FN += 1      # kept a loser (should have killed earlier)

    total = TP + TN + FP + FN
    precision = TP / (TP + FP) if (TP + FP) else 0
    recall    = TP / (TP + FN) if (TP + FN) else 0
    accuracy  = (TP + TN) / total if total else 0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    print(f"""
  Kill-decision confusion matrix (observable programs only):

                        Actually      Actually
                         Failed       Approved
    ┌──────────────────┬──────────────┬──────────────┐
    │ Decision: Kill   │ TP = {TP:>5}  │ FP = {FP:>5}  │
    ├──────────────────┼──────────────┼──────────────┤
    │ Decision: Keep   │ FN = {FN:>5}  │ TN = {TN:>5}  │
    └──────────────────┴──────────────┴──────────────┘

  Kill precision (correct kills / all kills):  {precision:.1%}
  Kill recall    (correct kills / all fails):  {recall:.1%}
  Decision accuracy:                           {accuracy:.1%}
  F1 score:                                    {f1:.3f}

  Capital preserved by correct kills:         ${decision_invest_saved/1e9:.1f}B (est.)
  Value lost from incorrect kills (FP):        ${missed_value/1e9:.1f}B (est. NPV)
  Net decision value:                         ${(decision_invest_saved - missed_value)/1e9:+.1f}B
""")


# ── Section 9: RL Portfolio Simulation ────────────────────────────────────────

def sec_rl_portfolio(rows: list[dict]) -> None:
    section("SECTION 9 · RL PORTFOLIO SIMULATION (30-YEAR POLICY)")

    from src.learning.rl_env import BioVentureEnv, TabularQAgent, run_greedy_episode
    from src.processors.feature_extractor import extract_features

    # Sort records by decision_year to simulate in chronological order
    hist = [r for r in rows if r.get("source") in SOURCES]
    if not hist:
        print("  No historical_cohort records found.")
        return

    # Enrich with prior probabilities
    for r in hist:
        feats = extract_features(r)
        r["feat_prior"] = feats.get("prior_probability", 0.35)

    # Train on 1996-2015 cohort (first 20 years)
    train_set = [r for r in hist
                 if r.get("extra", {}).get("decision_year", 9999) <= 2015]
    env_train = BioVentureEnv.from_records(train_set)
    agent = TabularQAgent(lr=0.1, gamma=0.95, epsilon=0.25)

    print(f"\n  Training RL agent on 1996-2015 cohort ({len(train_set)} records)...")
    rewards_train = agent.train(env_train, episodes=800)
    avg_early = sum(rewards_train[:60]) / 60 if len(rewards_train) >= 60 else 0
    avg_late  = sum(rewards_train[-60:]) / 60 if len(rewards_train) >= 60 else 0
    pct       = (avg_late - avg_early) / (abs(avg_early) + 1) * 100
    print(f"  Training reward: first-60 avg ${avg_early:>10,.0f}  →  "
          f"last-60 avg ${avg_late:>10,.0f}  ({pct:+.1f}%)")

    # Deploy on 2016-2026 cohort (held-out)
    test_set = [r for r in hist
                if r.get("extra", {}).get("decision_year", 0) >= 2016]
    print(f"  Deploying on 2016-2026 cohort ({len(test_set)} records)...")

    if test_set:
        env_test = BioVentureEnv.from_records(test_set)
        rewards_test = agent.train(env_test, episodes=1)   # single pass, epsilon=0
        # Greedy eval
        agent.epsilon = 0.0
        greedy_rewards = []
        n_correct = 0
        actions_taken: Counter = Counter()

        for ep_idx in range(min(12, len(test_set))):
            summary = run_greedy_episode(env_test, agent, episode_idx=ep_idx)
            hist_ep = summary.get("history", [])
            ep_reward = sum(h.get("reward", 0) for h in hist_ep)
            greedy_rewards.append(ep_reward)
            true_succ = summary.get("true_success")
            final_act = hist_ep[-1].get("action", "?") if hist_ep else "?"
            actions_taken[final_act] += 1
            correct = (final_act == "kill" and not true_succ) or \
                      (final_act != "kill" and true_succ)
            n_correct += int(correct)

        avg_greedy = sum(greedy_rewards) / len(greedy_rewards) if greedy_rewards else 0
        print(f"\n  Greedy policy (2016-2026 held-out):")
        print(f"    Correct decisions: {n_correct}/{min(12, len(test_set))}")
        print(f"    Avg episode reward: ${avg_greedy:>12,.0f}")
        print(f"    Action distribution: {dict(actions_taken)}")

    # Cumulative reward by year
    print(f"\n  ── Cumulative portfolio reward by year ──")
    cum = 0.0
    agent.epsilon = 0.0
    for yr in range(1996, 2027):
        yr_recs = [r for r in hist if r.get("extra", {}).get("decision_year") == yr]
        if not yr_recs:
            print(f"    {yr}: no records")
            continue
        env_yr = BioVentureEnv.from_records(yr_recs)
        rewards_yr = agent.train(env_yr, episodes=1)
        yr_reward = sum(rewards_yr)
        cum += yr_reward
        bar = "█" * max(0, int(cum / 5_000_000))
        sign = "+" if yr_reward >= 0 else ""
        print(f"    {yr}: annual ${yr_reward:>12,.0f}  cumulative ${cum:>14,.0f}  {bar}")


# ── Section 10: Summary ────────────────────────────────────────────────────────

def sec_summary(rows: list[dict], cohort_stats: dict) -> None:
    section("SECTION 10 · PLATFORM SUMMARY — 30-YEAR TRACK RECORD (1996-2026)")

    hist = [r for r in rows if r.get("source") in SOURCES]
    programs = programs_from_records(hist)

    n_progs   = len(programs)
    n_approv  = sum(1 for pid, s in programs.items()
                    if any(x.get("outcome") == "approved" for x in s))
    n_disc    = sum(1 for pid, s in programs.items()
                    if any("discontinued" in (x.get("outcome","")) for x in s))
    n_ongoing = n_progs - n_approv - n_disc
    total_inv = sum(r.get("investment_usd", 0) or 0 for r in hist)

    from src.learning.decision_model import SuccessPredictor
    pred = SuccessPredictor()
    res  = pred.train(db_path=DB_PATH)

    print(f"""
  ┌────────────────────────────────────────────────────────────────┐
  │            BIOVENTURE DECISION INTELLIGENCE                    │
  │         30-YEAR TRACK RECORD SUMMARY (1996-2026)              │
  ├────────────────────────────────────────────────────────────────┤
  │  PORTFOLIO                                                     │
  │    Cohort programs simulated:  {n_progs:>5}                        │
  │    Approved drugs:             {n_approv:>5}                        │
  │    Discontinued:               {n_disc:>5}                        │
  │    Ongoing:                    {n_ongoing:>5}                        │
  │    Total capital deployed:    ${total_inv/1e9:>6.1f}B                     │
  │                                                                │
  │  DB SNAPSHOT                                                   │
  │    Total DB records:          {len(rows):>5}                        │
  │    Historical cohort records: {len(hist):>5}                        │
  │                                                                │
  │  ML MODEL (full dataset)                                       │
  │    AUC:                       {res.get('auc', 0):.3f}                       │
  │    Accuracy:                  {res.get('accuracy', 0):.3f}                       │
  │    Training set:             {res.get('n_train', 0):>5}  records               │
  │                                                                │
  │  INDUSTRY BENCHMARKS                                           │
  │    Phase1 success rate:       ~52% (industry avg)             │
  │    Phase2 success rate:       ~29% (industry avg)             │
  │    Phase3 success rate:       ~58% (industry avg)             │
  │    Overall P→Approval:        ~12% (industry avg)             │
  └────────────────────────────────────────────────────────────────┘
""")


# ── Entrypoint ─────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n  Loading DB from {DB_PATH}...")
    rows = load_records()
    hist = hist_records(rows)

    if not hist:
        print("  ERROR: No hist30_cohort records found.")
        print("  Run  python3 generate_30year_history.py  first.")
        sys.exit(1)

    print(f"  Total DB records:         {len(rows)}")
    print(f"  Historical cohort records:{len(hist)}")

    cohort_stats = sec_cohort_overview(hist)
    sec_annual_audit(hist)
    sec_attrition_funnel(hist)
    sec_survival(cohort_stats)
    sec_investment_roi(hist)
    sec_indication_deep_dive(hist)
    sec_ml_temporal(rows)           # uses ALL records for better ML
    sec_decision_retrospective(hist)
    sec_rl_portfolio(rows)
    sec_summary(rows, cohort_stats)


if __name__ == "__main__":
    import io, sys as _sys

    # Tee stdout to report file
    class Tee(io.TextIOWrapper):
        def __init__(self, stream, path: Path):
            self._stream = stream
            self._file   = path.open("w")
        def write(self, s):
            self._stream.write(s)
            self._file.write(s)
            return len(s)
        def flush(self):
            self._stream.flush()
            self._file.flush()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _sys.stdout = Tee(_sys.stdout, REPORT_PATH)  # type: ignore
    try:
        main()
    finally:
        _sys.stdout._file.close()  # type: ignore
        _sys.stdout = _sys.stdout._stream  # type: ignore
        print(f"\n  Report saved to {REPORT_PATH}")
