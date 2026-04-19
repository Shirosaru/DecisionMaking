from __future__ import annotations

import math
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..storage.repository import fetch_all, summary_by_stage


def _safe_rate(num: int, den: int) -> float:
    return round(num / den, 4) if den > 0 else 0.0


# ── Portfolio-level summary ───────────────────────────────────────────────────

def portfolio_summary(db_path: Path | None = None) -> dict[str, Any]:
    kwargs = {"db_path": db_path} if db_path else {}
    rows = fetch_all(**kwargs)
    total = len(rows)
    if total == 0:
        return {"total": 0}

    decisions = Counter(r["decision"] for r in rows)
    outcomes = Counter(r["outcome"] for r in rows)
    sources = Counter(r["source"] for r in rows)
    stages = Counter(r["clinical_stage"] for r in rows)

    invested = sum(r["investment_usd"] for r in rows if r["investment_usd"])
    kill_rate = _safe_rate(decisions.get("no-go", 0), total)

    return {
        "total_projects": total,
        "kill_rate": kill_rate,
        "decisions": dict(decisions),
        "outcomes": dict(outcomes),
        "sources": dict(sources),
        "stages": dict(stages),
        "total_investment_usd": round(invested, 2),
        "avg_investment_usd": round(invested / total, 2) if total else 0,
    }


# ── Kill rate analysis ────────────────────────────────────────────────────────

def kill_rates_by_stage(db_path: Path | None = None) -> list[dict[str, Any]]:
    kwargs = {"db_path": db_path} if db_path else {}
    rows = fetch_all(**kwargs)

    by_stage: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_stage[r["clinical_stage"]].append(r["decision"])

    result = []
    for stage, decisions in sorted(by_stage.items()):
        total = len(decisions)
        kills = decisions.count("no-go")
        result.append({
            "stage": stage,
            "total": total,
            "kills": kills,
            "kill_rate": _safe_rate(kills, total),
        })
    return result


def kill_rates_by_indication(db_path: Path | None = None) -> list[dict[str, Any]]:
    kwargs = {"db_path": db_path} if db_path else {}
    rows = fetch_all(**kwargs)

    by_ind: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_ind[r["indication"]].append(r["decision"])

    result = []
    for ind, decisions in sorted(by_ind.items(), key=lambda x: -len(x[1])):
        total = len(decisions)
        kills = decisions.count("no-go")
        result.append({
            "indication": ind,
            "total": total,
            "kills": kills,
            "kill_rate": _safe_rate(kills, total),
        })
    return result[:30]   # top 30


# ── Phase transition probability estimation ───────────────────────────────────

def phase_transition_probabilities(db_path: Path | None = None) -> dict[str, float]:
    """
    Compute empirical P(advance | stage) from outcomes in DB.
    Discontinued = failed, approved/ongoing = advance.
    """
    kwargs = {"db_path": db_path} if db_path else {}
    rows = fetch_all(**kwargs)

    stage_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"advance": 0, "kill": 0})
    for r in rows:
        stage = r["clinical_stage"]
        outcome = r["outcome"]
        if "discontinued" in outcome:
            stage_counts[stage]["kill"] += 1
        elif outcome in ("approved", "ongoing"):
            stage_counts[stage]["advance"] += 1

    result = {}
    for stage, counts in stage_counts.items():
        total = counts["advance"] + counts["kill"]
        result[stage] = _safe_rate(counts["advance"], total)
    return result


# ── Cost-of-kill analysis ─────────────────────────────────────────────────────

def cost_of_kills(db_path: Path | None = None) -> dict[str, Any]:
    kwargs = {"db_path": db_path} if db_path else {}
    rows = fetch_all(**kwargs)

    killed_rows = [r for r in rows if r["decision"] == "no-go" and r["investment_usd"]]
    if not killed_rows:
        return {"total_killed_usd": 0, "avg_killed_usd": 0, "count": 0}

    total = sum(r["investment_usd"] for r in killed_rows)
    return {
        "total_killed_usd": round(total, 2),
        "avg_killed_usd": round(total / len(killed_rows), 2),
        "count": len(killed_rows),
    }


# ── Source quality audit ──────────────────────────────────────────────────────

def source_quality_audit(db_path: Path | None = None) -> list[dict[str, Any]]:
    kwargs = {"db_path": db_path} if db_path else {}
    rows = fetch_all(**kwargs)

    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r)

    result = []
    for source, recs in sorted(by_source.items()):
        n = len(recs)
        has_stage = sum(1 for r in recs if r["clinical_stage"] not in ("unknown",))
        has_invest = sum(1 for r in recs if r["investment_usd"] and r["investment_usd"] > 0)
        has_outcome = sum(1 for r in recs if r["outcome"] not in ("unknown", "ongoing"))
        result.append({
            "source": source,
            "n": n,
            "pct_with_stage": _safe_rate(has_stage, n),
            "pct_with_investment": _safe_rate(has_invest, n),
            "pct_with_outcome": _safe_rate(has_outcome, n),
            "completeness_score": _safe_rate(has_stage + has_invest + has_outcome, n * 3),
        })
    return sorted(result, key=lambda x: -x["completeness_score"])


# ── Text reporting ─────────────────────────────────────────────────────────────

def print_report(db_path: Path | None = None) -> None:
    kwargs = {"db_path": db_path} if db_path else {}
    summary = portfolio_summary(**kwargs)
    stages = kill_rates_by_stage(**kwargs)
    transitions = phase_transition_probabilities(**kwargs)
    kill_cost = cost_of_kills(**kwargs)
    source_audit = source_quality_audit(**kwargs)

    print("\n" + "=" * 62)
    print("  BIOVENTURE DECISION INTELLIGENCE — ANALYSIS REPORT")
    print("=" * 62)

    print(f"\nTotal projects:      {summary['total_projects']}")
    print(f"Overall kill rate:   {summary['kill_rate']:.1%}")
    print(f"Total investment:    ${summary['total_investment_usd']:,.0f}")
    print(f"Sources covered:     {len(summary.get('sources', {}))}")

    print("\n── Kill rates by stage ──")
    for s in stages:
        bar = "█" * int(s["kill_rate"] * 20)
        print(f"  {s['stage']:20s}  {s['kill_rate']:.1%}  ({s['kills']}/{s['total']})  {bar}")

    print("\n── Empirical phase transition probabilities ──")
    for stage, p in sorted(transitions.items()):
        print(f"  {stage:20s}  {p:.1%}")

    print(f"\n── Cost of kills ──")
    print(f"  Total invested in killed projects:  ${kill_cost['total_killed_usd']:,.0f}")
    print(f"  Average cost per kill:              ${kill_cost['avg_killed_usd']:,.0f}")
    print(f"  Number of kill events:              {kill_cost['count']}")

    print("\n── Source quality audit ──")
    print(f"  {'Source':<28} {'n':>5}  {'Stage%':>7}  {'Invest%':>8}  {'Outcome%':>9}  {'Score':>6}")
    for s in source_audit:
        print(f"  {s['source']:<28} {s['n']:>5}  {s['pct_with_stage']:>7.1%}  "
              f"{s['pct_with_investment']:>8.1%}  {s['pct_with_outcome']:>9.1%}  "
              f"{s['completeness_score']:>6.1%}")

    print()


# ══════════════════════════════════════════════════════════════════════════════
# Cross-modality historical failure lesson engine
# ══════════════════════════════════════════════════════════════════════════════

# Tox-signal keywords (for kill classification)
_TOX_RE = re.compile(
    r"(toxic(?:ity)?|adverse event|safety concern|dose.limit(?:ing)?|DLT\b|MTD\b"
    r"|hepatotox|hepatic (?:adverse|event|tox)|liver tox|ALT.*elev|AST.*elev"
    r"|cardiotox|cardiac (?:adverse|event|tox)|QT(?:c)?.?prolong|cardiomyopathy"
    r"|ILD\b|interstitial lung|pneumonitis|pulmonary tox"
    r"|CRS\b|cytokine release|cytokine storm"
    r"|ICANS\b|neurotox|neuropath|encephalopathy"
    r"|myelosuppression|thrombocytopen|neutropenia|cytopenias?"
    r"|off.target|on.target off.tumor|normal tissue (?:tox|damage)"
    r"|fatal|treatment.related death|serious adverse)",
    re.IGNORECASE,
)
_EFF_RE = re.compile(
    r"(no clinical benefit|futility|primary endpoint.*not met|did not meet.*endpoint"
    r"|no significant.*efficacy|lack.*efficacy|insufficient.*response|no response"
    r"|failed to demonstrate|no meaningful)",
    re.IGNORECASE,
)
# Specific toxicity sub-patterns
_SPECIFIC_TOX: dict[str, re.Pattern] = {
    "CRS / cytokine storm":     re.compile(r"(CRS\b|cytokine release|cytokine storm)", re.IGNORECASE),
    "ILD / pneumonitis":        re.compile(r"(ILD\b|interstitial lung|pneumonitis|pulmonary tox)", re.IGNORECASE),
    "hepatotoxicity":           re.compile(r"(hepatotox|liver tox|ALT.*elev|AST.*elev|hepatic)", re.IGNORECASE),
    "cardiotoxicity / QTc":     re.compile(r"(cardiotox|QT(?:c)?.?prolong|cardiomyopathy|cardiac tox)", re.IGNORECASE),
    "neurotox / ICANS":         re.compile(r"(ICANS\b|neurotox|neuropath|encephalopathy|seizure.*tox)", re.IGNORECASE),
    "myelosuppression":         re.compile(r"(myelosuppression|thrombocytopen|neutropenia|cytopenias?)", re.IGNORECASE),
    "off-target / on-target off-tumour": re.compile(r"(off.target|on.target off.tumor|normal tissue tox|bystander tox)", re.IGNORECASE),
}

# ── Ancestry map: newer/conditional format → ancestor formats to learn from ──

MODALITY_ANCESTRY: dict[str, list[str]] = {
    # Conditional antibody formats learn from unmasked mAb history
    "probody":         ["igg_full"],
    "ph_switch_ab":    ["igg_full"],
    # T-cell engager hierarchy
    "hle_bite":        ["bite_format"],
    "masked_tce":      ["bite_format", "hle_bite"],
    "cobra_bispec":    ["bite_format", "hle_bite", "masked_tce"],
    # Conditional ADC inherits from both ADC classes
    "probody_dc":      ["adc_noncleavable", "adc_cleavable", "probody"],
    "adc_cleavable":   ["adc_noncleavable"],          # cleavable design explicitly fixed non-cleavable tox
    # Logic-gated / next-gen CAR-T
    "dual_logic_car":  ["autologous_car"],
    "synnotch_car":    ["autologous_car", "dual_logic_car"],
    "adapter_car":     ["autologous_car"],
    "not_gate_car":    ["autologous_car"],
    "split_car":       ["autologous_car", "adapter_car"],
    "truck_car":       ["autologous_car"],
    "allogeneic_car":  ["autologous_car"],
    # Precision gene editing learns from delivery vehicle history
    "base_editing":    ["aav_vector", "lentiviral_vec"],
    "prime_editing":   ["aav_vector", "lentiviral_vec", "base_editing"],
    # Hypoxia prodrug learns from the covalent/alkylating SM history
    "hypoxia_act":     ["covalent_sm", "oral_sm"],
    # Bispecific formats learn from foundational IgG
    "crossmab_kih":    ["igg_full"],
    "dart_format":     ["bite_format"],
}

# ── How risk transfers from ancestor to descendant ────────────────────────────
# Keys: (child_format, ancestor_format) — value: transfer note
_RISK_TRANSFER: dict[tuple[str, str], str] = {
    # TCE hierarchy
    ("hle_bite",    "bite_format"):  "Half-life extension does not reduce CRS/neurotox mechanistically — same CD3 engagement, longer exposure window actually increases cumulative cytokine burden; clinical data (mosunetuzumab, glofitamab) confirms CRS persists.",
    ("masked_tce",  "bite_format"):  "Masking reduces systemic T-cell engagement; BiTE CRS track record sets upper bound — your worst case if mask fails. Cardiotox seen in BiTE discontinuations likely driven by cytokine-mediated cardiac injury and persists if on-tumour T-cell activation is strong.",
    ("masked_tce",  "hle_bite"):     "Half-life-extended format failures directly precedent masked TCE design space — same molecular scaffold, CRS attenuation from masking alone is insufficient without high mask selectivity.",
    ("cobra_bispec","bite_format"):  "COBRA/latent bispecifics were designed specifically to address BiTE CRS failures. BiTE DB shows cardiotox and off-target T-cell activation as dominant kill reasons. COBRA protease-gating reduces but cannot eliminate on-tumour CRS. Key lesson from BiTE: step-up dosing is non-negotiable regardless of conditional format.",
    ("cobra_bispec","hle_bite"):     "HLE-BiTE CRS track record (high recent failure signal) is the most current precedent for COBRA. Very recent discontinuations in this class are the most informative — check for CRS grade 3+ at dose levels you are targeting.",
    ("cobra_bispec","masked_tce"):   "Masked TCE and COBRA occupy the same design space; any mask shedding in circulation restores full BiTE-like T-cell engagement and BiTE-level CRS risk.",
    # ADC hierarchy
    ("adc_cleavable",   "adc_noncleavable"): "Cleavable linker design was directly motivated by non-cleavable ADC payload-driven tox (DM1 neurotox, myelosuppression). DB shows 62% tox-kill in non-cleavable ADC. Cleavable linkers reduce systemic payload release but do NOT eliminate: (1) bystander effect toxicity from released payload in tumour, (2) ILD from DXd/deruxtecan payload (independent of linker).",
    ("probody_dc",  "adc_noncleavable"): "Non-cleavable ADC 62% tox-kill rate is the baseline risk Probody Drug Conjugates are designed to overcome. Masking adds one selectivity layer but the payload chemistry is identical — ILD, myelosuppression, and neuropathy risks are fully inherited once the PDC is activated.",
    ("probody_dc",  "adc_cleavable"):    "Cleavable ADC DB shows ILD and haematological tox as dominant signals even with cleavable linker. PDC masking on top reduces systemic activation but DXd/MMAE profile is not changed — same organ tox pattern once unmasked on-tumour.",
    ("probody_dc",  "probody"):          "Probody antibody failures show incomplete mask shedding as primary risk. PDC compounds this with a second mask on the payload arm — dual-mask failure modes have compounded probability; validate both masks independently in plasma stability assays.",
    # CAR-T hierarchy
    ("allogeneic_car", "autologous_car"): "Allogeneic CAR-T inherits all autologous CRS/ICANS risks and adds GvHD risk from alloreactive donor T cells. DB autologous CAR discontinuations are the floor — allogeneic clinical attrition has been higher historically.",
    ("dual_logic_car", "autologous_car"): "AND-gate CAR attenuates but does not eliminate CRS/ICANS. Autologous CAR DB precedent defines the upper bound. Key lesson: even OR-gate bystander killing (when only one antigen is present) can cause on-target/off-tumour toxicity — validate both antigens' normal tissue expression independently.",
    ("synnotch_car",   "autologous_car"): "SynNotch circuit requires tumour priming step (antigen A → CAR expression → antigen B killing). If antigen A is present on normal tissue, priming occurs systemically. Autologous CAR tox DB shows ICANS and prolonged cytopenia — these risks persist unless priming antigen is strictly tumour-specific.",
    ("synnotch_car", "dual_logic_car"):   "Dual CAR DB precedent shows logic-gating alone is insufficient to prevent neurotox in CNS-adjacent tumours. SynNotch requires even more rigorous priming-antigen tissue selectivity validation.",
    ("split_car",    "autologous_car"):   "Split/CID-CAR CRS risk is physician-titratable (stop CID → CAR off), which is the key safety improvement over standard CAR. Autologous CAR DB tox precedent defines the on-state risk. Key lesson: plan physician-controlled drug holiday protocols upfront for grade ≥2 CRS.",
    ("split_car",    "adapter_car"):      "Adapter CAR and split-CAR share the modular T-cell redirect concept. Adapter CAR DB discontinuations show that linker/bridge molecule loss of control is a real failure mode — for split-CAR, validate rapamycin dose-response for T-cell activation with tight PK/PD model.",
    ("truck_car",    "autologous_car"):   "TRUCK/armored CAR adds cytokine secretion to standard CAR, increasing local inflammation. Autologous CAR DB CRS and ICANS risks are inherited and potentially amplified by the secreted cytokine payload. DB lessons: grade 3+ cytokine events in standard autologous CAR were dose-limiting — armored format likely moves this threshold lower.",
    # Gene therapy
    ("base_editing", "aav_vector"):       "AAV DB shows immune response to capsid (dorsal root ganglion tox, hepatotox with systemic AAV) as primary kill drivers. Base editors delivered via AAV inherit full capsid immunogenicity risk — additionally, base editor off-target DNA edits are a new risk not present in AAV gene replacement therapy.",
    ("base_editing", "lentiviral_vec"):   "Lentiviral DB shows insertional mutagenesis and genotoxicity as the key safety concern that ended SIN-LV programs. Base editing is non-integrating (if delivered transiently), which mitigates this specific risk, but compartment-limited delivery still requires full AAV/LV biodistribution profiling.",
    ("prime_editing","aav_vector"):       "Same AAV capsid risk as base editing, plus larger pegRNA cargo creates packaging constraints. AAV DB failures at high dose inform prime editing dose ceiling — large construct packaging forces dual-AAV split designs, doubling the capsid immunogenicity exposure.",
    ("prime_editing","base_editing"):     "Base editing DB shows off-target C→T or A→G edits at near-cognate PAM sites as key concern. Prime editing was designed to address this but pegRNA secondary structure and reverse transcriptase off-targets are a new risk class; monitor both editing precision AND RT infidelity.",
    # Hypoxia
    ("hypoxia_act",  "covalent_sm"):      "Covalent SM DB shows off-target covalent binding and hepatotox as primary kill reasons. HAP released warheads are covalent alkylating agents — same off-target covalent tox applies if hypoxia selectivity is incomplete. Normal-tissue hypoxia (bone marrow, wound healing) activates the same cytotoxic mechanism.",
    ("hypoxia_act",  "oral_sm"):          "Oral SM DB failures include systemic hepatotox and cardiotox. HAP prodrugs reduce but do not eliminate systemic drug exposure — partial reduction in normal-tissue activation still exposes patients to the same organ tox class as the parent cytotoxin.",
    # Bispecific IgG formats
    ("crossmab_kih", "igg_full"):         "CrossMAb/KiH bispecifics inherit full IgG1 Fc effector function toxicity. IgG DB shows ILD (anti-VEGF-adjacent ADRs), cardiotox in checkpoint combinations, and infusion reactions. CrossMAb assembly mismatch (chain swapping) during manufacturing adds a unique protein QC failure mode not present in monospecifics.",
    ("dart_format",  "bite_format"):      "DART/ADAPTIR shares T-cell redirection mechanism with BiTE. BiTE DB cardiotox and CRS findings directly transfer. DART's longer half-life vs first-gen BiTE is mechanistically identical to HLE-BiTE from a PK/safety standpoint — step-dose ramp and CRS monitoring protocol required.",
}

# ── Cached failure stats ───────────────────────────────────────────────────────
_FAILURE_STATS_CACHE: dict[str, dict[str, Any]] = {}
_FAILURE_CACHE_LOCK = threading.Lock()


def _build_failure_stats(db_path: Path | None = None) -> dict[str, Any]:
    """
    Scan all DB records once, classify discontinued records by detected format,
    and return empirical tox-kill / efficacy-kill stats per format.
    Cached per db_path string key.
    """
    from ..processors.feature_extractor import detect_formats

    cache_key = str(db_path) if db_path else "__default__"
    with _FAILURE_CACHE_LOCK:
        if cache_key in _FAILURE_STATS_CACHE:
            return _FAILURE_STATS_CACHE[cache_key]

    kwargs: dict = {"db_path": db_path} if db_path else {}
    rows = fetch_all(**kwargs)

    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "total_discontinued": 0,
        "tox_kill": 0,
        "eff_kill": 0,
        "ambiguous": 0,
        "specific_tox": defaultdict(int),
        "example_texts": [],
    })

    for row in rows:
        if "discontinued" not in (row.get("outcome") or ""):
            continue
        text = row.get("raw_text") or ""
        fmts = detect_formats(text)
        if not fmts:
            continue

        is_tox = bool(_TOX_RE.search(text))
        is_eff = bool(_EFF_RE.search(text))

        for fmt in fmts:
            s = stats[fmt]
            s["total_discontinued"] += 1
            if is_tox and not is_eff:
                s["tox_kill"] += 1
            elif is_eff and not is_tox:
                s["eff_kill"] += 1
            else:
                s["ambiguous"] += 1
            for tox_name, pattern in _SPECIFIC_TOX.items():
                if pattern.search(text):
                    s["specific_tox"][tox_name] += 1
            if len(s["example_texts"]) < 3 and is_tox and len(text) > 40:
                s["example_texts"].append(text[:200])

    # Freeze inner defaultdicts
    for fmt in stats:
        stats[fmt]["specific_tox"] = dict(stats[fmt]["specific_tox"])

    result = dict(stats)
    with _FAILURE_CACHE_LOCK:
        _FAILURE_STATS_CACHE[cache_key] = result
    return result


def cross_modality_lessons(
    fmt_classes: list[str],
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """
    For each detected format, find its ancestor formats, mine their discontinuation
    records from the DB, and return actionable lessons with empirical backing.

    Each lesson:
      ancestor        — the format the lesson is drawn from
      child_format    — the format being evaluated
      n_discontinued  — total discontinued records found for ancestor
      tox_kill_rate   — fraction of discontinued records with tox signal
      top_toxicities  — ranked list of specific toxicity types observed
      transfer_note   — how the ancestor risk applies to the child format
      severity        — "high" / "moderate" / "low" based on tox_kill_rate
    """
    if not fmt_classes:
        return []

    failure_stats = _build_failure_stats(db_path)

    lessons: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()   # avoid duplicate child/ancestor pairs

    for fmt in fmt_classes:
        ancestors = MODALITY_ANCESTRY.get(fmt, [])
        for anc in ancestors:
            if (fmt, anc) in seen:
                continue
            seen.add((fmt, anc))

            s = failure_stats.get(anc)
            if not s or s["total_discontinued"] == 0:
                # No DB data for this ancestor — still emit the structural note if available
                transfer = _RISK_TRANSFER.get((fmt, anc))
                if transfer:
                    lessons.append({
                        "ancestor":        anc,
                        "child_format":    fmt,
                        "n_discontinued":  0,
                        "tox_kill_rate":   None,
                        "eff_kill_rate":   None,
                        "top_toxicities":  [],
                        "transfer_note":   transfer,
                        "severity":        "unknown — no ancestor data in DB",
                        "data_quality":    "structural_only",
                    })
                continue

            n   = s["total_discontinued"]
            tk  = s["tox_kill"]
            ek  = s["eff_kill"]
            tox_rate = _safe_rate(tk, n)
            eff_rate = _safe_rate(ek, n)

            # Severity from tox kill rate
            if tox_rate >= 0.35:
                severity = "HIGH — dominant tox-driven attrition in ancestor class"
            elif tox_rate >= 0.15:
                severity = "MODERATE — meaningful tox contribution in ancestor class"
            elif tox_rate > 0:
                severity = "LOW — some tox signals in ancestor class"
            else:
                severity = "NOT DETECTED — no tox-classified kills in ancestor DB records"

            top_tox = sorted(s["specific_tox"].items(), key=lambda x: -x[1])

            lessons.append({
                "ancestor":        anc,
                "child_format":    fmt,
                "n_discontinued":  n,
                "tox_kill_rate":   round(tox_rate, 3),
                "eff_kill_rate":   round(eff_rate, 3),
                "top_toxicities":  [{"type": t, "count": c} for t, c in top_tox if c > 0],
                "transfer_note":   _RISK_TRANSFER.get((fmt, anc),
                    f"Risk inheritance from {anc} → {fmt}: review tox-kill records above for direct precedent."),
                "severity":        severity,
                "data_quality":    "empirical" if n >= 5 else "limited_data",
            })

    # Sort: highest tox_kill_rate first (None / unknown last)
    lessons.sort(key=lambda x: -(x["tox_kill_rate"] or -1))
    return lessons


# ── Modality / format group mapping ──────────────────────────────────────────

_FORMAT_MODALITY_GROUP: dict[str, str] = {
    # Standard antibody
    "igg_full":        "Antibody",
    "nanobody":        "Antibody",
    "fab_scfv":        "Antibody",
    "fc_fusion":       "Antibody",
    "fc_engineered":   "Antibody",
    # Conditional antibody
    "probody":         "Antibody — Conditional",
    "ph_switch_ab":    "Antibody — Conditional",
    # Bispecific
    "bite_format":     "Bispecific / T-cell engager",
    "hle_bite":        "Bispecific / T-cell engager",
    "crossmab_kih":    "Bispecific / T-cell engager",
    "dart_format":     "Bispecific / T-cell engager",
    # Conditional bispecific
    "masked_tce":      "Bispecific — Conditional",
    "cobra_bispec":    "Bispecific — Conditional",
    # ADC
    "adc_cleavable":   "ADC",
    "adc_noncleavable": "ADC",
    # Conditional ADC
    "probody_dc":      "ADC — Conditional",
    # CAR-T
    "autologous_car":  "CAR-T",
    "allogeneic_car":  "CAR-T",
    # Logic-gated / next-gen CAR-T
    "dual_logic_car":  "CAR-T — Logic-gated",
    "synnotch_car":    "CAR-T — Logic-gated",
    "adapter_car":     "CAR-T — Logic-gated",
    "not_gate_car":    "CAR-T — Logic-gated",
    "split_car":       "CAR-T — Logic-gated",
    "truck_car":       "CAR-T — Armored / TRUCK",
    # Small molecule
    "covalent_sm":     "Small Molecule",
    "macrocycle":      "Small Molecule",
    "allosteric_sm":   "Small Molecule",
    "oral_sm":         "Small Molecule",
    # RNA / oligo
    "galnac_rnai":     "RNA / Oligonucleotide",
    "splice_switch":   "RNA / Oligonucleotide",
    "circular_rna":    "RNA / Oligonucleotide",
    # Gene therapy
    "aav_vector":      "Gene Therapy",
    "lentiviral_vec":  "Gene Therapy",
    "base_editing":    "Gene Therapy — Precision",
    "prime_editing":   "Gene Therapy — Precision",
    # Delivery
    "subcutaneous":    "Delivery",
    "pegylated":       "Delivery",
    "nanoparticle":    "Delivery",
    # Hypoxia / stimulus-responsive
    "hypoxia_act":     "Conditional Activation — Microenvironment",
}

_CONDITIONAL_FORMATS = frozenset({
    "probody", "ph_switch_ab", "masked_tce", "cobra_bispec", "probody_dc",
    "dual_logic_car", "synnotch_car", "adapter_car", "not_gate_car", "split_car",
    "hypoxia_act",
})


def _record_year(r: dict) -> int | None:
    """Best-effort year extraction from any record in the DB."""
    extra = r.get("extra") or {}
    if extra.get("year"):
        try:
            return int(extra["year"])
        except (ValueError, TypeError):
            pass
    if extra.get("first_approval"):
        try:
            return int(extra["first_approval"])
        except (ValueError, TypeError):
            pass
    if extra.get("pubdate"):
        m = re.search(r"(20\d\d)", str(extra["pubdate"]))
        if m:
            return int(m.group(1))
    sid = r.get("source_id") or ""
    if sid.startswith("NCT"):
        m = re.match(r"NCT(20\d\d)", sid)
        if m:
            return int(m.group(1))
    return None


# ── Modality trend report ─────────────────────────────────────────────────────

def modality_trend_report(
    db_path: Path | None = None,
    top_n: int = 10,
    min_records: int = 3,
    recent_from: int = 2022,
    baseline_from: int = 2017,
) -> dict[str, Any]:
    """
    Scan all DB records, detect molecular formats, and return:
      - hot_formats:      highest combined volume × recency activity
      - emerging_formats: highest recent acceleration vs prior baseline (next new thing)
      - by_modality:      per-modality-group ranking

    Heat score  = recent_5yr_count × (recent_5yr / total_dated + 0.1)
    Emergence   = (recent_5yr - baseline_5yr) / (baseline_5yr + early_count + 1)
                  scaled by log(recent_5yr + 1) to require some real volume
    """
    from ..processors.feature_extractor import detect_formats

    kwargs: dict = {"db_path": db_path} if db_path else {}
    rows = fetch_all(**kwargs)

    fmt_total:    Counter = Counter()
    fmt_recent:   Counter = Counter()    # recent_from … 2026
    fmt_baseline: Counter = Counter()    # baseline_from … recent_from-1
    fmt_early:    Counter = Counter()    # before baseline_from
    fmt_stages:   dict[str, Counter] = defaultdict(Counter)
    fmt_outcomes: dict[str, Counter] = defaultdict(Counter)

    for row in rows:
        text = row.get("raw_text") or ""
        fmts = detect_formats(text)
        if not fmts:
            continue
        year  = _record_year(row)
        stage   = row.get("clinical_stage", "unknown") or "unknown"
        outcome = row.get("outcome",         "unknown") or "unknown"

        for fmt in fmts:
            fmt_total[fmt] += 1
            fmt_stages[fmt][stage] += 1
            fmt_outcomes[fmt][outcome] += 1
            if year:
                if year >= recent_from:
                    fmt_recent[fmt] += 1
                elif year >= baseline_from:
                    fmt_baseline[fmt] += 1
                else:
                    fmt_early[fmt] += 1

    entries: list[dict] = []
    for fmt, total in fmt_total.items():
        if total < min_records:
            continue
        recent   = fmt_recent.get(fmt, 0)
        baseline = fmt_baseline.get(fmt, 0)
        early    = fmt_early.get(fmt, 0)
        dated    = recent + baseline + early

        # Heat: volume × recency fraction
        recency_frac = recent / dated if dated > 0 else 0.0
        heat_score   = round(recent * (recency_frac + 0.1), 2)

        # Emergence: recent acceleration weighted by log-volume (needs real evidence)
        denom           = baseline + early + 1
        raw_velocity    = (recent - baseline) / denom
        emergence_score = round(raw_velocity * math.log1p(recent), 3)

        # Success signal
        outcomes   = dict(fmt_outcomes[fmt])
        n_approved = outcomes.get("approved", 0)
        n_ongoing  = outcomes.get("ongoing", 0)
        n_disc     = sum(v for k, v in outcomes.items() if "discontinued" in k)
        denom_s    = n_approved + n_ongoing + n_disc
        success_rate = _safe_rate(n_approved + n_ongoing, denom_s) if denom_s > 0 else None

        entries.append({
            "format":           fmt,
            "modality_group":   _FORMAT_MODALITY_GROUP.get(fmt, "Other"),
            "is_conditional":   fmt in _CONDITIONAL_FORMATS,
            "total_records":    total,
            "recent_5yr":       recent,
            "baseline_5yr":     baseline,
            "heat_score":       heat_score,
            "velocity":         round(raw_velocity, 3),
            "emergence_score":  emergence_score,
            "success_rate":     success_rate,
            "stage_distribution": dict(fmt_stages[fmt]),
        })

    hot      = sorted(entries, key=lambda x: -x["heat_score"])[:top_n]
    emerging = sorted(entries, key=lambda x: -x["emergence_score"])[:top_n]

    by_modality: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_modality[e["modality_group"]].append(e)
    by_modality_ranked = {
        grp: sorted(items, key=lambda x: -x["heat_score"])
        for grp, items in sorted(by_modality.items())
    }

    return {
        "hot_formats":            hot,
        "emerging_formats":       emerging,
        "by_modality":            by_modality_ranked,
        "total_formats_detected": len(entries),
        "total_records_analysed": len(rows),
        "params": {
            "recent_window":   f"{recent_from}–2026",
            "baseline_window": f"{baseline_from}–{recent_from - 1}",
        },
    }


def print_trend_report(db_path: Path | None = None, top_n: int = 10) -> None:
    """Print a concise trend intelligence report to stdout."""
    report = modality_trend_report(db_path=db_path, top_n=top_n)

    W = 70
    print("\n" + "=" * W)
    print("  BIOVENTURE — MODALITY TREND INTELLIGENCE")
    print("=" * W)
    print(f"  Records analysed: {report['total_records_analysed']:,}   "
          f"Formats with signal: {report['total_formats_detected']}")
    print(f"  Recent window:    {report['params']['recent_window']}   "
          f"Baseline:  {report['params']['baseline_window']}")

    # ── HOT (high volume + high recency)
    print(f"\n{'─' * W}")
    print("  🔥  HOT NOW  —  highest activity in recent years")
    print(f"{'─' * W}")
    _fmt_table(report["hot_formats"])

    # ── EMERGING (accelerating from low base)
    print(f"\n{'─' * W}")
    print("  🚀  NEXT NEW THING  —  fastest-rising from low base")
    print(f"{'─' * W}")
    _fmt_table(report["emerging_formats"])

    # ── PER-MODALITY BREAKDOWN
    print(f"\n{'─' * W}")
    print("  📊  BY MODALITY GROUP")
    print(f"{'─' * W}")
    for grp, items in report["by_modality"].items():
        top_item = items[0]
        cond_flag = " [conditional]" if top_item["is_conditional"] else ""
        print(f"\n  {grp}")
        for i in items[:3]:
            bar   = "█" * min(int(i["heat_score"] / 2), 20)
            badge = "↑↑" if i["emergence_score"] > 0.5 else ("↑" if i["emergence_score"] > 0.1 else "  ")
            sr    = f"  success={i['success_rate']:.0%}" if i["success_rate"] is not None else ""
            print(f"    {badge} {i['format']:<22}  heat={i['heat_score']:>6.1f}  "
                  f"n={i['total_records']:>4}  recent={i['recent_5yr']:>3}{sr}  {bar}")
    print()


def _fmt_table(items: list[dict]) -> None:
    print(f"  {'#':<3}  {'Format':<25}  {'Group':<32}  "
          f"{'Heat':>6}  {'n':>5}  {'Recent':>6}  {'Velocity':>8}  {'Success':>8}")
    for rank, item in enumerate(items, 1):
        sr = f"{item['success_rate']:.0%}" if item["success_rate"] is not None else "  n/a"
        cnd = "*" if item["is_conditional"] else " "
        print(f"  {rank:<3}  {cnd}{item['format']:<24}  {item['modality_group']:<32}  "
              f"{item['heat_score']:>6.1f}  {item['total_records']:>5}  "
              f"{item['recent_5yr']:>6}  {item['velocity']:>+8.3f}  {sr:>8}")
