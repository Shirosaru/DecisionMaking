#!/usr/bin/env python3
"""
Bulk inference: generate 1000 varied drug programs, score all of them,
then produce a trends + lessons HTML report at data/bulk_inference_report.html
"""
from __future__ import annotations

import itertools, json, random, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

random.seed(42)
sys.path.insert(0, str(Path(__file__).parent))

# ─────────────────────────────────────────────────────────────────────────────
# 1. PROGRAM GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

STAGES       = ["preclinical", "phase1", "phase2", "phase3"]
INDICATIONS  = ["oncology", "rare_disease", "immunology", "neurology",
                "cardiovascular", "metabolic", "infectious"]
STAGE_LABEL  = {"preclinical":"Preclinical","phase1":"Phase 1",
                "phase2":"Phase 2","phase3":"Phase 3"}
IND_LABEL    = {"oncology":"Oncology","rare_disease":"Rare Disease",
                "immunology":"Immunology","neurology":"Neurology",
                "cardiovascular":"Cardiovascular","metabolic":"Metabolic",
                "infectious":"Infectious"}

# (platform_name, description_snippet, mechanism_keywords)
PLATFORMS = [
    ("ADC (cleavable)",
     "Cleavable linker antibody-drug conjugate (ADC) with {target} payload. "
     "Site-specific conjugation, DAR 4. {trial_detail}",
     ["adc", "antibody-drug conjugate", "cleavable"]),

    ("ADC (non-cleavable)",
     "Stable-linker ADC targeting {target}. MMAE warhead, DAR 3.5. {trial_detail}",
     ["adc", "antibody-drug conjugate", "non-cleavable"]),

    ("Bispecific antibody (HLE BiTE)",
     "Half-life extended bispecific T-cell engager (HLE BiTE) targeting {target} x CD3. "
     "Weekly SC dosing. {trial_detail}",
     ["bispecific", "bite", "t-cell engager"]),

    ("Monoclonal antibody",
     "Full-length IgG1 monoclonal antibody against {target}. {trial_detail}",
     ["monoclonal antibody", "mab", "igg"]),

    ("Small molecule (oral)",
     "Oral small molecule inhibitor of {target}. Once-daily dosing. {trial_detail}",
     ["small molecule", "inhibitor", "oral"]),

    ("CAR-T (autologous)",
     "Autologous CAR-T cell therapy directed at {target}. {trial_detail}",
     ["car-t", "cell therapy", "autologous"]),

    ("siRNA",
     "Subcutaneous siRNA GalNAc conjugate silencing {target}. Quarterly dosing. {trial_detail}",
     ["sirna", "rna", "oligonucleotide"]),

    ("mRNA",
     "Lipid nanoparticle mRNA encoding {target} antigen/protein. {trial_detail}",
     ["mrna", "rna", "nanoparticle"]),

    ("CRISPR ex vivo",
     "Ex vivo CRISPR-Cas9 editing targeting {target} locus. {trial_detail}",
     ["crispr", "gene editing", "ex vivo"]),

    ("AAV gene therapy",
     "Adeno-associated virus (AAV) gene replacement for {target} deficiency. {trial_detail}",
     ["gene therapy", "aav", "lentiviral"]),
]

# (target_name, is_validated, indication_affinity)
TARGETS = [
    ("HER2 (ERBB2)",  True,  ["oncology"]),
    ("PD-L1",         True,  ["oncology"]),
    ("PD-1",          True,  ["oncology"]),
    ("CD19",          True,  ["oncology"]),
    ("CD38",          True,  ["oncology"]),
    ("KRAS G12C",     True,  ["oncology"]),
    ("EGFR",          True,  ["oncology"]),
    ("VEGFR",         True,  ["oncology"]),
    ("BRAF",          True,  ["oncology"]),
    ("BCR-ABL",       True,  ["oncology"]),
    ("BTK",           True,  ["oncology", "immunology"]),
    ("JAK",           True,  ["oncology", "immunology"]),
    ("PARP",          True,  ["oncology"]),
    ("CDK4/CDK6",     True,  ["oncology"]),
    ("CTLA-4",        True,  ["oncology"]),
    ("IL-6",          True,  ["immunology"]),
    ("IL-17",         True,  ["immunology"]),
    ("IL-23",         True,  ["immunology"]),
    ("TNF-alpha",     True,  ["immunology"]),
    ("GLP-1",         True,  ["metabolic"]),
    ("GLP-1/GIP",     True,  ["metabolic"]),
    ("PCSK9",         True,  ["cardiovascular"]),
    ("amyloid beta",  True,  ["neurology"]),
    ("SGLT2",         True,  ["metabolic"]),
    ("CCR5",          True,  ["infectious"]),
    # Novel / unvalidated
    ("TREM2",         False, ["neurology"]),
    ("LRRK2",         False, ["neurology"]),
    ("TDP-43",        False, ["neurology"]),
    ("PIEZO1",        False, ["rare_disease"]),
    ("SLC6A8",        False, ["rare_disease"]),
    ("ENPP1",         False, ["oncology", "immunology"]),
    ("STING",         False, ["oncology", "immunology"]),
    ("LIF",           False, ["oncology"]),
    ("CCN2",          False, ["rare_disease"]),
    ("ACVR1",         False, ["rare_disease"]),
    ("ANGPTL3",       False, ["cardiovascular"]),
    ("NLRP3",         False, ["immunology", "metabolic"]),
]

TRIAL_DETAILS = {
    "preclinical": [
        "IND-enabling studies underway. Rodent PK/PD complete.",
        "GLP toxicology studies ongoing. IND filing expected Q3.",
        "Lead optimisation complete. Candidate nominated.",
    ],
    "phase1": [
        "SAD/MAD study in healthy volunteers. Well tolerated at 3 dose levels.",
        "Phase 1 dose escalation, 28 patients enrolled. No DLTs observed.",
        "First-in-human study. PK profile supports once-weekly dosing.",
        "Phase 1 complete. MTD established. Biomarker data encouraging.",
    ],
    "phase2": [
        "Randomised Phase 2, n=120. Primary endpoint ORR 38% vs 16% control.",
        "Phase 2 single-arm. CR rate 54%. Durable responses at 12 months.",
        "Phase 2 missed primary endpoint. Secondary endpoints show trend.",
        "Phase 2 interim: 42% responders. IDMC recommends continuation.",
        "Phase 2 n=200. Statistically significant improvement on primary endpoint.",
    ],
    "phase3": [
        "Phase 3 pivotal trial n=2400. Met primary endpoint p<0.001.",
        "Phase 3 complete. NDA submission planned 2026.",
        "Phase 3 failed primary endpoint. Post-hoc subgroup shows benefit.",
        "Phase 3 n=1800. Breakthrough therapy designation. Priority review.",
        "Phase 3 superiority trial vs SoC. Co-primary endpoints both met.",
    ],
}

def generate_programs(n: int = 1000) -> list[dict]:
    programs = []
    pid = 1
    combos = list(itertools.product(STAGES, INDICATIONS))

    for i in range(n):
        stage, indication = combos[i % len(combos)]

        # Pick a platform (weighted slightly toward common ones)
        platform_name, desc_tmpl, _ = random.choice(PLATFORMS)

        # Pick a target — prefer targets that match the indication
        matching = [t for t in TARGETS if not t[2] or indication in t[2]]
        if not matching:
            matching = TARGETS
        target_name, is_validated, _ = random.choice(matching)

        trial_detail = random.choice(TRIAL_DETAILS[stage])

        description = desc_tmpl.format(target=target_name, trial_detail=trial_detail)

        programs.append({
            "_id": f"SIM-{pid:04d}",
            "title": f"{platform_name} targeting {target_name} in {IND_LABEL.get(indication, indication)}",
            "description": description,
            "clinical_stage": stage,
            "indication": indication,
            "source": "clinical_trials",
            "_meta": {
                "platform": platform_name,
                "target": target_name,
                "is_validated_target": is_validated,
            },
        })
        pid += 1

    return programs

# ─────────────────────────────────────────────────────────────────────────────
# 2. RUN INFERENCE
# ─────────────────────────────────────────────────────────────────────────────
print("Loading model …", flush=True)
from src.learning.decision_model import SuccessPredictor

model = SuccessPredictor()
metrics = model.train()
print(f"Model ready  AUC={metrics.get('auc_roc',0):.3f}  n={metrics.get('n_train',0):,}", flush=True)

N_PROGRAMS = 10_000
programs = generate_programs(N_PROGRAMS)
print(f"Scoring {len(programs)} programs …", flush=True)

records = []
for i, prog in enumerate(programs, 1):
    if i % 1000 == 0:
        print(f"  {i}/{N_PROGRAMS} …", flush=True)
    r = model.explain(prog)
    tech = r["technology"]
    bio  = r["biology"]
    sig  = r.get("signals", {})
    frt  = r.get("frontier", {})
    safety = r.get("safety_profile", {})
    lessons = r.get("historical_lessons", [])
    records.append({
        "id":           prog["_id"],
        "title":        prog["title"],
        "description":  prog["description"],
        "stage":        prog["clinical_stage"],
        "indication":   prog["indication"],
        "platform":     prog["_meta"]["platform"],
        "target":       prog["_meta"]["target"],
        "validated":    prog["_meta"]["is_validated_target"],
        "score":        r["p_success"],
        "verdict":      r["verdict"],
        "calibration":  r["calibration"],
        "summary":      r.get("summary", ""),
        # rich explain fields
        "fit_rationale":  tech.get("fit_rationale", ""),
        "fit_score":      tech.get("fit_score", 0),
        "is_clearcut":    tech.get("is_clearcut", False),
        "is_bleeding_edge": tech.get("is_bleeding_edge", False),
        "target_status":  bio.get("target_status", "unknown"),
        "detected_targets": bio.get("detected_targets", []),
        "signals_completion": sig.get("completion", []),
        "signals_failure":    sig.get("failure", []),
        "signals_safety":     sig.get("safety", []),
        "frontier_modality":  frt.get("modality", ""),
        "frontier_in_use":    frt.get("in_use", []),
        "frontier_not_using": frt.get("not_using", []),
        "safety_risks":       safety.get("risks", []),
        "safety_summary":     safety.get("summary", ""),
        "lessons":            [{"lesson": l.get("lesson",""), "outcome": l.get("outcome","")} for l in lessons[:3]],
    })

print(f"Done. {sum(1 for r in records if r['verdict']=='GO')} GO  /  "
      f"{sum(1 for r in records if r['verdict']=='NO-GO')} NO-GO", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# 3. AGGREGATE STATS
# ─────────────────────────────────────────────────────────────────────────────

def group_stats(records, key):
    g = defaultdict(list)
    for r in records:
        g[r[key]].append(r["score"])
    out = []
    for k, scores in sorted(g.items()):
        go = sum(1 for s in scores if s >= 0.5)
        out.append({
            "label": k, "n": len(scores),
            "go_rate": go / len(scores),
            "avg": sum(scores) / len(scores),
            "med": sorted(scores)[len(scores)//2],
        })
    out.sort(key=lambda x: -x["go_rate"])
    return out

stage_stats      = group_stats(records, "stage")
indication_stats = group_stats(records, "indication")
platform_stats   = group_stats(records, "platform")

# Calibration factor frequency
cal_counts: dict[str, dict] = defaultdict(lambda: {"pos": 0, "neg": 0, "n": 0})
for r in records:
    for c in r["calibration"]:
        key = c["factor"]
        cal_counts[key]["n"] += 1
        if c["adjustment"].startswith("+"):
            cal_counts[key]["pos"] += 1
        else:
            cal_counts[key]["neg"] += 1

# Score distribution buckets
buckets = [0]*10
for r in records:
    idx = min(int(r["score"] * 10), 9)
    buckets[idx] += 1

# Validated vs unvalidated target
val_go   = sum(1 for r in records if r["validated"] and r["verdict"]=="GO")
val_n    = sum(1 for r in records if r["validated"])
unval_go = sum(1 for r in records if not r["validated"] and r["verdict"]=="GO")
unval_n  = sum(1 for r in records if not r["validated"])

# ─────────────────────────────────────────────────────────────────────────────
# 4. KEY LESSONS
# ─────────────────────────────────────────────────────────────────────────────
total_go = sum(1 for r in records if r["verdict"] == "GO")
lessons = []

# Stage
for s in stage_stats:
    lbl = STAGE_LABEL.get(s["label"], s["label"])
    verb = "most likely" if s["go_rate"] > 0.65 else ("unlikely" if s["go_rate"] < 0.35 else "borderline")
    lessons.append({
        "category": "Stage",
        "finding": f"{lbl} programs are {verb} to get GO ({s['go_rate']:.0%} GO rate, n={s['n']})",
        "score": s["go_rate"],
    })

# Indication
for ind in indication_stats[:3]:
    lbl = IND_LABEL.get(ind["label"], ind["label"])
    lessons.append({
        "category": "Indication",
        "finding": f"{lbl} has the {'highest' if ind == indication_stats[0] else 'high'} GO rate: "
                   f"{ind['go_rate']:.0%} (avg score {ind['avg']:.1%})",
        "score": ind["go_rate"],
    })
for ind in indication_stats[-2:]:
    lbl = IND_LABEL.get(ind["label"], ind["label"])
    lessons.append({
        "category": "Indication",
        "finding": f"{lbl} is the hardest indication: {ind['go_rate']:.0%} GO rate — high attrition",
        "score": ind["go_rate"],
    })

# Platform
for plat in platform_stats[:2]:
    lessons.append({
        "category": "Platform",
        "finding": f"'{plat['label']}' is the top-performing platform: {plat['go_rate']:.0%} GO rate",
        "score": plat["go_rate"],
    })
for plat in platform_stats[-2:]:
    lessons.append({
        "category": "Platform",
        "finding": f"'{plat['label']}' has the lowest GO rate: {plat['go_rate']:.0%} — consider risk-sharing",
        "score": plat["go_rate"],
    })

# Validated target
if val_n > 0:
    lessons.append({
        "category": "Biology",
        "finding": f"Validated targets: {val_go/val_n:.0%} GO rate vs "
                   f"{unval_go/unval_n:.0%} for novel targets — a {abs(val_go/val_n - unval_go/unval_n):.0%} gap",
        "score": val_go / val_n,
    })

lessons.sort(key=lambda x: -x["score"])

# ─────────────────────────────────────────────────────────────────────────────
# 5. HTML GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def bar_chart_js(label: str, labels: list[str], go_rates: list[float],
                 ns: list[int], chart_id: str) -> str:
    lbl_js   = json.dumps(labels)
    go_js    = json.dumps([round(r*100, 1) for r in go_rates])
    nogo_js  = json.dumps([round((1-r)*100, 1) for r in go_rates])
    ns_js    = json.dumps(ns)
    return f"""
    <div class="chart-box">
      <h3>{label}</h3>
      <canvas id="{chart_id}" height="220"></canvas>
    </div>
    <script>
    (function(){{
      var ctx = document.getElementById('{chart_id}').getContext('2d');
      new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: {lbl_js},
          datasets: [
            {{ label: 'GO %', data: {go_js}, backgroundColor: 'rgba(34,197,94,0.75)', borderRadius: 4 }},
            {{ label: 'NO-GO %', data: {nogo_js}, backgroundColor: 'rgba(239,68,68,0.55)', borderRadius: 4 }},
          ]
        }},
        options: {{
          responsive: true,
          plugins: {{
            legend: {{ labels: {{ color: '#e2e8f0' }} }},
            tooltip: {{
              callbacks: {{
                afterLabel: function(ctx) {{
                  var ns = {ns_js};
                  return 'n = ' + ns[ctx.dataIndex];
                }}
              }}
            }}
          }},
          scales: {{
            x: {{ stacked: true, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
            y: {{ stacked: true, max: 100, ticks: {{ color: '#94a3b8', callback: v => v+'%' }},
                  grid: {{ color: '#334155' }} }}
          }}
        }}
      }});
    }})();
    </script>
    """

def dist_chart(buckets: list[int]) -> str:
    labels = [f"{i*10}–{i*10+9}%" for i in range(10)]
    colours = []
    for i in range(10):
        mid = (i * 10 + 5) / 100
        if mid >= 0.65:
            colours.append("rgba(34,197,94,0.8)")
        elif mid >= 0.45:
            colours.append("rgba(245,158,11,0.8)")
        else:
            colours.append("rgba(239,68,68,0.7)")
    return f"""
    <div class="chart-box">
      <h3>Score Distribution (all {N_PROGRAMS:,} programs)</h3>
      <canvas id="distChart" height="200"></canvas>
    </div>
    <script>
    (function(){{
      var ctx = document.getElementById('distChart').getContext('2d');
      new Chart(ctx, {{
        type: 'bar',
        data: {{
          labels: {json.dumps(labels)},
          datasets: [{{
            label: 'Programs',
            data: {json.dumps(buckets)},
            backgroundColor: {json.dumps(colours)},
            borderRadius: 4,
          }}]
        }},
        options: {{
          responsive: true,
          plugins: {{ legend: {{ display: false }} }},
          scales: {{
            x: {{ ticks: {{ color: '#94a3b8', font: {{ size: 11 }} }},
                  grid: {{ color: '#1e293b' }} }},
            y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }}
          }}
        }}
      }});
    }})();
    </script>
    """

def lessons_html(lessons: list[dict]) -> str:
    cats = {}
    for l in lessons:
        cats.setdefault(l["category"], []).append(l)
    html = ""
    cat_colours = {"Stage": "#38bdf8", "Indication": "#a78bfa",
                   "Platform": "#34d399", "Biology": "#fb923c"}
    for cat, items in cats.items():
        colour = cat_colours.get(cat, "#e2e8f0")
        html += f'<div class="lesson-group"><h4 style="color:{colour}">{cat}</h4><ul>'
        for l in items:
            pct = l["score"]
            bar_col = "#22c55e" if pct > 0.6 else ("#f59e0b" if pct > 0.4 else "#ef4444")
            bar_w   = int(pct * 100)
            html += (
                f'<li>'
                f'<div class="lesson-bar-bg"><div class="lesson-bar" '
                f'style="width:{bar_w}%;background:{bar_col}"></div></div>'
                f'<span>{l["finding"]}</span>'
                f'</li>'
            )
        html += "</ul></div>"
    return html

def cal_factor_table(cal_counts: dict) -> str:
    rows = sorted(cal_counts.items(), key=lambda x: -x[1]["n"])
    html = '<table class="cal-table"><thead><tr><th>Calibration Factor</th><th>Fires in</th><th>Always +</th><th>Always −</th></tr></thead><tbody>'
    total = len(records)
    for factor, d in rows:
        freq = d["n"] / total
        sign = "+" if d["pos"] >= d["neg"] else "−"
        sign_col = "#22c55e" if sign == "+" else "#ef4444"
        html += (
            f'<tr><td>{factor}</td>'
            f'<td>{freq:.1%} of programs</td>'
            f'<td style="color:#22c55e">{d["pos"]}</td>'
            f'<td style="color:#ef4444">{d["neg"]}</td></tr>'
        )
    html += "</tbody></table>"
    return html

# ── Build page ────────────────────────────────────────────────────────────────
stage_labels  = [STAGE_LABEL.get(s["label"], s["label"]) for s in stage_stats]
stage_go      = [s["go_rate"] for s in stage_stats]
stage_ns      = [s["n"] for s in stage_stats]

ind_labels    = [IND_LABEL.get(s["label"], s["label"]) for s in indication_stats]
ind_go        = [s["go_rate"] for s in indication_stats]
ind_ns        = [s["n"] for s in indication_stats]

plat_labels   = [s["label"] for s in platform_stats]
plat_go       = [s["go_rate"] for s in platform_stats]
plat_ns       = [s["n"] for s in platform_stats]

ts = datetime.now().strftime("%Y-%m-%d %H:%M")
total_n = len(records)
n_go_total = sum(1 for r in records if r["verdict"] == "GO")
n_nogo_total = total_n - n_go_total
avg_score = sum(r["score"] for r in records) / total_n

HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>REC-DECISION · 10,000-Program Trend Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg:#0f172a; --surface:#1e293b; --surface2:#293548; --border:#334155;
    --text:#e2e8f0; --muted:#64748b; --go:#22c55e; --nogo:#ef4444;
    --amber:#f59e0b; --accent:#38bdf8;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; font-size:14px; line-height:1.6; }}

  header {{ background:linear-gradient(135deg,#1e3a5f,#0f172a); padding:32px 40px; border-bottom:1px solid var(--border); }}
  header h1 {{ font-size:22px; font-weight:700; color:var(--accent); }}
  header .sub {{ color:var(--muted); font-size:12px; margin-top:4px; }}

  .stats-bar {{ display:flex; gap:32px; padding:20px 40px; background:var(--surface); border-bottom:1px solid var(--border); flex-wrap:wrap; }}
  .stat .val {{ font-size:36px; font-weight:800; }}
  .stat .lbl {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }}
  .go-val {{ color:var(--go); }} .nogo-val {{ color:var(--nogo); }}
  .accent-val {{ color:var(--accent); }} .amber-val {{ color:var(--amber); }}

  main {{ max-width:1100px; margin:0 auto; padding:32px 24px 80px; }}
  h2 {{ font-size:16px; font-weight:700; color:var(--accent); text-transform:uppercase;
        letter-spacing:.06em; margin:40px 0 16px; border-bottom:1px solid var(--border); padding-bottom:8px; }}

  .grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:24px; }}
  @media(max-width:700px){{ .grid-2 {{ grid-template-columns:1fr; }} }}
  .chart-box {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px; }}
  .chart-box h3 {{ font-size:13px; color:var(--muted); margin-bottom:14px; text-transform:uppercase; letter-spacing:.06em; }}

  .lesson-group {{ margin-bottom:24px; }}
  .lesson-group h4 {{ font-size:13px; font-weight:700; margin-bottom:10px; text-transform:uppercase; letter-spacing:.06em; }}
  .lesson-group ul {{ list-style:none; }}
  .lesson-group li {{ display:flex; flex-direction:column; margin-bottom:10px; }}
  .lesson-bar-bg {{ height:4px; background:var(--surface2); border-radius:2px; margin-bottom:5px; width:100%; }}
  .lesson-bar {{ height:4px; border-radius:2px; }}
  .lesson-group li span {{ font-size:13px; color:var(--text); }}

  .cal-table {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:8px; }}
  .cal-table th {{ text-align:left; color:var(--muted); font-size:11px; padding:6px 8px;
                   border-bottom:1px solid var(--border); text-transform:uppercase; }}
  .cal-table td {{ padding:7px 8px; border-bottom:1px solid #1e293b; }}
  .cal-table tr:hover td {{ background:var(--surface2); }}

  .insight-box {{ background:rgba(56,189,248,.07); border:1px solid rgba(56,189,248,.2);
                  border-radius:10px; padding:18px 22px; font-size:13px; color:#bae6fd;
                  margin-bottom:24px; line-height:1.8; }}

  footer {{ text-align:center; padding:24px; color:var(--muted); font-size:12px;
            border-top:1px solid var(--border); }}
</style>
</head>
<body>

<header>
  <h1>REC-DECISION · 10,000-Program GO/NO-GO Trend Report</h1>
  <div class="sub">
    {ts} &nbsp;·&nbsp;
    Model: SuccessPredictor_ensemble &nbsp;·&nbsp;
    AUC-ROC: {metrics.get('auc_roc',0):.3f} &nbsp;·&nbsp;
    Trained on {metrics.get('n_train',0):,} real-world samples
  </div>
</header>

<div class="stats-bar">
  <div class="stat"><span class="val accent-val">{total_n:,}</span><span class="lbl">Programs scored</span></div>
  <div class="stat"><span class="val go-val">{n_go_total:,}</span><span class="lbl">GO decisions</span></div>
  <div class="stat"><span class="val nogo-val">{n_nogo_total:,}</span><span class="lbl">NO-GO decisions</span></div>
  <div class="stat"><span class="val amber-val">{n_go_total/total_n:.1%}</span><span class="lbl">Overall GO rate</span></div>
  <div class="stat"><span class="val" style="color:#a78bfa">{avg_score:.1%}</span><span class="lbl">Avg success probability</span></div>
</div>

<main>

<h2>Key Lessons</h2>
<div class="insight-box">
  These patterns emerge from scoring {N_PROGRAMS:,} varied drug programs across all stages, indications, and platforms.
  Each bar shows the GO rate — the fraction of programs in that category the model recommends advancing.
  Use this to calibrate portfolio decisions: high GO rates mean the model finds the risk profile acceptable;
  low GO rates signal systematic risk the model has learned from historical attrition data.
</div>
{lessons_html(lessons)}

<h2>GO / NO-GO by Clinical Stage</h2>
{bar_chart_js("Clinical Stage", stage_labels, stage_go, stage_ns, "stageChart")}

<h2>GO / NO-GO by Indication</h2>
{bar_chart_js("Therapeutic Indication", ind_labels, ind_go, ind_ns, "indChart")}

<h2>GO / NO-GO by Platform / Modality</h2>
{bar_chart_js("Drug Platform", plat_labels, plat_go, plat_ns, "platChart")}

<h2>Score Distribution</h2>
{dist_chart(buckets)}

<h2>Validated vs Novel Targets</h2>
<div class="grid-2">
  <div class="chart-box">
    <h3>GO rate by target validation status</h3>
    <canvas id="targetChart" height="200"></canvas>
  </div>
  <div class="chart-box" style="display:flex;flex-direction:column;justify-content:center;gap:12px;padding:28px;">
    <div style="font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">Validated targets (n={val_n})</div>
    <div style="font-size:40px;font-weight:800;color:var(--go)">{val_go/val_n:.1%} GO</div>
    <div style="height:1px;background:var(--border);margin:8px 0"></div>
    <div style="font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">Novel / unvalidated targets (n={unval_n})</div>
    <div style="font-size:40px;font-weight:800;color:var(--amber)">{unval_go/unval_n:.1%} GO</div>
    <div style="margin-top:12px;font-size:13px;color:#94a3b8">
      Validated target biology de-risks programs by ~{abs(val_go/val_n - unval_go/unval_n):.0%}.
      A validated target is the single strongest predictor in the model.
    </div>
  </div>
</div>
<script>
(function(){{
  var ctx = document.getElementById('targetChart').getContext('2d');
  new Chart(ctx, {{
    type:'doughnut',
    data:{{
      labels:['Validated GO','Validated NO-GO','Novel GO','Novel NO-GO'],
      datasets:[{{
        data:[{val_go},{val_n-val_go},{unval_go},{unval_n-unval_go}],
        backgroundColor:['rgba(34,197,94,0.8)','rgba(239,68,68,0.4)',
                         'rgba(245,158,11,0.7)','rgba(239,68,68,0.25)'],
        borderWidth:0,
      }}]
    }},
    options:{{
      responsive:true,
      plugins:{{
        legend:{{ labels:{{ color:'#e2e8f0', font:{{ size:12 }} }} }}
      }}
    }}
  }});
}})();
</script>

<h2>Score Calibration — What Fires Most Often?</h2>
<div class="chart-box">
  <h3>Factors applied by the model during scoring (across all {N_PROGRAMS:,} programs)</h3>
  {cal_factor_table(cal_counts)}
</div>

</main>

<footer>REC-DECISION · {N_PROGRAMS:,}-Program Simulation · {ts}</footer>
</body>
</html>
"""

# ── Individual program table (JSON embedded for JS filtering) ─────────────
table_rows_json = json.dumps([
    {
        "id":           r["id"],
        "title":        r["title"],
        "description":  r["description"],
        "stage":        STAGE_LABEL.get(r["stage"], r["stage"]),
        "indication":   IND_LABEL.get(r["indication"], r["indication"]),
        "platform":     r["platform"],
        "target":       r["target"],
        "validated":    r["validated"],
        "score":        round(r["score"] * 100, 1),
        "verdict":      r["verdict"],
        "calibration":  r["calibration"],
        "summary":      r["summary"],
        # WHY / HOW fields
        "fit_rationale":    r.get("fit_rationale", ""),
        "fit_score":        round(r.get("fit_score", 0) * 100),
        "is_clearcut":      r.get("is_clearcut", False),
        "is_bleeding_edge": r.get("is_bleeding_edge", False),
        "target_status":    r.get("target_status", "unknown"),
        "detected_targets": r.get("detected_targets", []),
        "sig_completion":   r.get("signals_completion", []),
        "sig_failure":      r.get("signals_failure", []),
        "sig_safety":       r.get("signals_safety", []),
        "safety_summary":   r.get("safety_summary", ""),
        "safety_risks":     r.get("safety_risks", []),
        "frontier_modality": r.get("frontier_modality", ""),
        "frontier_in_use":   [{"tech": e.get("tech",""), "status": e.get("status",""),
                                "pursuit": e.get("pursuit_level",""), "note": e.get("note","")}
                               for e in r.get("frontier_in_use", [])[:3]],
        "frontier_missing":  [{"tech": e.get("tech",""), "status": e.get("status",""),
                                "pursuit": e.get("pursuit_level","")}
                               for e in r.get("frontier_not_using", [])[:3]],
        "lessons":           r.get("lessons", []),
    }
    for r in sorted(records, key=lambda x: -x["score"])
])

INDIVIDUAL_SECTION = f"""
<h2>All {N_PROGRAMS:,} Programs — Individual Detail &amp; Investment Thesis</h2>
<div class="table-controls">
  <input id="searchBox" type="text" placeholder="Search by ID, target, platform, indication, hypothesis text …" oninput="filterTable()">
  <select id="verdictFilter" onchange="filterTable()">
    <option value="">All verdicts</option>
    <option value="GO">GO only</option>
    <option value="NO-GO">NO-GO only</option>
  </select>
  <select id="stageFilter" onchange="filterTable()">
    <option value="">All stages</option>
    <option>Preclinical</option><option>Phase 1</option>
    <option>Phase 2</option><option>Phase 3</option>
  </select>
  <select id="indFilter" onchange="filterTable()">
    <option value="">All indications</option>
    <option>Oncology</option><option>Rare Disease</option>
    <option>Immunology</option><option>Neurology</option>
    <option>Cardiovascular</option><option>Metabolic</option>
    <option>Infectious Disease</option>
  </select>
  <select id="targetFilter" onchange="filterTable()">
    <option value="">All target types</option>
    <option value="validated">Validated targets</option>
    <option value="unvalidated">Novel targets</option>
  </select>
  <span id="countLabel" class="count-label"></span>
</div>
<div id="programTable"></div>

<script>
var ALL_PROGRAMS = {table_rows_json};
var filtered = ALL_PROGRAMS.slice();
var PAGE = 50, page = 0;

function sc(s) {{
  return s >= 65 ? '#22c55e' : s >= 45 ? '#f59e0b' : '#ef4444';
}}
function escH(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}
function calRows(cal) {{
  if (!cal||!cal.length) return '<span style="color:#64748b">None</span>';
  return cal.map(function(c) {{
    var col = c.adjustment.startsWith('+') ? '#22c55e' : '#ef4444';
    return '<span style="color:'+col+';font-weight:700">'+c.adjustment+'</span> '+escH(c.factor);
  }}).join('<br>');
}}
function tags(arr, col) {{
  if (!arr||!arr.length) return '<span style="color:#64748b">—</span>';
  return arr.map(function(t) {{
    return '<span style="background:'+col+'22;color:'+col+';padding:1px 7px;border-radius:8px;font-size:11px;margin-right:4px">'+escH(t)+'</span>';
  }}).join('');
}}
function frontierRows(items, col) {{
  if (!items||!items.length) return '<span style="color:#64748b">—</span>';
  return items.map(function(e) {{
    var note = e.note ? ' <span style="color:#64748b;font-size:11px">— '+escH(e.note)+'</span>' : '';
    return '<div style="margin-bottom:4px"><span style="color:'+col+';font-weight:600">'+escH(e.tech)+'</span>'
         + ' <span style="color:#64748b;font-size:11px">['+escH(e.status)+' · '+escH(e.pursuit)+']</span>'+note+'</div>';
  }}).join('');
}}
function lessonRows(lessons) {{
  if (!lessons||!lessons.length) return '<span style="color:#64748b">No historical lessons found.</span>';
  return lessons.map(function(l) {{
    var outcome = l.outcome ? ' <span style="color:#64748b;font-size:11px">('+escH(l.outcome)+')</span>' : '';
    return '<div style="margin-bottom:5px;padding-left:10px;border-left:2px solid #38bdf8;font-size:12px;color:#94a3b8">'+escH(l.lesson)+outcome+'</div>';
  }}).join('');
}}

var IND_CT_MAP = {{
  'Oncology':'cancer','Rare Disease':'rare+disease','Immunology':'autoimmune',
  'Neurology':'neurological','Cardiovascular':'cardiovascular',
  'Metabolic':'metabolic','Infectious Disease':'infectious+disease'
}};

function resourceLinks(r) {{
  var tgt  = encodeURIComponent(r.target);
  var cond = encodeURIComponent(IND_CT_MAP[r.indication] || r.indication);
  var plat = encodeURIComponent(r.platform);
  var qpm  = encodeURIComponent(r.target + ' ' + r.indication + ' clinical trial');
  var links = [
    {{ url:'https://clinicaltrials.gov/search?cond='+cond+'&term='+tgt,
       icon:'🔬', label:'ClinicalTrials.gov',
       why:'Active &amp; completed trials for <strong>'+escH(r.target)+'</strong> in <strong>'+escH(r.indication)+'</strong> — compare pipeline density and phase distribution' }},
    {{ url:'https://pubmed.ncbi.nlm.nih.gov/?term='+qpm+'&sort=date',
       icon:'📄', label:'PubMed',
       why:'Recent publications on <strong>'+escH(r.target)+'</strong> mechanism, validation, and clinical evidence' }},
    {{ url:'https://www.ebi.ac.uk/chembl/explore/targets?q='+tgt,
       icon:'🧬', label:'ChEMBL Target',
       why:'Compound activity data, selectivity profiles, and known liabilities for <strong>'+escH(r.target)+'</strong>' }},
    {{ url:'https://clinicaltrials.gov/search?term='+tgt+'&aggFilters=phase:3+4',
       icon:'📊', label:'Late-Stage Competitors',
       why:'Phase 3/4 trials on the same target — assess competitive crowding and differentiation need' }},
    {{ url:'https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files',
       icon:'🏛', label:'FDA Approvals',
       why:'Historical approval precedent for <strong>'+escH(r.platform)+'</strong> modality — assess regulatory pathway risk' }}
  ];
  return '<div class="res-grid">'
    + links.map(function(l) {{
        return '<a class="res-card" href="'+l.url+'" target="_blank" rel="noopener noreferrer">'
          + '<div class="res-header"><span class="res-icon">'+l.icon+'</span>'
          + '<span class="res-label">'+l.label+'</span><span class="res-ext">↗</span></div>'
          + '<div class="res-why">'+l.why+'</div></a>';
      }}).join('')
    + '</div>';
}}
function miniBar(pct) {{
  var col = sc(pct);
  return '<div style="height:6px;background:#293548;border-radius:3px;overflow:hidden;margin-top:2px">'
       + '<div style="width:'+pct+'%;height:100%;background:'+col+';border-radius:3px"></div></div>';
}}

function renderTable() {{
  var start = page * PAGE, end = Math.min(start + PAGE, filtered.length);
  var html = '';
  filtered.slice(start, end).forEach(function(r, i) {{
    var brd = r.verdict === 'GO' ? '#22c55e' : '#ef4444';
    var badge = r.verdict === 'GO'
      ? '<span class="badge badge-go">▲ GO</span>'
      : '<span class="badge badge-nogo">▼ NO-GO</span>';
    var vtag = r.validated
      ? '<span class="vtag vtag-val">✓ validated target</span>'
      : '<span class="vtag vtag-nov">⚠ novel target</span>';
    var be = r.is_bleeding_edge
      ? '<span class="vtag" style="background:rgba(56,189,248,.12);color:#38bdf8">bleeding-edge</span>' : '';
    var cc = r.is_clearcut
      ? '<span class="vtag" style="background:rgba(52,211,153,.12);color:#34d399">clear-cut fit</span>' : '';
    var uid = 'r'+(start+i);

    html += '<div class="prog-row" style="border-left:4px solid '+brd+'" id="'+uid+'">';
    // ── header row ──
    html += '<div class="prog-row-header" onclick="td(\''+uid+'\')" style="cursor:pointer">';
    html += '  <div class="prog-row-left">';
    html += '    <span class="prog-id-sm">'+r.id+'</span>';
    html += '    <span class="prog-title-sm">'+escH(r.title)+'</span>';
    html += '    '+vtag+be+cc;
    html += '  </div>';
    html += '  <div class="prog-row-right">';
    html += '    <span class="stage-chip">'+r.stage+'</span>';
    html += '    <span class="ind-chip">'+r.indication+'</span>';
    html += '    <div style="text-align:right;min-width:60px"><span class="score-sm" style="color:'+sc(r.score)+'">'+r.score+'%</span>'+miniBar(r.score)+'</div>';
    html += '    '+badge;
    html += '    <span class="expand-icon" id="ei-'+uid+'">▾</span>';
    html += '  </div>';
    html += '</div>';

    // ── expanded detail ──
    html += '<div class="prog-detail" id="d-'+uid+'" style="display:none">';

    // Hypothesis
    html += '<div class="thesis-section">';
    html += '<div class="thesis-label">📋 Hypothesis</div>';
    html += '<p class="hyp-text">'+escH(r.description)+'</p>';
    html += '</div>';

    // Why GO/NO-GO
    html += '<div class="thesis-section">';
    html += '<div class="thesis-label">🧠 Why '+(r.verdict === 'GO' ? 'GO' : 'NO-GO')+'</div>';
    html += '<p class="summary-sm" style="margin-bottom:8px">'+escH(r.summary)+'</p>';
    html += '<div style="margin-bottom:6px"><strong style="font-size:11px;color:#64748b">SCORE DRIVERS</strong><br>'+calRows(r.calibration)+'</div>';
    if (r.fit_rationale) {{
      html += '<div style="margin-top:6px;font-size:12px;color:#94a3b8;border-left:2px solid #38bdf8;padding-left:8px">'+escH(r.fit_rationale)+'</div>';
    }}
    html += '</div>';

    // Biology
    html += '<div class="detail-grid" style="margin-top:14px">';
    html += '<div class="sub-box">';
    html += '<div class="thesis-label">🎯 Target Biology</div>';
    html += '<div style="margin-bottom:6px"><span style="font-size:11px;color:#64748b">TARGET STATUS: </span>'
          + '<span style="font-weight:600;color:'+(r.target_status==="validated"?"#22c55e":r.target_status==="unvalidated"?"#f59e0b":"#94a3b8")+'">'+r.target_status.toUpperCase()+'</span></div>';
    html += '<div style="margin-bottom:6px"><span style="font-size:11px;color:#64748b">DETECTED: </span>'+tags(r.detected_targets,'#a78bfa')+'</div>';
    if (r.sig_completion&&r.sig_completion.length) html += '<div style="margin-top:4px"><span style="font-size:11px;color:#64748b">POSITIVE SIGNALS: </span>'+tags(r.sig_completion,'#22c55e')+'</div>';
    if (r.sig_failure&&r.sig_failure.length) html += '<div style="margin-top:4px"><span style="font-size:11px;color:#64748b">FAILURE SIGNALS: </span>'+tags(r.sig_failure,'#ef4444')+'</div>';
    if (r.sig_safety&&r.sig_safety.length) html += '<div style="margin-top:4px"><span style="font-size:11px;color:#64748b">SAFETY FLAGS: </span>'+tags(r.sig_safety,'#f59e0b')+'</div>';
    html += '</div>';

    // Safety
    html += '<div class="sub-box">';
    html += '<div class="thesis-label">⚠ Safety Profile</div>';
    if (r.safety_summary) html += '<p style="font-size:12px;color:#94a3b8;margin-bottom:8px">'+escH(r.safety_summary)+'</p>';
    html += tags(r.safety_risks, '#f59e0b');
    if (!r.safety_summary&&(!r.safety_risks||!r.safety_risks.length)) html += '<span style="color:#64748b;font-size:12px">No specific safety concerns flagged.</span>';
    html += '</div>';
    html += '</div>'; // close detail-grid

    // Frontier tech
    html += '<div class="detail-grid" style="margin-top:14px">';
    html += '<div class="sub-box">';
    html += '<div class="thesis-label">🚀 Frontier Tech In Use</div>';
    html += frontierRows(r.frontier_in_use, '#34d399');
    html += '</div>';
    html += '<div class="sub-box">';
    html += '<div class="thesis-label">💡 High-Pursuit Tech NOT Used</div>';
    html += frontierRows(r.frontier_missing, '#f59e0b');
    html += '</div>';
    html += '</div>';

    // Historical lessons
    html += '<div class="sub-box" style="margin-top:14px">';
    html += '<div class="thesis-label">📚 Historical Lessons for This Modality</div>';
    html += lessonRows(r.lessons);
    html += '</div>';

    // Research resource links
    html += '<div style="margin-top:16px">';
    html += '<div class="thesis-label" style="margin-bottom:10px">🔗 Research Resources &amp; Due-Diligence Links</div>';
    html += resourceLinks(r);
    html += '</div>';

    html += '</div>'; // end prog-detail
    html += '</div>'; // end prog-row
  }});

  document.getElementById('programTable').innerHTML = html + renderPager(start, end);
  document.getElementById('countLabel').textContent =
    'Showing ' + (start+1) + '–' + end + ' of ' + filtered.length + ' programs';
}}

function renderPager(start, end) {{
  var tp = Math.ceil(filtered.length / PAGE);
  if (tp <= 1) return '';
  var b = '<div class="pager">';
  if (page > 0) b += '<button onclick="goPage('+(page-1)+')">&laquo; Prev</button>';
  b += '<span style="color:#94a3b8;margin:0 16px">Page '+(page+1)+' / '+tp+'</span>';
  if (end < filtered.length) b += '<button onclick="goPage('+(page+1)+')">Next &raquo;</button>';
  return b + '</div>';
}}

function goPage(p) {{
  page = p;
  renderTable();
  var el = document.getElementById('programTable');
  if (el) window.scrollTo({{top: el.offsetTop - 80, behavior: 'smooth'}});
}}

function td(uid) {{
  var d = document.getElementById('d-'+uid);
  var icon = document.getElementById('ei-'+uid);
  if (d.style.display === 'none') {{ d.style.display = 'block'; icon.textContent = '▴'; }}
  else {{ d.style.display = 'none'; icon.textContent = '▾'; }}
}}

function filterTable() {{
  var q  = document.getElementById('searchBox').value.toLowerCase();
  var vf = document.getElementById('verdictFilter').value;
  var sf = document.getElementById('stageFilter').value;
  var inf= document.getElementById('indFilter').value;
  var tf = document.getElementById('targetFilter').value;
  filtered = ALL_PROGRAMS.filter(function(r) {{
    if (vf && r.verdict !== vf) return false;
    if (sf && r.stage !== sf) return false;
    if (inf && r.indication !== inf) return false;
    if (tf === 'validated' && !r.validated) return false;
    if (tf === 'unvalidated' && r.validated) return false;
    if (q) {{
      var hay = [r.id,r.title,r.target,r.platform,r.indication,r.description,
                 r.summary,r.fit_rationale,(r.detected_targets||[]).join(' ')].join(' ').toLowerCase();
      if (hay.indexOf(q) === -1) return false;
    }}
    return true;
  }});
  page = 0;
  renderTable();
}}

filterTable();
</script>
"""

HTML = HTML.replace('</main>', INDIVIDUAL_SECTION + '</main>')

HTML = HTML.replace('</style>', """
  .table-controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:16px; }
  .table-controls input, .table-controls select {
    background:var(--surface); border:1px solid var(--border); color:var(--text);
    border-radius:8px; padding:8px 12px; font-size:13px; outline:none;
  }
  .table-controls input { flex:1; min-width:220px; }
  .table-controls input:focus, .table-controls select:focus { border-color:var(--accent); }
  .count-label { color:var(--muted); font-size:12px; margin-left:auto; white-space:nowrap; }
  .prog-row { background:var(--surface); border:1px solid var(--border); border-radius:10px;
              margin-bottom:8px; overflow:hidden; transition: border-color .15s; }
  .prog-row:hover { border-color:#475569; }
  .prog-row-header { display:flex; justify-content:space-between; align-items:center;
                     padding:12px 16px; gap:12px; }
  .prog-row-left { display:flex; align-items:center; gap:8px; flex:1; min-width:0; flex-wrap:wrap; }
  .prog-row-right { display:flex; align-items:center; gap:10px; flex-shrink:0; }
  .prog-id-sm { font-size:11px; color:var(--muted); font-weight:600; white-space:nowrap; }
  .prog-title-sm { font-size:13px; font-weight:500; white-space:nowrap; overflow:hidden;
                   text-overflow:ellipsis; max-width:300px; }
  .vtag { font-size:10px; padding:2px 7px; border-radius:8px; white-space:nowrap; font-weight:600; }
  .vtag-val { background:rgba(34,197,94,.12); color:#22c55e; }
  .vtag-nov { background:rgba(245,158,11,.12); color:#f59e0b; }
  .stage-chip { font-size:11px; padding:2px 8px; background:var(--surface2);
                border-radius:6px; color:var(--muted); white-space:nowrap; }
  .ind-chip { font-size:11px; padding:2px 8px; background:rgba(56,189,248,.1);
              border-radius:6px; color:#38bdf8; white-space:nowrap; }
  .score-sm { font-size:18px; font-weight:800; }
  .expand-icon { font-size:16px; color:var(--muted); }
  .prog-detail { padding:16px 20px 20px; border-top:1px solid var(--border); background:#182032; }
  .thesis-section { margin-bottom:14px; }
  .thesis-label { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.08em;
                  color:var(--muted); margin-bottom:6px; }
  .hyp-text { font-size:13px; color:#94a3b8; font-style:italic; line-height:1.7; }
  .summary-sm { font-size:13px; color:#bae6fd; line-height:1.6; }
  .detail-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  @media(max-width:640px){ .detail-grid { grid-template-columns:1fr; } }
  .sub-box { background:var(--surface2); border-radius:8px; padding:14px 16px; }
  .badge { display:inline-block; padding:3px 10px; border-radius:12px; font-size:12px; font-weight:700; }
  .badge-go { background:rgba(34,197,94,.15); color:#22c55e; border:1px solid #22c55e; }
  .badge-nogo { background:rgba(239,68,68,.15); color:#ef4444; border:1px solid #ef4444; }
  .pager { display:flex; justify-content:center; align-items:center; padding:18px 0; }
  .pager button { background:var(--surface2); border:1px solid var(--border); color:var(--text);
                  border-radius:8px; padding:8px 20px; cursor:pointer; font-size:13px; }
  .pager button:hover { border-color:var(--accent); color:var(--accent); }
  .res-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; margin-top:4px; }
  .res-card { display:flex; flex-direction:column; gap:6px; background:var(--surface);
              border:1px solid var(--border); border-radius:8px; padding:11px 13px;
              text-decoration:none; color:inherit; transition:border-color .15s,background .15s; }
  .res-card:hover { border-color:var(--accent); background:#1a2944; }
  .res-header { display:flex; align-items:center; gap:6px; }
  .res-icon { font-size:14px; }
  .res-label { font-size:12px; font-weight:700; color:var(--accent); flex:1; }
  .res-ext { font-size:11px; color:var(--muted); }
  .res-why { font-size:11px; color:#94a3b8; line-height:1.55; }
  .res-why strong { color:#cbd5e1; font-weight:600; }
</style>""")

out = Path("data/bulk_inference_report.html")
out.write_text(HTML)
print(f"\nReport written → {out.resolve()}")
print(f"Open in browser:  file://{out.resolve()}")
