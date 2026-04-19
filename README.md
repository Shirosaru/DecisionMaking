# Pharma Decision Intelligence Platform

A data-driven decision engine for drug development and bioventure programs. Combines a Bayesian sequential decision framework, an ML-powered success predictor, a multi-agent AI review board, and a modality trend intelligence layer — trained on 19,000+ public records from ChEMBL, ClinicalTrials.gov, FDA/EMA approvals, and VC portfolios.

---

## What It Does

| Capability | Entry Point | Description |
|---|---|---|
| **Predict program success** | `model.explain(row)` | P(success) with GO/NO-GO verdict, safety profile, and engineering trade-off notes |
| **Historical lessons** | `explain()["historical_lessons"]` | Cross-modality failure patterns mined from DB — what killed ancestor formats and how that risk transfers to yours |
| **Modality trend intelligence** | `model.modality_trends()` | Heat scores, emergence signals, and per-modality breakdowns across 40+ molecular formats |
| **Bayesian sequential decisions** | `pharma_decision_engine.py` | Sequential KILL / CONTINUE / INVEST with expected-value gating |
| **Multi-agent review board** | `run_with_review_board.py` | Three parallel AI agents (Clinical, Financial, Risk) synthesise into a decision brief |
| **Portfolio analytics** | `run_pipeline.py analyse` | Kill rates by stage/indication, phase transition probabilities, cost-of-kill analysis |

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/YOUR_USERNAME/DecisionMaking.git
cd DecisionMaking
pip install -r requirements.txt
```

### 2. Collect data and train

```bash
python run_pipeline.py collect   # Pulls from ChEMBL, ClinicalTrials, FDA, EMA, VC sites
python run_pipeline.py train     # Trains LR + GradientBoosting ensemble (~AUC 0.79)
```

> Data collection requires internet access. The trained DB and model artefacts are excluded from the repository (see `.gitignore`).

### 3. Score a program

```python
from src.learning.decision_model import SuccessPredictor

model = SuccessPredictor()
model.train()   # uses default DB path; no arguments needed

result = model.explain({
    "indication":     "oncology",
    "mechanism":      "antibody",
    "clinical_stage": "phase1",
    "raw_text": (
        "HER2 Probody drug conjugate masked ADC CytomX protease-activated "
        "MMAE cleavable linker breast cancer phase 1 dose escalation"
    ),
})

print(result["verdict"])              # GO / NO-GO
print(result["p_success"])            # 0.0–1.0
print(result["safety_profile"])       # target + modality risks, overall tier
print(result["historical_lessons"])   # lessons from ancestor format failures
```

### 4. Trend report

```bash
python run_pipeline.py trend
```

```python
trends = model.modality_trends(top_n=10)
for fmt in trends["hot_formats"]:
    print(fmt["format"], fmt["heat_score"], fmt["success_rate"])
```

### 5. Multi-agent review board (requires Anthropic API key)

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # see .env.example
python run_with_review_board.py
```

---

## Module Map

```
src/
├── collectors/          # Data ingestion (ChEMBL, ClinicalTrials, FDA, EMA, VC, SEC)
├── processors/
│   └── feature_extractor.py   # 40+ format patterns, target safety profiles,
│                              # frontier context, modality toxicity catalogue
├── learning/
│   ├── decision_model.py      # SuccessPredictor — train(), predict(), explain(),
│   │                          # modality_trends(), historical_lessons
│   └── rl_env.py              # Reinforcement learning environment (portfolio MDP)
├── analysis/
│   └── analytics.py           # Portfolio analytics, trend intelligence,
│                              # cross-modality failure lesson engine
└── storage/
    ├── database.py            # TinyDB JSON store
    └── repository.py          # fetch_all(), upsert_record()

pharma_decision_engine.py      # Bayesian sequential engine (standalone)
decision_agents.py             # Clinical / Financial / Risk AI agents
review_board.py                # Multi-agent synthesis → DecisionBrief
run_pipeline.py                # CLI: collect | analyse | trend | train | rl
run_with_review_board.py       # Interactive review board entry point
```

---

## Decision Logic

The success predictor uses a gradient-boosted ensemble with logistic regression calibration.
Post-model calibration nudges are applied based on text signals:

| Signal | Adjustment |
|---|---|
| Validated molecular target (18 antigen profiles) | +0.25 |
| Positive outcome signal in text | +0.15 |
| Clear-cut tech–indication fit | +0.12 |
| Good tech–indication fit (score ≥ 0.70) | +0.05 |
| Unvalidated molecular target | −0.08 |
| Failure / termination signal in text | −0.18 |
| Bleeding-edge tech in mismatched indication | −0.05 |

---

## Molecular Format Coverage

The feature extractor detects **40+ molecular formats** across:

- **Antibody**: IgG1/2/4, nanobody, Fab/scFv, Fc-fusion, Fc-engineered, Probody, pH-selective/sweeping
- **Bispecific**: BiTE, HLE-BiTE, CrossMAb/KiH, DART, masked TCE, COBRA conditional bispecific
- **ADC**: cleavable linker (MMAE, DXd, SN-38), non-cleavable (DM1/DM4), Probody Drug Conjugate (masked ADC)
- **CAR-T**: autologous, allogeneic, AND-gate/dual-logic, SynNotch, TRUCK/armored, adapter, NOT-gate, split/ZIP-CAR
- **Small molecule**: covalent, macrocycle, allosteric, oral
- **RNA / oligo**: GalNAc-RNAi, splice-switching, circular RNA
- **Gene therapy**: AAV, lentiviral, base editing, prime editing
- **Conditional / stimulus-responsive**: hypoxia-activated prodrug (HAP)
- **Delivery**: subcutaneous, PEGylated, nanoparticle

Each format has an **engineering trade-off note**, a **modality toxicity profile**, and a **cross-modality ancestry** linking it to ancestor formats whose historical failure data is surfaced during evaluation.

---

## Historical Failure Lessons

For any evaluated program, `explain()` returns `historical_lessons` — empirical lessons drawn from ancestor format discontinuation records in the DB:

```python
lessons = result["historical_lessons"]
# Each lesson:
# {
#   "ancestor":        "adc_noncleavable",
#   "child_format":    "probody_dc",
#   "n_discontinued":  8,
#   "tox_kill_rate":   0.50,   # 50% of discontinuations had tox as primary driver
#   "top_toxicities":  [{"type": "myelosuppression", "count": 3}, ...],
#   "transfer_note":   "Non-cleavable ADC 62% tox-kill rate is the baseline...",
#   "severity":        "HIGH — dominant tox-driven attrition in ancestor class",
#   "data_quality":    "empirical"
# }
```

---

## Modality Trend Scores

Two signals per format:

**Heat score** — activity concentrated in recent years:

```
heat = n_recent * (n_recent / n_dated + 0.1)
```

**Emergence score** — acceleration from a low base (next new thing signal):

```
emergence = (n_recent - n_baseline) / (n_baseline + n_early + 1) * log(n_recent + 1)
```

---

## Data Sources

| Source | Records | Notes |
|---|---|---|
| ChEMBL | ~7,800 | First-approval year, max phase, indication |
| ClinicalTrials.gov | ~2,900 | Registration year, stage, outcome |
| FDA approvals | ~1,330 | Indication, mechanism, approval year |
| EMA medicines | ~2,230 | European approval data |
| Historical cohort | ~3,800 | 10/30-year synthetic cohort for RL training |
| VC portfolio | ~390 | Atlas, Sofinnova, ARCH, Versant, OrbiMed |
| SEC EDGAR | ~175 | 10-K/8-K pipeline decisions |
| PubMed | ~130 | Phase outcomes with publication date |

All proprietary data files are excluded from the repository via `.gitignore`.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your key:

```bash
cp .env.example .env
```

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

The review board and AI agent features require a valid Anthropic API key.
All other capabilities (ML predictor, trend analysis, Bayesian engine) run without any API key.

---

## What Is Excluded From This Repository

The following are kept out of version control:

- `data/bioventure.db`, `data/bioventure.json` — the collected dataset
- `data/*.txt` — analysis output files
- `data/slides/` — downloaded investor decks
- `models/`, `*.pkl`, `*.joblib` — trained model artefacts
- `.env`, `*.key`, `secrets.json` — credentials
- `decision_log.jsonl` — decision audit logs

See `.gitignore` for the complete list.

---

## Pipeline CLI Reference

```bash
python run_pipeline.py collect    # Collect from all public sources
python run_pipeline.py analyse    # Portfolio kill-rate and transition analysis
python run_pipeline.py trend      # Modality trend intelligence report
python run_pipeline.py train      # Train ML model
python run_pipeline.py rl         # Run RL portfolio simulation
python run_pipeline.py full       # Run all stages in sequence
```

---

## License

MIT

---

## Background

Bioventure investing and drug development share the same structural problem: decisions are made under extreme uncertainty, with high costs, long time horizons, and catastrophic failure rates (>90% of drugs entering clinical trials never reach approval).

Despite this, most go/no-go decisions are:
- Made by expert opinion (black-box, non-reproducible)
- Not logged in a structured, machine-readable form
- Rarely post-mortemed in a way that feeds back into future decisions

The insight is that the *quality of the decision process* matters more than any individual outcome — in a probabilistic environment you cannot optimise one trial, you can only optimise the distribution of decisions over many trials.

### Design

| Method | Pros | Cons |
|---|---|---|
| **Supervised Learning** | Simple, interpretable, works with limited data | Only learns from historical decisions |
| **Bayesian Updating** | Principled uncertainty, small-data friendly | No cross-project generalisation |
| **Reinforcement Learning** | Learns sequential policy, captures optionality | Needs large data, sparse reward |
| **Hybrid (implemented)** | Strong prior (Bayesian) + learned policy (RL) | More complex but most realistic |

Architecture: Bayesian-seeded RL — priors initialised from historical success rates, sequential decisions mapped to an MDP, human expert decisions used as imitation learning seed.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    PROJECT CONTEXT                          │
│  (hypothesis, evidence, assumptions, financials)            │
└────────────────────────┬────────────────────────────────────┘
                         │
               ┌─────────┼─────────┐
               │         │         │
               ↓         ↓         ↓
        ┌────────────┐ ┌──────────────┐ ┌───────────┐
        │  Clinical  │ │  Financial   │ │   Risk    │
        │   Agent    │ │    Agent     │ │  Officer  │
        │ (Claude)   │ │  (Claude)    │ │ (Claude)  │
        └────────────┘ └──────────────┘ └───────────┘
               │         │         │
               └─────────┼─────────┘
                         │
                   PARALLEL API CALLS
                   (ThreadPoolExecutor)
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    ┌────▼────┐    ┌────▼────┐    ┌────▼────┐
    │CLINICAL │    │FINANCIAL│    │  RISK   │
    │ANALYSIS │    │ANALYSIS │    │ANALYSIS │
    └────┬────┘    └────┬────┘    └────┬────┘
         │               │               │
         └───────────────┼───────────────┘
                         │
                   ┌─────▼─────┐
                   │ CONSENSUS │  (ALIGNED / SPLIT / CONFLICTED)
                   └─────┬─────┘
                         │
                   ┌─────▼──────────────────┐
                   │  DECISION BRIEF        │
                   │  - Agent agreements    │
                   │  - Tensions            │
                   │  - Critical unknowns   │
                   └─────┬──────────────────┘
                         │
                    [OPERATOR REVIEWS]
                         │
                    ┌────▼─────┐
                    │ Decision │  KILL / CONTINUE / INVEST
                    └────┬─────┘
                         │
                 ┌───────▼────────┐
                 │ DECISION LOG   │
                 │ (JSONL)        │
                 └────────────────┘
```

### Consensus Patterns

| Pattern | Meaning | Action |
|---|---|---|
| **ALIGNED** | All three agents agree | Proceed with confidence |
| **SPLIT** | Two vs one | Identify which concern is binding |
| **CONFLICTED** | All three differ | De-risk specific assumptions first |

### Decision Log Schema

```json
{
  "timestamp": "2026-04-12T15:30:45Z",
  "project_id": "P-ONCO-001",
  "agent_consensus": "CONTINUE",
  "agent_confidence": 0.68,
  "human_decision": "INVEST",
  "human_reasoning": "Clinical team confidence on efficacy signal",
  "aligned": false,
  "override_reason": "Additional runway justifies the risk",
  "tensions_count": 2
}
```

---

## Claude API Setup

The review board requires an Anthropic API key. All other features run without one.

### Set API key

```bash
# Linux/macOS
export ANTHROPIC_API_KEY="sk-ant-v0-xxxxxxxxxxxxx"

# Or add to ~/.bashrc for persistence
echo 'export ANTHROPIC_API_KEY="sk-ant-v0-xxxxxxxxxxxxx"' >> ~/.bashrc
```

```powershell
# Windows
$env:ANTHROPIC_API_KEY = "sk-ant-v0-xxxxxxxxxxxxx"
```

Or use a `.env` file (already in `.gitignore`):

```
ANTHROPIC_API_KEY=sk-ant-v0-xxxxxxxxxxxxx
```

### Verify

```bash
python -c "import os; print('Key found' if os.getenv('ANTHROPIC_API_KEY') else 'Key missing')"
```

### Cost estimate

Each decision brief (3 agents, claude-3-5-sonnet): **~$0.003–0.006**

| Tokens | Rate |
|---|---|
| Input | $3 / million |
| Output | $15 / million |

### Integrating with external project data

```python
from review_board import ReviewBoard

board = ReviewBoard()
brief = board.analyze_project(
    project_id="P-ONCO-001",
    hypothesis="Drug A improves PFS in HER2+ breast cancer",
    project_context={
        "prior_probability": 0.55,
        "payoff": 400_000_000,
        "cost_per_milestone": 2_500_000,
        "assumptions": ["Target validated", "Phase II endpoint agreed with FDA"],
        "evidences": ["Phase IIb: PFS HR 0.61 (p=0.003)"],
    },
    parallel=True,
)

print(brief.consensus)       # ALIGNED / SPLIT / CONFLICTED
print(brief.recommendation)  # KILL / CONTINUE / INVEST
```

### Troubleshooting

| Problem | Fix |
|---|---|
| Key not found | Run `echo $ANTHROPIC_API_KEY`; re-export if empty |
| Rate limited | Check credits at console.anthropic.com |
| Response parsing failed | `call_claude_agent()` auto-falls back to placeholder |
| ThreadPoolExecutor hangs | Set `parallel=False` to run agents sequentially |
