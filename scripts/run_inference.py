#!/usr/bin/env python3
"""
GO / NO-GO Inference Report Generator
--------------------------------------
Runs a list of drug programs through the trained SuccessPredictor model
and writes a self-contained HTML report to  data/inference_report.html

Usage:
    python3 run_inference.py                      # uses built-in demo programs
    python3 run_inference.py my_programs.json     # reads programs from JSON file
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# ── optional: accept a JSON file of programs as first arg ────────────────────
_PROGRAMS_FILE = Path(sys.argv[1]) if len(sys.argv) > 1 else None

# ── default hypothetical programs ────────────────────────────────────────────
DEFAULT_PROGRAMS = [
    {
        "_id": "HYP-001",
        "title": "Phase 2 HER2-targeted ADC · Breast Cancer",
        "description": (
            "Cleavable ADC targeting HER2 (ERBB2) in HER2-low metastatic breast cancer. "
            "Phase 2 randomised study, 120 patients. Interim ORR 38% vs 18% control. "
            "No dose-limiting toxicities observed. Partnered with AstraZeneca."
        ),
        "clinical_stage": "phase2",
        "indication": "oncology",
        "source": "clinical_trials",
    },
    {
        "_id": "HYP-002",
        "title": "Phase 1 LRRK2 Inhibitor · Parkinson's Disease",
        "description": (
            "First-in-class small molecule inhibitor of LRRK2 kinase for Parkinson's disease. "
            "Preclinical data in rodent models only. No clinical safety data yet. "
            "Unvalidated target, no approved drugs in class. Early seed-stage company."
        ),
        "clinical_stage": "phase1",
        "indication": "neurology",
        "source": "clinical_trials",
    },
    {
        "_id": "HYP-003",
        "title": "Phase 3 GLP-1/GIP Dual Agonist · Obesity",
        "description": (
            "Subcutaneous GLP-1/GIP dual receptor agonist for obesity and type 2 diabetes. "
            "Phase 3 trial, 2400 patients. 22% mean weight loss at 72 weeks. "
            "Validated target class. NDA submission planned 2026."
        ),
        "clinical_stage": "phase3",
        "indication": "metabolic",
        "source": "clinical_trials",
    },
    {
        "_id": "HYP-004",
        "title": "Phase 2 CAR-T · Relapsed B-cell Lymphoma",
        "description": (
            "CD19-directed CAR-T cell therapy in relapsed/refractory B-cell NHL. "
            "Phase 2 single-arm study. CR rate 54%. Manufacturing scale-up risk noted. "
            "Competing with approved axicabtagene and tisagenlecleucel products."
        ),
        "clinical_stage": "phase2",
        "indication": "oncology",
        "source": "clinical_trials",
    },
    {
        "_id": "HYP-005",
        "title": "Phase 1 siRNA · PCSK9 Cardiovascular",
        "description": (
            "Subcutaneous siRNA targeting PCSK9 for LDL lowering in high-risk CV patients. "
            "Phase 1 SAD/MAD study. 60% LDL reduction observed. "
            "Validated target (inclisiran approved). RNA platform, durable 6-month dosing."
        ),
        "clinical_stage": "phase1",
        "indication": "cardiovascular",
        "source": "clinical_trials",
    },
    {
        "_id": "HYP-006",
        "title": "Preclinical CRISPR · Sickle Cell Disease",
        "description": (
            "Ex vivo CRISPR-Cas9 editing of BCL11A enhancer to reactivate fetal haemoglobin "
            "in sickle cell disease. IND-enabling studies. "
            "Validated biology (similar approach approved as Casgevy). "
            "Manufacturing and regulatory complexity high."
        ),
        "clinical_stage": "preclinical",
        "indication": "rare_disease",
        "source": "clinical_trials",
    },
]

# ── load model ────────────────────────────────────────────────────────────────
print("Loading model …", flush=True)
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from src.learning.decision_model import SuccessPredictor

model = SuccessPredictor()
metrics = model.train()
print(f"Model ready  AUC={metrics.get('auc_roc', 0):.3f}  n={metrics.get('n_train', 0):,}", flush=True)

# ── run inference ─────────────────────────────────────────────────────────────
if _PROGRAMS_FILE and _PROGRAMS_FILE.exists():
    programs = json.loads(_PROGRAMS_FILE.read_text())
    print(f"Loaded {len(programs)} programs from {_PROGRAMS_FILE}")
else:
    programs = DEFAULT_PROGRAMS

results = []
for prog in programs:
    r = model.explain(prog)
    results.append({"program": prog, "result": r})
    verdict = r["verdict"]
    print(f"  {prog['_id']}  {r['score_pct']:>6}  {verdict}  {prog['title'][:55]}")

# ── build HTML ────────────────────────────────────────────────────────────────
STAGE_LABELS = {
    "preclinical": "Preclinical", "phase1": "Phase 1", "phase2": "Phase 2",
    "phase3": "Phase 3", "nda_submitted": "NDA Filed", "approved": "Approved",
}
INDICATION_LABELS = {
    "oncology": "Oncology", "rare_disease": "Rare Disease", "immunology": "Immunology",
    "neurology": "Neurology", "cardiovascular": "Cardiovascular",
    "metabolic": "Metabolic", "infectious": "Infectious Disease",
}

def pct_bar(p: float) -> str:
    """SVG progress bar coloured by score."""
    pct = round(p * 100, 1)
    if p >= 0.65:
        colour = "#22c55e"   # green
    elif p >= 0.45:
        colour = "#f59e0b"   # amber
    else:
        colour = "#ef4444"   # red
    return (
        f'<div class="bar-wrap">'
        f'<div class="bar-fill" style="width:{pct}%;background:{colour}"></div>'
        f'</div>'
    )

def verdict_badge(v: str) -> str:
    cls = "badge-go" if v == "GO" else "badge-nogo"
    icon = "▲ GO" if v == "GO" else "▼ NO-GO"
    return f'<span class="badge {cls}">{icon}</span>'

def calibration_rows(cal: list) -> str:
    if not cal:
        return '<tr><td colspan="2" class="muted">No adjustments applied</td></tr>'
    rows = ""
    for c in cal:
        adj = c["adjustment"]
        colour = "#22c55e" if adj.startswith("+") else "#ef4444"
        rows += (
            f'<tr><td style="color:{colour};font-weight:600">{adj}</td>'
            f'<td>{c["factor"]}</td></tr>'
        )
    return rows

def lesson_items(lessons: list) -> str:
    if not lessons:
        return "<li class='muted'>No historical lessons found for this modality.</li>"
    items = ""
    for l in lessons[:4]:
        txt = l.get("lesson", "")
        items += f"<li>{txt}</li>"
    return items

def card(prog: dict, r: dict, idx: int) -> str:
    p = r["p_success"]
    pct = f"{p:.1%}"
    v = r["verdict"]
    stage = STAGE_LABELS.get(prog.get("clinical_stage", ""), prog.get("clinical_stage", "—"))
    indication = INDICATION_LABELS.get(prog.get("indication", ""), prog.get("indication", "—"))
    tech = r["technology"]
    platform = ", ".join(tech["platform"]) if tech["platform"] else "Standard modality"
    fit = f"{tech['fit_score']:.0%}"
    fit_label = "Clear-cut fit ✓" if tech["is_clearcut"] else f"Fit score {fit}"
    bio = r["biology"]
    targets = ", ".join(bio["detected_targets"]) if bio["detected_targets"] else "None detected"

    border = "#22c55e" if v == "GO" else "#ef4444"

    return f"""
    <div class="card" style="border-left:5px solid {border}">
      <div class="card-header">
        <div>
          <span class="prog-id">{prog['_id']}</span>
          <span class="prog-title">{prog['title']}</span>
        </div>
        <div class="header-right">
          <span class="score-big" style="color:{border}">{pct}</span>
          {verdict_badge(v)}
        </div>
      </div>

      {pct_bar(p)}

      <p class="description">{prog['description']}</p>

      <div class="meta-row">
        <div class="meta-item"><span class="meta-label">Stage</span><span class="meta-val">{stage}</span></div>
        <div class="meta-item"><span class="meta-label">Indication</span><span class="meta-val">{indication}</span></div>
        <div class="meta-item"><span class="meta-label">Platform</span><span class="meta-val">{platform}</span></div>
        <div class="meta-item"><span class="meta-label">Target(s)</span><span class="meta-val">{targets}</span></div>
        <div class="meta-item"><span class="meta-label">Tech fit</span><span class="meta-val">{fit_label}</span></div>
        <div class="meta-item"><span class="meta-label">Target status</span><span class="meta-val">{bio['target_status']}</span></div>
      </div>

      <div class="two-col">
        <div class="sub-box">
          <h4>Score Calibration</h4>
          <table class="cal-table">
            <thead><tr><th>Δ</th><th>Factor</th></tr></thead>
            <tbody>{calibration_rows(r['calibration'])}</tbody>
          </table>
        </div>
        <div class="sub-box">
          <h4>Historical Lessons</h4>
          <ul class="lessons">{lesson_items(r.get('historical_lessons', []))}</ul>
        </div>
      </div>

      <div class="summary-box">{r['summary']}</div>
    </div>
    """


# ── summary stats ─────────────────────────────────────────────────────────────
n_go = sum(1 for x in results if x["result"]["verdict"] == "GO")
n_nogo = len(results) - n_go
avg_score = sum(x["result"]["p_success"] for x in results) / len(results)
sorted_res = sorted(results, key=lambda x: x["result"]["p_success"], reverse=True)

cards_html = "\n".join(card(x["program"], x["result"], i) for i, x in enumerate(sorted_res))

model_auc  = metrics.get("auc_roc", 0)
model_n    = metrics.get("n_train", 0)
ts = datetime.now().strftime("%Y-%m-%d %H:%M")

HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>REC-DECISION · Inference Report</title>
<style>
  :root {{
    --bg: #0f172a; --surface: #1e293b; --surface2: #293548;
    --border: #334155; --text: #e2e8f0; --muted: #64748b;
    --go: #22c55e; --nogo: #ef4444; --amber: #f59e0b;
    --accent: #38bdf8;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; line-height: 1.6; }}

  header {{ background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%); padding: 32px 40px; border-bottom: 1px solid var(--border); }}
  header h1 {{ font-size: 24px; font-weight: 700; color: var(--accent); letter-spacing: 0.05em; }}
  header .sub {{ color: var(--muted); margin-top: 4px; font-size: 13px; }}

  .stats-bar {{ display: flex; gap: 24px; padding: 20px 40px; background: var(--surface); border-bottom: 1px solid var(--border); flex-wrap: wrap; }}
  .stat {{ display: flex; flex-direction: column; }}
  .stat-val {{ font-size: 28px; font-weight: 700; }}
  .stat-label {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }}
  .go-val {{ color: var(--go); }}
  .nogo-val {{ color: var(--nogo); }}
  .accent-val {{ color: var(--accent); }}

  main {{ max-width: 960px; margin: 32px auto; padding: 0 24px 64px; }}

  .card {{ background: var(--surface); border-radius: 12px; padding: 24px; margin-bottom: 24px; border: 1px solid var(--border); }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; gap: 12px; }}
  .header-right {{ display: flex; align-items: center; gap: 12px; flex-shrink: 0; }}
  .prog-id {{ font-size: 11px; font-weight: 600; color: var(--muted); margin-right: 8px; }}
  .prog-title {{ font-size: 17px; font-weight: 600; }}
  .score-big {{ font-size: 32px; font-weight: 800; }}

  .badge {{ display: inline-block; padding: 4px 14px; border-radius: 20px; font-size: 13px; font-weight: 700; }}
  .badge-go {{ background: rgba(34,197,94,.15); color: var(--go); border: 1px solid var(--go); }}
  .badge-nogo {{ background: rgba(239,68,68,.15); color: var(--nogo); border: 1px solid var(--nogo); }}

  .bar-wrap {{ height: 8px; background: var(--surface2); border-radius: 4px; margin-bottom: 14px; overflow: hidden; }}
  .bar-fill {{ height: 100%; border-radius: 4px; transition: width .6s ease; }}

  .description {{ color: #94a3b8; font-size: 13px; margin-bottom: 16px; }}

  .meta-row {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 18px; }}
  .meta-item {{ background: var(--surface2); border-radius: 8px; padding: 8px 14px; min-width: 140px; }}
  .meta-label {{ display: block; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }}
  .meta-val {{ display: block; font-size: 13px; font-weight: 600; margin-top: 2px; }}

  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
  @media (max-width: 640px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
  .sub-box {{ background: var(--surface2); border-radius: 8px; padding: 14px 16px; }}
  .sub-box h4 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin-bottom: 10px; }}

  .cal-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .cal-table th {{ text-align: left; font-size: 11px; color: var(--muted); padding-bottom: 4px; }}
  .cal-table td {{ padding: 3px 8px 3px 0; vertical-align: top; }}
  .cal-table td:first-child {{ width: 54px; font-family: monospace; }}

  .lessons {{ list-style: none; font-size: 13px; color: #94a3b8; }}
  .lessons li {{ padding: 3px 0; padding-left: 12px; position: relative; }}
  .lessons li::before {{ content: "›"; position: absolute; left: 0; color: var(--accent); }}

  .muted {{ color: var(--muted); }}

  .summary-box {{ background: rgba(56,189,248,.07); border: 1px solid rgba(56,189,248,.2); border-radius: 8px; padding: 12px 16px; font-size: 13px; color: #bae6fd; }}

  footer {{ text-align: center; padding: 32px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--border); margin-top: 16px; }}
</style>
</head>
<body>

<header>
  <h1>REC-DECISION · GO / NO-GO Inference Report</h1>
  <div class="sub">
    Generated {ts} &nbsp;·&nbsp;
    Model: SuccessPredictor_ensemble &nbsp;·&nbsp;
    AUC-ROC: {model_auc:.3f} &nbsp;·&nbsp;
    Trained on {model_n:,} samples
  </div>
</header>

<div class="stats-bar">
  <div class="stat"><span class="stat-val go-val">{n_go}</span><span class="stat-label">GO decisions</span></div>
  <div class="stat"><span class="stat-val nogo-val">{n_nogo}</span><span class="stat-label">NO-GO decisions</span></div>
  <div class="stat"><span class="stat-val accent-val">{len(results)}</span><span class="stat-label">Programs evaluated</span></div>
  <div class="stat"><span class="stat-val" style="color:var(--amber)">{avg_score:.1%}</span><span class="stat-label">Avg success probability</span></div>
</div>

<main>
{cards_html}
</main>

<footer>
  REC-DECISION · Machine Learning from Top Bio Firms · {ts}
</footer>

</body>
</html>
"""

out = _ROOT / "data" / "reports" / "inference_report.html"
out.write_text(HTML)
print(f"\nReport written → {out.resolve()}")
print(f"Open in browser:  file://{out.resolve()}")
