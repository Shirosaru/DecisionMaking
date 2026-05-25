#!/usr/bin/env python3
"""
train_and_report.py
═══════════════════
1. Parse all downloaded portfolio materials from data/slides/portfolio/
2. Build RawRecord objects and upsert them into the DB as source="vc_portfolio_v2"
3. Train SuccessPredictor on the full real-world DB
4. Run explain() for every company
5. Write data/portfolio_training_report.html — full analysis + model internals

Run:
    python train_and_report.py
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from src.collectors.base_collector import RawRecord
from src.learning.decision_model import SuccessPredictor
from src.processors.feature_extractor import extract_features, characterize
from src.storage.repository import bulk_upsert, fetch_all

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

PORTFOLIO_DIR = Path("data/slides/portfolio")
REPORT_OUT    = Path("data/portfolio_training_report.html")
DB_SOURCE     = "vc_portfolio_v2"

# ── Company catalog ────────────────────────────────────────────────────────────
# outcome_hint: "approved" | "acquired" | "failed_p3" | "failed_p2" | "active"
PORTFOLIO: list[dict] = [
    # ── 3E BioVentures ─────────────────────────────────────────────────────────
    dict(firm="3E BioVentures", slug="aravive", name="Aravive", ticker="ARAV",
         drug="Batiraxcept (AVB-S6-500)", mechanism="AXL/GAS6-axis decoy protein (anti-metastatic)",
         indication="oncology", outcome_hint="failed_p3"),
    dict(firm="3E BioVentures", slug="oncoimmune", name="OncoImmune", ticker=None,
         drug="CD24Fc (efprezimod alfa / MK-7110)",
         mechanism="CD24-SiglecG/10 innate immune checkpoint fusion protein",
         indication="immunology", outcome_hint="failed_p3"),
    dict(firm="3E BioVentures", slug="c4_therapeutics", name="C4 Therapeutics", ticker=None,
         drug="Cemsidomide (CFT7455)", mechanism="IKZF1/3 molecular glue protein degrader (CELMoD)",
         indication="oncology", outcome_hint="active"),
    dict(firm="3E BioVentures", slug="cognition_therapeutics", name="Cognition Therapeutics", ticker=None,
         drug="CT1812", mechanism="Sigma-2 receptor antagonist (amyloid-beta synaptic toxin blocker)",
         indication="neurology", outcome_hint="active"),
    dict(firm="3E BioVentures", slug="oncoc4", name="OncoC4", ticker=None,
         drug="AUNP-12 (IO-108)", mechanism="PD-L1/PD-L2 dual checkpoint peptide antagonist",
         indication="oncology", outcome_hint="active"),
    dict(firm="3E BioVentures", slug="dewpoint_therapeutics", name="Dewpoint Therapeutics", ticker=None,
         drug="DPTX3186", mechanism="Myc biomolecular condensate modulator (phase separation biology)",
         indication="oncology", outcome_hint="active"),
    dict(firm="3E BioVentures", slug="cullgen", name="Cullgen", ticker=None,
         drug="CG001419 / CG009301", mechanism="UBR-box targeted protein degradation (U-PROTAC)",
         indication="oncology", outcome_hint="acquired"),
    dict(firm="3E BioVentures", slug="lipidio", name="Lipidio", ticker=None,
         drug="LIP-401", mechanism="Fatty acid oxidation / ATGL lipase modulator",
         indication="metabolic", outcome_hint="active"),
    dict(firm="3E BioVentures", slug="arnatar_therapeutics", name="Arnatar Therapeutics", ticker=None,
         drug="ART101", mechanism="RNAi / GalNAc-siRNA hepatitis B replication inhibitor",
         indication="infectious", outcome_hint="active"),
    # ── BioVentures Capital ─────────────────────────────────────────────────────
    dict(firm="BioVentures Capital", slug="oraliva", name="Oraliva", ticker=None,
         drug="Oraliva periodontal formulation", mechanism="Topical antimicrobial / oral drug delivery",
         indication="rare_disease", outcome_hint="active"),
    dict(firm="BioVentures Capital", slug="biopathogenix", name="Biopathogenix", ticker=None,
         drug="Undisclosed antimicrobial", mechanism="Anti-infective / antimicrobial platform",
         indication="infectious", outcome_hint="active"),
    # ── BioVentures MedTech ─────────────────────────────────────────────────────
    dict(firm="BioVentures MedTech Funds", slug="optivio", name="Optivio", ticker=None,
         drug="Recover™ extracorporeal hemodynamic support",
         mechanism="Extracorporeal cardiopulmonary support device (cardiogenic shock)",
         indication="cardiovascular", outcome_hint="active"),
    dict(firm="BioVentures MedTech Funds", slug="endotronix", name="Endotronix", ticker=None,
         drug="Cordella PA Sensor System",
         mechanism="Implantable wireless pulmonary artery pressure monitor (HF management)",
         indication="cardiovascular", outcome_hint="active"),
    dict(firm="BioVentures MedTech Funds", slug="conextions", name="CoNextions", ticker=None,
         drug="CoNextions TR implant",
         mechanism="Mechanical tendon stapler / zone-2 flexor tendon repair implant",
         indication="rare_disease", outcome_hint="active"),
    dict(firm="BioVentures MedTech Funds", slug="deep_vein_medical", name="Deep Vein Medical", ticker=None,
         drug="DVT interventional device",
         mechanism="Venous insufficiency / deep vein interventional endovascular device",
         indication="cardiovascular", outcome_hint="active"),
    dict(firm="BioVentures MedTech Funds", slug="verax_biomedical", name="Verax Biomedical", ticker=None,
         drug="PGD Prime™ bacterial detection test",
         mechanism="Rapid platelet pathogen detection assay (blood safety)",
         indication="rare_disease", outcome_hint="active"),
    # ── Pivotal Life Sciences ──────────────────────────────────────────────────
    dict(firm="Pivotal Life Sciences", slug="io_biotech", name="IO Biotech", ticker="IOBT",
         drug="Cylembio (imsapepimut + etimupepimut / IO102-IO103)",
         mechanism="IDO/PD-L1/PD-L2 peptide cancer vaccine (off-the-shelf)",
         indication="oncology", outcome_hint="failed_p3"),
    dict(firm="Pivotal Life Sciences", slug="bolt_biotherapeutics", name="Bolt Biotherapeutics", ticker="BOLT",
         drug="BDC-4182 (anti-HER2 ISAC)", mechanism="TLR8 agonist immunostimulatory antibody conjugate (ISAC)",
         indication="oncology", outcome_hint="active"),
    dict(firm="Pivotal Life Sciences", slug="bioage_labs", name="BioAge Labs", ticker="BIOA",
         drug="BGE-102", mechanism="NLRP3 inflammasome inhibitor (oral brain-penetrant small molecule)",
         indication="metabolic", outcome_hint="active"),
    dict(firm="Pivotal Life Sciences", slug="aligos_therapeutics", name="Aligos Therapeutics", ticker="ALGS",
         drug="ALG-000184", mechanism="HBV core assembly machine (CAM) inhibitor + RNAi combination",
         indication="infectious", outcome_hint="active"),
    dict(firm="Pivotal Life Sciences", slug="gossamer_bio", name="Gossamer Bio", ticker="GOSS",
         drug="Seralutinib", mechanism="Inhaled PDGFR/FGFR/CSF1R kinase inhibitor (PAH)",
         indication="cardiovascular", outcome_hint="failed_p3"),
    dict(firm="Pivotal Life Sciences", slug="exscientia", name="Exscientia", ticker="EXAI",
         drug="EXS74539 / GTAEXS617", mechanism="AI-designed androgen receptor / CDK7 small molecule inhibitor",
         indication="oncology", outcome_hint="acquired"),
    dict(firm="Pivotal Life Sciences", slug="inozyme_pharma", name="Inozyme Pharma", ticker="INZY",
         drug="INZ-701", mechanism="ENPP1 recombinant enzyme replacement therapy (rare calcification disease)",
         indication="rare_disease", outcome_hint="active"),
    dict(firm="Pivotal Life Sciences", slug="oculis", name="Oculis", ticker="OCS",
         drug="OCS-01 / Licaminlimab", mechanism="Dexamethasone nanoparticle eye drop / IL-4Rα antibody",
         indication="rare_disease", outcome_hint="active"),
    dict(firm="Pivotal Life Sciences", slug="vigil_neuroscience", name="Vigil Neuroscience", ticker="VIGL",
         drug="VG-3927", mechanism="TREM2 agonist oral small molecule (microglial activator, Alzheimer's)",
         indication="neurology", outcome_hint="acquired"),
    dict(firm="Pivotal Life Sciences", slug="trevi_therapeutics", name="Trevi Therapeutics", ticker="TRVI",
         drug="Haduvio (nalbuphine ER)", mechanism="Kappa/mu-opioid receptor modulator (oral ER, chronic cough)",
         indication="rare_disease", outcome_hint="active"),
    dict(firm="Pivotal Life Sciences", slug="karuna_therapeutics", name="Karuna Therapeutics", ticker="KRTX",
         drug="Cobenfy (KarXT / xanomeline-trospium)",
         mechanism="M1/M4 muscarinic agonist + peripheral M2/M3 antagonist (schizophrenia)",
         indication="neurology", outcome_hint="approved"),
    dict(firm="Pivotal Life Sciences", slug="harmony_biosciences", name="Harmony Biosciences", ticker="HRMY",
         drug="WAKIX (pitolisant)", mechanism="Histamine H3 receptor inverse agonist (narcolepsy)",
         indication="neurology", outcome_hint="approved"),
    dict(firm="Pivotal Life Sciences", slug="gracell_biotechnologies", name="Gracell Biotechnologies", ticker="GRCL",
         drug="GC012F", mechanism="Allogeneic BCMA×CD19 dual-target FasTCAR-T cell therapy",
         indication="oncology", outcome_hint="acquired"),
    dict(firm="Pivotal Life Sciences", slug="fusion_pharmaceuticals", name="Fusion Pharmaceuticals", ticker="FUSN",
         drug="FPI-2265 (225Ac-PSMA-I&T)", mechanism="Actinium-225 targeted alpha therapy (radiopharmaceutical, PSMA)",
         indication="oncology", outcome_hint="active"),
    # ── Capital BioVentures ────────────────────────────────────────────────────
    dict(firm="Capital BioVentures", slug="apiary_tx", name="Apiary TX", ticker=None,
         drug="Bee venom peptide therapeutics (melittin derivatives)",
         mechanism="Antimicrobial peptide / apitoxin-derived anti-tumor agent",
         indication="oncology", outcome_hint="active"),
    dict(firm="Capital BioVentures", slug="cerebrotx", name="CerebroTX", ticker=None,
         drug="Undisclosed CNS asset", mechanism="CNS / neurological therapeutic (undisclosed mechanism)",
         indication="neurology", outcome_hint="active"),
    dict(firm="Capital BioVentures", slug="cura_therapeutics", name="Cura Therapeutics", ticker=None,
         drug="Undisclosed LNP delivery platform",
         mechanism="Lipid nanoparticle RNA / small molecule delivery system",
         indication="rare_disease", outcome_hint="active"),
    dict(firm="Capital BioVentures", slug="fibrodynamx", name="FibroDynamX", ticker=None,
         drug="Anti-fibrotic compound", mechanism="TGF-β / fibrosis pathway inhibitor",
         indication="rare_disease", outcome_hint="active"),
    dict(firm="Capital BioVentures", slug="i_rna_therapeutics", name="i-RNA Therapeutics", ticker=None,
         drug="lncRNA-targeting ophthalmic RNAi",
         mechanism="lncRNA-targeting RNAi eyedrop (topical ocular siRNA delivery)",
         indication="rare_disease", outcome_hint="active"),
]

# ── CT phase → stage mapping ───────────────────────────────────────────────────
PHASE_MAP = {
    "PHASE1": "phase1", "PHASE2": "phase2", "PHASE3": "phase3",
    "PHASE1, PHASE2": "phase2", "PHASE2, PHASE3": "phase3",
    "PHASE1,PHASE2": "phase2", "PHASE2,PHASE3": "phase3",
    "PHASE4": "approved", "NA": "unknown", "": "unknown",
}


def _parse_phase(phase_str: str) -> str:
    p = phase_str.strip().upper()
    for key in sorted(PHASE_MAP, key=len, reverse=True):
        if key in p:
            return PHASE_MAP[key]
    return "unknown"


def _read_texts(co_dir: Path, max_kb: int = 400) -> str:
    """Concatenate all .txt files under co_dir sub-directories."""
    chunks: list[str] = []
    budget = max_kb * 1024
    used = 0
    for sub in ["ct_gov", "pubmed", "gnw", "site"]:
        for f in sorted(co_dir.glob(f"{sub}/*.txt" if sub != "site" else "site_*.txt")):
            if used >= budget:
                break
            try:
                text = f.read_text(errors="ignore")[:8000]
                chunks.append(text)
                used += len(text)
            except OSError:
                pass
    return "\n\n".join(chunks)


def _parse_ct_files(co_dir: Path) -> dict:
    """
    Return dict with:
      max_stage: str (highest phase seen)
      terminated_stages: list[str]
      n_trials: int
      n_terminated: int
      n_completed: int
      n_recruiting: int
    """
    ct_dir = co_dir / "ct_gov"
    if not ct_dir.exists():
        return dict(max_stage="unknown", terminated_stages=[], n_trials=0,
                    n_terminated=0, n_completed=0, n_recruiting=0)

    max_stage_order = -1
    max_stage = "unknown"
    terminated_stages: list[str] = []
    n_trials = n_terminated = n_completed = n_recruiting = 0

    stage_order = {"preclinical": 0, "phase1": 1, "phase2": 2, "phase3": 3,
                   "nda_submitted": 3.5, "approved": 4}

    for f in ct_dir.glob("*.txt"):
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        status = re.search(r"^Status:\s*(.+)", text, re.M)
        phase  = re.search(r"^Phase:\s*(.+)", text, re.M)
        if not status:
            continue
        n_trials += 1
        stat = status.group(1).strip().upper()
        stage = _parse_phase(phase.group(1) if phase else "")

        order = stage_order.get(stage, -1)
        if order > max_stage_order:
            max_stage_order = order
            max_stage = stage

        if stat in ("TERMINATED", "WITHDRAWN"):
            n_terminated += 1
            if stage != "unknown":
                terminated_stages.append(stage)
        elif stat == "COMPLETED":
            n_completed += 1
        elif stat in ("RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"):
            n_recruiting += 1

    return dict(max_stage=max_stage, terminated_stages=terminated_stages,
                n_trials=n_trials, n_terminated=n_terminated,
                n_completed=n_completed, n_recruiting=n_recruiting)


def _derive_outcome(co: dict, ct: dict) -> tuple[str, str, str]:
    """Returns (clinical_stage, outcome, decision)."""
    hint = co.get("outcome_hint", "active")
    max_stage = ct["max_stage"] if ct["max_stage"] != "unknown" else "phase1"

    if hint == "approved":
        return "approved", "approved", "go"

    if hint == "acquired":
        return max_stage, "ongoing", "acquired"

    if hint in ("failed_p3", "failed_p2"):
        fail_stage = "phase3" if hint == "failed_p3" else "phase2"
        outcome_key = f"discontinued_{fail_stage.replace('phase', 'p')}"
        return fail_stage, outcome_key, "no-go"

    # active — use max observed CT stage, check if most trials terminated
    if ct["n_trials"] > 0:
        term_ratio = ct["n_terminated"] / ct["n_trials"]
    else:
        term_ratio = 0.0

    # If most trials terminated and there are terminated stages, treat as discontinued
    if term_ratio >= 0.6 and ct["terminated_stages"]:
        worst = max(ct["terminated_stages"],
                    key=lambda s: {"phase1": 1, "phase2": 2, "phase3": 3}.get(s, 0))
        out_key = f"discontinued_{worst.replace('phase', 'p')}"
        return worst, out_key, "no-go"

    return max_stage, "ongoing", "go" if ct["n_recruiting"] > 0 else "undecided"


def build_records() -> list[RawRecord]:
    """Build one RawRecord per portfolio company from downloaded materials."""
    records: list[RawRecord] = []
    for co in PORTFOLIO:
        co_dir = PORTFOLIO_DIR / co["slug"]
        if not co_dir.exists():
            log.warning("Missing folder: %s", co_dir)

        ct = _parse_ct_files(co_dir)
        raw_text = _read_texts(co_dir)
        stage, outcome, decision = _derive_outcome(co, ct)

        extra = {
            "firm": co["firm"],
            "ticker": co.get("ticker"),
            "drug": co["drug"],
            "ct_n_trials": ct["n_trials"],
            "ct_n_terminated": ct["n_terminated"],
            "ct_n_completed": ct["n_completed"],
            "ct_n_recruiting": ct["n_recruiting"],
            "ct_terminated_stages": ct["terminated_stages"],
        }

        rec = RawRecord(
            source=DB_SOURCE,
            source_id=co["slug"],
            url=f"https://{co['slug'].replace('_', '')}.com",
            title=f"{co['name']} — {co['drug']}",
            indication=co["indication"],
            mechanism=co["mechanism"],
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=0.0,
            raw_text=raw_text[:60_000],
            extra=extra,
        )
        records.append(rec)

    log.info("Built %d portfolio records", len(records))
    return records


# ── HTML report generation ─────────────────────────────────────────────────────

_SCORE_GRADIENT = [
    (0.70, "#22c55e"),  # green
    (0.50, "#84cc16"),  # lime
    (0.35, "#f59e0b"),  # amber
    (0.00, "#ef4444"),  # red
]


def _score_color(p: float) -> str:
    for threshold, color in _SCORE_GRADIENT:
        if p >= threshold:
            return color
    return "#ef4444"


def _bar_svg(values: list[tuple[str, float]], title: str, width: int = 560) -> str:
    """Horizontal bar chart as inline SVG."""
    if not values:
        return ""
    max_v = max(v for _, v in values) or 1e-9
    row_h = 22
    pad_l = 230
    pad_r = 70
    chart_w = width - pad_l - pad_r
    total_h = len(values) * row_h + 50

    bars = []
    for i, (name, val) in enumerate(values):
        y = i * row_h + 30
        bar_w = int((val / max_v) * chart_w)
        bars.append(
            f'<text x="{pad_l - 6}" y="{y + 14}" text-anchor="end" '
            f'font-size="11" fill="#555">{name[:35]}</text>'
            f'<rect x="{pad_l}" y="{y}" width="{bar_w}" height="16" '
            f'fill="#6366f1" rx="2"/>'
            f'<text x="{pad_l + bar_w + 4}" y="{y + 13}" font-size="10" fill="#777">'
            f'{val:.3f}</text>'
        )
    return (
        f'<svg width="{width}" height="{total_h}" xmlns="http://www.w3.org/2000/svg">'
        f'<text x="{width//2}" y="18" text-anchor="middle" font-size="13" '
        f'font-weight="bold" fill="#374151">{title}</text>'
        + "".join(bars)
        + "</svg>"
    )


def _risk_matrix_svg(companies: list[dict]) -> str:
    """
    2-D scatter: x=P(success), y=termination rate.
    Color by verdict. Labeled dots.
    """
    W, H = 620, 440
    PAD = 60
    cw = W - PAD * 2
    ch = H - PAD * 2 - 20

    dots = []
    for c in companies:
        p = c["p_success"]
        term_r = c.get("term_rate", 0.0)
        cx = int(PAD + p * cw)
        cy = int(PAD + 20 + (1 - term_r) * ch)
        color = _score_color(p)
        label = c["name"][:14]
        dots.append(
            f'<circle cx="{cx}" cy="{cy}" r="6" fill="{color}" '
            f'fill-opacity="0.85" stroke="#fff" stroke-width="1.2"/>'
            f'<text x="{cx + 8}" y="{cy + 4}" font-size="9" fill="#374151">{label}</text>'
        )

    axes = (
        f'<line x1="{PAD}" y1="{PAD+20}" x2="{PAD}" y2="{H-PAD}" '
        f'stroke="#9ca3af" stroke-width="1"/>'
        f'<line x1="{PAD}" y1="{H-PAD}" x2="{W-PAD}" y2="{H-PAD}" '
        f'stroke="#9ca3af" stroke-width="1"/>'
        f'<text x="{W//2}" y="{H-10}" text-anchor="middle" font-size="11" fill="#6b7280">'
        f'P(success) →</text>'
        f'<text x="14" y="{H//2}" text-anchor="middle" font-size="11" fill="#6b7280" '
        f'transform="rotate(-90 14 {H//2})">Trial Health →</text>'
        # tick labels x-axis
        f'<text x="{PAD}" y="{H-PAD+14}" font-size="9" text-anchor="middle" fill="#9ca3af">0%</text>'
        f'<text x="{PAD+cw//2}" y="{H-PAD+14}" font-size="9" text-anchor="middle" fill="#9ca3af">50%</text>'
        f'<text x="{PAD+cw}" y="{H-PAD+14}" font-size="9" text-anchor="middle" fill="#9ca3af">100%</text>'
        # threshold line at p=0.5
        f'<line x1="{PAD+cw//2}" y1="{PAD+20}" x2="{PAD+cw//2}" y2="{H-PAD}" '
        f'stroke="#f59e0b" stroke-width="1" stroke-dasharray="4,4"/>'
    )
    title = (
        f'<text x="{W//2}" y="16" text-anchor="middle" font-size="13" '
        f'font-weight="bold" fill="#374151">Risk Matrix: P(success) vs Trial Health</text>'
    )
    return (
        f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg">'
        + title + axes + "".join(dots)
        + "</svg>"
    )


def _calibration_html(cal: list[dict]) -> str:
    if not cal:
        return '<span style="color:#9ca3af">none</span>'
    rows = []
    for item in cal:
        adj = item["adjustment"]
        col = "#22c55e" if adj.startswith("+") else "#ef4444"
        rows.append(
            f'<span style="background:{col}22;border:1px solid {col}55;'
            f'border-radius:4px;padding:1px 6px;margin:2px;display:inline-block;'
            f'font-size:11px">'
            f'<b style="color:{col}">{adj}</b> {item["factor"]}</span>'
        )
    return " ".join(rows)


def _frontier_html(frontier: dict) -> str:
    in_use = frontier.get("in_use", [])
    not_using = frontier.get("not_using", [])
    rows = []
    for e in in_use[:4]:
        rows.append(
            f'<li><b>{e["tech"]}</b> '
            f'<span style="color:#6366f1">[{e["status"]} · {e["pursuit_level"]}]</span> — '
            f'{e["note"][:140]}…</li>'
        )
    for e in not_using[:3]:
        rows.append(
            f'<li style="color:#9ca3af"><s>{e["tech"]}</s> '
            f'<span>[{e["status"]} · {e["pursuit_level"]}]</span></li>'
        )
    return "<ul>" + "".join(rows) + "</ul>" if rows else ""


def generate_html(
    train_metrics: dict,
    feature_importance: list[tuple[str, float]],
    results: list[dict],
    db_stats: dict,
) -> str:
    # ── Summary stats ────────────────────────────────────────────────────────
    n_go    = sum(1 for r in results if r["verdict"] == "GO")
    n_nogo  = len(results) - n_go
    approved = sum(1 for r in results if r.get("outcome_hint") == "approved")
    acquired = sum(1 for r in results if r.get("outcome_hint") == "acquired")
    failed   = sum(1 for r in results if "failed" in r.get("outcome_hint", ""))
    avg_p    = sum(r["p_success"] for r in results) / len(results)

    # Firm breakdown
    firm_rows = defaultdict(list)
    for r in results:
        firm_rows[r["firm"]].append(r)

    # Feature importance chart (top 20)
    fi_top = feature_importance[:20]
    fi_svg = _bar_svg(fi_top, "Top 20 Feature Importances (Gradient Boosting)")

    # Risk matrix
    risk_svg = _risk_matrix_svg(results)

    # Per-company cards
    company_cards = []
    for r in sorted(results, key=lambda x: -x["p_success"]):
        p    = r["p_success"]
        col  = _score_color(p)
        verd = r["verdict"]
        vbg  = "#dcfce7" if verd == "GO" else "#fee2e2"
        vcol = "#166534" if verd == "GO" else "#991b1b"

        # Trial bar
        ct_n = r["ct_n_trials"]
        ct_t = r["ct_n_terminated"]
        ct_c = r["ct_n_completed"]
        ct_r = r["ct_n_recruiting"]
        ct_bar = ""
        if ct_n > 0:
            tw = int(ct_t / ct_n * 100)
            cw = int(ct_c / ct_n * 100)
            rw = int(ct_r / ct_n * 100)
            ct_bar = (
                f'<div style="margin:6px 0;font-size:11px">'
                f'<b>Trials ({ct_n}):</b> '
                f'<span style="background:#ef444433;padding:1px 5px;border-radius:3px">'
                f'{ct_t} term</span> '
                f'<span style="background:#22c55e33;padding:1px 5px;border-radius:3px">'
                f'{ct_c} compl</span> '
                f'<span style="background:#6366f133;padding:1px 5px;border-radius:3px">'
                f'{ct_r} recruit</span>'
                f'</div>'
                f'<div style="height:6px;background:#e5e7eb;border-radius:3px;overflow:hidden">'
                f'<div style="display:flex;height:100%">'
                f'<div style="width:{tw}%;background:#ef4444"></div>'
                f'<div style="width:{cw}%;background:#22c55e"></div>'
                f'<div style="width:{rw}%;background:#6366f1"></div>'
                f'</div></div>'
            )

        # Signals
        sigs = r.get("signals", {})
        sig_html = ""
        if sigs:
            items = []
            if sigs.get("has_completion_signal"):
                items.append('<span style="color:#16a34a">✓ Positive signals</span>')
            if sigs.get("has_failure_signal"):
                items.append('<span style="color:#dc2626">✗ Failure signals</span>')
            if sigs.get("has_breakthrough_designation"):
                items.append('<span style="color:#7c3aed">★ Breakthrough/FT designation</span>')
            sig_html = " &nbsp;·&nbsp; ".join(items)

        # Technology
        tech = r.get("technology", {})
        fmt_labels = ", ".join(tech.get("formats", [])[:3]) or "N/A"
        fit_score = tech.get("fit_score", 0)
        fit_bar_w = int(fit_score * 80)
        fit_col = "#22c55e" if fit_score >= 0.7 else ("#f59e0b" if fit_score >= 0.4 else "#ef4444")

        # Biology
        bio = r.get("biology", {})
        target_status = bio.get("target_status", "unknown")
        detected = ", ".join(bio.get("detected_targets", [])[:4]) or "none detected"

        company_cards.append(f"""
        <div id="co-{r['slug']}" style="background:#fff;border:1px solid #e5e7eb;
             border-left:4px solid {col};border-radius:8px;padding:18px;margin:12px 0;
             box-shadow:0 1px 4px #0001">
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <span style="font-size:10px;color:#6b7280;text-transform:uppercase;
                    letter-spacing:.05em">{r['firm']}</span>
              <h3 style="margin:2px 0;font-size:17px">{r['name']}
                <span style="font-size:12px;color:#6b7280;font-weight:normal">
                  {('(' + r['ticker'] + ')') if r.get('ticker') else ''}</span>
              </h3>
              <div style="font-size:12px;color:#4b5563">{r['drug']}</div>
              <div style="font-size:11px;color:#6b7280;margin-top:2px">{r['mechanism'][:120]}</div>
            </div>
            <div style="text-align:right;min-width:90px">
              <div style="font-size:28px;font-weight:bold;color:{col}">{p:.1%}</div>
              <div style="background:{vbg};color:{vcol};border-radius:4px;
                   padding:2px 10px;font-weight:bold;font-size:13px">{verd}</div>
              <div style="font-size:11px;color:#9ca3af;margin-top:3px">
                Stage: {r['clinical_stage']}</div>
            </div>
          </div>

          {ct_bar}

          <div style="margin-top:8px;font-size:11px">
            <b>Calibration:</b> {_calibration_html(r.get('calibration', []))}
          </div>

          <div style="margin-top:6px;font-size:11px">
            <b>Signals:</b> {sig_html or '<span style="color:#9ca3af">none detected</span>'}
          </div>

          <div style="margin-top:8px;display:flex;gap:16px;flex-wrap:wrap">
            <div style="font-size:11px">
              <b>Tech formats:</b> {fmt_labels}<br>
              <b>Tech fit:</b>
              <span style="display:inline-block;background:#f3f4f6;border-radius:3px;
                    padding:1px;vertical-align:middle;width:80px">
                <span style="display:block;height:10px;width:{fit_bar_w}px;
                      background:{fit_col};border-radius:2px"></span>
              </span>
              <span style="font-size:10px;color:#6b7280">{fit_score:.2f}</span>
            </div>
            <div style="font-size:11px">
              <b>Target:</b> {target_status}<br>
              <b>Detected:</b> {detected[:80]}
            </div>
          </div>

          <details style="margin-top:8px">
            <summary style="cursor:pointer;font-size:11px;color:#6366f1;
                     font-weight:600">Frontier technology analysis</summary>
            <div style="font-size:11px;margin-top:4px">
              {_frontier_html(r.get('frontier', {}))}
            </div>
          </details>

          <details style="margin-top:4px">
            <summary style="cursor:pointer;font-size:11px;color:#6366f1;
                     font-weight:600">Model summary</summary>
            <div style="font-size:11px;color:#374151;margin-top:4px;
                 background:#f9fafb;padding:8px;border-radius:4px">
              {r.get('summary', '')[:400]}
            </div>
          </details>
        </div>
        """)

    # Firm summary table
    firm_table_rows = []
    for firm, cos in sorted(firm_rows.items()):
        avg = sum(c["p_success"] for c in cos) / len(cos)
        go  = sum(1 for c in cos if c["verdict"] == "GO")
        acq = sum(1 for c in cos if c.get("outcome_hint") == "acquired")
        app = sum(1 for c in cos if c.get("outcome_hint") == "approved")
        fail = sum(1 for c in cos if "failed" in c.get("outcome_hint", ""))
        col = _score_color(avg)
        firm_table_rows.append(
            f'<tr>'
            f'<td><b>{firm}</b></td>'
            f'<td>{len(cos)}</td>'
            f'<td style="color:{col};font-weight:bold">{avg:.1%}</td>'
            f'<td style="color:#22c55e">{go}</td>'
            f'<td style="color:#6366f1">{acq}</td>'
            f'<td style="color:#f59e0b">{app}</td>'
            f'<td style="color:#ef4444">{fail}</td>'
            f'</tr>'
        )

    # Training section
    n_train = train_metrics.get("n_train", 0)
    n_test  = train_metrics.get("n_test", 0)
    auc     = train_metrics.get("auc_roc", 0)
    acc     = train_metrics.get("accuracy", 0)
    status  = train_metrics.get("status", "")
    if status == "skipped_insufficient_data":
        train_html = f"""
        <div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:12px">
        ⚠️ Supervised training skipped — only {train_metrics.get('n',0)} labelled samples available.
        Predictions use Bayesian priors + domain-knowledge calibration only.
        </div>"""
    else:
        auc_pct = int(auc * 100)
        acc_pct = int(acc * 100)
        train_html = f"""
        <div style="display:flex;gap:24px;flex-wrap:wrap;margin:12px 0">
          <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;
               padding:16px;min-width:150px;text-align:center">
            <div style="font-size:32px;font-weight:bold;color:#16a34a">{auc:.3f}</div>
            <div style="font-size:12px;color:#15803d">AUC-ROC</div>
          </div>
          <div style="background:#eff6ff;border:1px solid #93c5fd;border-radius:8px;
               padding:16px;min-width:150px;text-align:center">
            <div style="font-size:32px;font-weight:bold;color:#1d4ed8">{acc:.3f}</div>
            <div style="font-size:12px;color:#1e40af">Accuracy</div>
          </div>
          <div style="background:#faf5ff;border:1px solid #c4b5fd;border-radius:8px;
               padding:16px;min-width:150px;text-align:center">
            <div style="font-size:32px;font-weight:bold;color:#7c3aed">{n_train:,}</div>
            <div style="font-size:12px;color:#6d28d9">Train samples</div>
          </div>
          <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:8px;
               padding:16px;min-width:150px;text-align:center">
            <div style="font-size:32px;font-weight:bold;color:#c2410c">{n_test:,}</div>
            <div style="font-size:12px;color:#9a3412">Test samples</div>
          </div>
        </div>
        <div style="margin:8px 0;font-size:12px;color:#6b7280">
          Ensemble: Logistic Regression (C=0.5) + Gradient Boosting (80 trees, depth 3, lr=0.05).
          Post-model calibration: validated target (+0.25), unvalidated target (−0.08),
          positive signal (+0.15), failure signal (−0.18), tech fit adjustments.
        </div>"""

    # DB stats
    db_html = "".join(
        f'<li>{s}: <b>{n:,}</b></li>' for s, n in db_stats.items()
    )

    now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VC Portfolio Decision Analysis — {now}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       margin:0;background:#f8fafc;color:#1f2937;line-height:1.5}}
  .wrap{{max-width:960px;margin:0 auto;padding:24px 16px}}
  h1{{font-size:24px;margin:0 0 4px}}
  h2{{font-size:18px;border-bottom:2px solid #e5e7eb;padding-bottom:6px;margin-top:32px}}
  table{{border-collapse:collapse;width:100%;font-size:13px}}
  th{{background:#f3f4f6;text-align:left;padding:7px 10px;border-bottom:2px solid #e5e7eb}}
  td{{padding:6px 10px;border-bottom:1px solid #f3f4f6}}
  tr:hover td{{background:#f9fafb}}
  details summary{{outline:none}}
  .stat-pill{{display:inline-block;padding:2px 10px;border-radius:12px;
              font-size:12px;font-weight:600;margin:2px}}
  .toc a{{color:#6366f1;text-decoration:none;font-size:13px;margin-right:12px}}
  .toc a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<div class="wrap">

<h1>VC Portfolio Decision Analysis</h1>
<p style="color:#6b7280;font-size:13px">Generated {now} &nbsp;·&nbsp;
  {len(results)} companies across 5 VC firms &nbsp;·&nbsp;
  {db_stats.get('total_records',0):,} total training records</p>

<div class="toc">
  <a href="#overview">Overview</a>
  <a href="#model">Model</a>
  <a href="#features">Features</a>
  <a href="#matrix">Risk Matrix</a>
  <a href="#firms">By Firm</a>
  <a href="#companies">All Companies</a>
</div>

<!-- ═══════════════════ OVERVIEW ═══════════════════ -->
<h2 id="overview">Portfolio Overview</h2>
<div style="display:flex;gap:16px;flex-wrap:wrap;margin:12px 0">
  <div class="stat-pill" style="background:#dcfce7;color:#166534">
    {n_go} GO ({n_go/len(results):.0%})</div>
  <div class="stat-pill" style="background:#fee2e2;color:#991b1b">
    {n_nogo} NO-GO ({n_nogo/len(results):.0%})</div>
  <div class="stat-pill" style="background:#ede9fe;color:#5b21b6">
    {approved} Approved</div>
  <div class="stat-pill" style="background:#dbeafe;color:#1e40af">
    {acquired} Acquired</div>
  <div class="stat-pill" style="background:#fee2e2;color:#991b1b">
    {failed} Phase 3 failures</div>
  <div class="stat-pill" style="background:#f3f4f6;color:#374151">
    Avg P(success) = {avg_p:.1%}</div>
</div>

<!-- ═══════════════════ MODEL ═══════════════════ -->
<h2 id="model">Model Training</h2>
{train_html}
<details style="margin-top:8px">
  <summary style="cursor:pointer;font-size:12px;color:#6b7280">DB source breakdown</summary>
  <ul style="font-size:12px;columns:2">{db_html}</ul>
</details>

<!-- ═══════════════════ FEATURES ═══════════════════ -->
<h2 id="features">Feature Importances</h2>
<div style="overflow-x:auto">{fi_svg}</div>

<!-- ═══════════════════ RISK MATRIX ═══════════════════ -->
<h2 id="matrix">Risk Matrix</h2>
<div style="overflow-x:auto">{risk_svg}</div>
<p style="font-size:11px;color:#9ca3af">
  X-axis: P(success) from model. Y-axis: trial health (higher = fewer terminated trials).
  Dashed line at 50% = GO/NO-GO threshold. Colors: green ≥70%, lime ≥50%, amber ≥35%, red &lt;35%.
</p>

<!-- ═══════════════════ FIRMS ═══════════════════ -->
<h2 id="firms">Firm Summary</h2>
<table>
  <thead><tr>
    <th>Firm</th><th>Companies</th><th>Avg P(success)</th>
    <th>GO</th><th>Acquired</th><th>Approved</th><th>Failed Ph3</th>
  </tr></thead>
  <tbody>{"".join(firm_table_rows)}</tbody>
</table>

<!-- ═══════════════════ COMPANIES ═══════════════════ -->
<h2 id="companies">All Companies (ranked by P(success))</h2>
{"".join(company_cards)}

</div><!-- /wrap -->
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("Step 1 — Building portfolio records from downloaded materials…")
    records = build_records()

    log.info("Step 2 — Upserting %d records into DB…", len(records))
    # Remove old v2 records first
    from src.storage.database import DB_PATH, _load, _save
    db = _load(DB_PATH)
    old_keys = [k for k in db["projects"] if k.startswith(f"{DB_SOURCE}::")]
    for k in old_keys:
        del db["projects"][k]
    _save(db, DB_PATH)
    n_ins = bulk_upsert(records)
    log.info("  Inserted %d records", n_ins)

    log.info("Step 3 — Training SuccessPredictor…")
    model = SuccessPredictor()
    metrics = model.train()
    log.info("  Training metrics: %s", metrics)

    log.info("Step 4 — Running explain() for each company…")
    results = []
    for co, rec in zip(PORTFOLIO, records):
        row = {
            "title":          rec.title,
            "indication":     rec.indication,
            "mechanism":      rec.mechanism,
            "clinical_stage": rec.clinical_stage,
            "outcome":        rec.outcome,
            "raw_text":       rec.raw_text[:20_000],
        }
        expl = model.explain(row)

        # term rate
        ct_n   = co.get("ct_n", rec.extra["ct_n_trials"])
        ct_t   = co.get("ct_t", rec.extra["ct_n_terminated"])
        term_r = ct_t / ct_n if ct_n > 0 else 0.0

        results.append({
            **expl,
            "slug":           co["slug"],
            "name":           co["name"],
            "firm":           co["firm"],
            "ticker":         co.get("ticker"),
            "drug":           co["drug"],
            "mechanism":      co["mechanism"],
            "clinical_stage": rec.clinical_stage,
            "outcome_hint":   co["outcome_hint"],
            "ct_n_trials":    rec.extra["ct_n_trials"],
            "ct_n_terminated":rec.extra["ct_n_terminated"],
            "ct_n_completed": rec.extra["ct_n_completed"],
            "ct_n_recruiting":rec.extra["ct_n_recruiting"],
            "term_rate":      term_r,
        })

    # DB stats
    all_rows = fetch_all()
    from collections import Counter
    src_c = Counter(r.get("source", "?") for r in all_rows)
    db_stats = {"total_records": len(all_rows)}
    db_stats.update(src_c)

    log.info("Step 5 — Generating HTML report…")
    fi = model.feature_importance()
    html = generate_html(metrics, fi, results, db_stats)
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text(html, encoding="utf-8")
    log.info("  Report → %s  (%d KB)", REPORT_OUT, len(html) // 1024)

    # Console summary
    print("\n" + "═" * 62)
    print(f"  Portfolio Training Report")
    print("═" * 62)
    if "auc_roc" in metrics:
        print(f"  Model AUC: {metrics['auc_roc']:.3f}  |  Accuracy: {metrics['accuracy']:.3f}")
        print(f"  Training samples: {metrics['n_train']:,}  |  Test: {metrics['n_test']:,}")
    print(f"  Companies scored: {len(results)}")
    print()
    # Top / bottom
    srt = sorted(results, key=lambda x: -x["p_success"])
    print("  TOP 5 (highest P(success)):")
    for r in srt[:5]:
        print(f"    {r['p_success']:.1%}  {r['verdict']:6}  {r['name']}")
    print()
    print("  BOTTOM 5 (lowest P(success)):")
    for r in srt[-5:]:
        print(f"    {r['p_success']:.1%}  {r['verdict']:6}  {r['name']}")
    print()
    print(f"  Report → {REPORT_OUT}")
    print("═" * 62)


if __name__ == "__main__":
    main()
