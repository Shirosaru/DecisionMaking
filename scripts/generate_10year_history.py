#!/usr/bin/env python3
"""
generate_10year_history.py
──────────────────────────
Simulate 10 years (2016-2026) of bioventure drug-development decision history.

Produces ~1 500-2 000 stage-decision records for 495 synthetic programs across
9 start cohorts (2016-2024), calibrated to published industry success rates
(BIO 2011-2020 Clinical Development Success Rates; Hay et al. 2014).

Each record carries extra metadata:
    decision_year   – calendar year the stage decision was taken
    cohort_start    – year the program first entered the portfolio
    stage_end_year  – year the stage concluded (success or failure)
    ind_group       – therapeutic area group
    vc              – lead VC backer
    prog_id         – unique program identifier

Run once:
    python3 generate_10year_history.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.collectors.base_collector import RawRecord
from src.storage.repository import bulk_upsert, count

# ── Config ─────────────────────────────────────────────────────────────────────
SEED          = 20260417
PROGRAMS_PER_YEAR = 55          # × 9 cohort years = 495 programs
START_YEARS   = range(2016, 2025)
CURRENT_YEAR  = 2026
DB_PATH       = _ROOT / "data" / "bioventure.json"
SOURCE_NAME   = "historical_cohort"

random.seed(SEED)

# ── Vocabularies ───────────────────────────────────────────────────────────────
INDICATIONS: list[tuple[str, str]] = [
    ("Non-small cell lung cancer",          "oncology"),
    ("Breast cancer",                       "oncology"),
    ("Acute myeloid leukemia",              "oncology"),
    ("Colorectal cancer",                   "oncology"),
    ("Ovarian cancer",                      "oncology"),
    ("Melanoma",                            "oncology"),
    ("Prostate cancer",                     "oncology"),
    ("Glioblastoma",                        "oncology"),
    ("Pancreatic cancer",                   "oncology"),
    ("Diffuse large B-cell lymphoma",       "oncology"),
    ("Hepatocellular carcinoma",            "oncology"),
    ("Renal cell carcinoma",                "oncology"),
    ("Crohn's disease",                     "immunology"),
    ("Ulcerative colitis",                  "immunology"),
    ("Rheumatoid arthritis",                "immunology"),
    ("Systemic lupus erythematosus",        "immunology"),
    ("Psoriatic arthritis",                 "immunology"),
    ("Ankylosing spondylitis",              "immunology"),
    ("Alzheimer's disease",                 "neurology"),
    ("Parkinson's disease",                 "neurology"),
    ("ALS",                                 "neurology"),
    ("Multiple sclerosis",                  "neurology"),
    ("Huntington's disease",                "neurology"),
    ("Spinal muscular atrophy",             "rare_disease"),
    ("Sickle cell disease",                 "rare_disease"),
    ("Beta-thalassemia",                    "rare_disease"),
    ("Gaucher disease",                     "rare_disease"),
    ("Fabry disease",                       "rare_disease"),
    ("Duchenne muscular dystrophy",         "rare_disease"),
    ("Type 2 diabetes",                     "metabolic"),
    ("NASH/MASH",                           "metabolic"),
    ("Obesity",                             "metabolic"),
    ("Heart failure",                       "cardiovascular"),
    ("Atrial fibrillation",                 "cardiovascular"),
    ("Hypertrophic cardiomyopathy",         "cardiovascular"),
    ("HIV",                                 "infectious"),
    ("Hepatitis B",                         "infectious"),
    ("RSV infection",                       "infectious"),
    ("SARS-CoV-2",                          "infectious"),
]

MECHANISMS: list[str] = [
    "anti-PD-1 monoclonal antibody",
    "PD-L1/CTLA-4 bispecific",
    "HER2 ADC",
    "TROP2 ADC",
    "CDK4/6 small molecule inhibitor",
    "KRAS G12C inhibitor",
    "KRAS G12D inhibitor",
    "CAR-T cell therapy",
    "bispecific T-cell engager",
    "anti-IL-6 antibody",
    "IL-17A inhibitor",
    "JAK1/2 inhibitor",
    "TYK2 inhibitor",
    "CRISPR-Cas9 gene editing",
    "AAV-delivered gene therapy",
    "mRNA therapeutic",
    "LNP-siRNA",
    "GLP-1/GIP dual agonist",
    "GLP-1 receptor agonist",
    "SGLT2 inhibitor",
    "anti-VEGF antibody",
    "BRAF/MEK combination",
    "Tau antisense oligonucleotide",
    "alpha-synuclein immunotherapy",
    "SMN2 splicing modifier",
    "enzyme replacement therapy",
    "checkpoint inhibitor + VEGF combo",
    "BTK inhibitor",
    "BCL-2 inhibitor",
    "FLT3/AXL inhibitor",
    "RET selective inhibitor",
    "ALK/ROS1 inhibitor",
    "PARP inhibitor",
    "IDH1 inhibitor",
    "HIF-2alpha inhibitor",
]

VCS: list[str] = [
    "OrbiMed", "Versant Ventures", "Atlas Venture", "SR One",
    "5AM Ventures", "Foresite Capital", "Sofinnova Partners",
    "Third Rock Ventures", "RA Capital", "Flagship Pioneering",
    "Arch Venture", "NEA", "GV", "Frazier Healthcare",
    "Deerfield Management", "Perceptive Advisors", "Bain Capital Life Sciences",
    "MPM Capital", "Polaris Partners", "F2 Ventures",
]

# ── Industry success rates (BIO 2011-2020; Hay et al.) ─────────────────────────
IND_SUCCESS: dict[str, dict[str, float]] = {
    "oncology":       {"preclinical": 0.08, "phase1": 0.44, "phase2": 0.24, "phase3": 0.48},
    "immunology":     {"preclinical": 0.11, "phase1": 0.55, "phase2": 0.38, "phase3": 0.61},
    "neurology":      {"preclinical": 0.08, "phase1": 0.47, "phase2": 0.15, "phase3": 0.49},
    "rare_disease":   {"preclinical": 0.14, "phase1": 0.63, "phase2": 0.48, "phase3": 0.69},
    "metabolic":      {"preclinical": 0.09, "phase1": 0.53, "phase2": 0.31, "phase3": 0.58},
    "cardiovascular": {"preclinical": 0.09, "phase1": 0.57, "phase2": 0.34, "phase3": 0.59},
    "infectious":     {"preclinical": 0.10, "phase1": 0.56, "phase2": 0.40, "phase3": 0.63},
}

# Stage duration (years): (min, max)
STAGE_DURATION: dict[str, tuple[int, int]] = {
    "preclinical": (1, 3),
    "phase1":      (1, 2),
    "phase2":      (2, 4),
    "phase3":      (2, 5),
}

# Investment ranges (USD, 2016 base); inflate 8% per year after 2016
STAGE_INVEST_BASE: dict[str, tuple[float, float]] = {
    "preclinical": (2_000_000,   12_000_000),
    "phase1":      (8_000_000,   40_000_000),
    "phase2":      (35_000_000,  180_000_000),
    "phase3":      (120_000_000, 750_000_000),
}

STAGE_ORDER   = ["preclinical", "phase1", "phase2", "phase3"]
STAGE_NEXT    = {"preclinical": "phase1", "phase1": "phase2",
                 "phase2": "phase3", "phase3": "approved"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _investment(stage: str, year: int) -> float:
    lo, hi = STAGE_INVEST_BASE[stage]
    inflation = 1.08 ** (year - 2016)
    return random.uniform(lo * inflation, hi * inflation)


def _start_stage_weights(year: int) -> list[float]:
    """Earlier cohorts skew toward earlier stages (longer runways)."""
    if year <= 2018:
        return [0.55, 0.30, 0.15]   # preclinical, phase1, phase2
    if year <= 2021:
        return [0.40, 0.35, 0.25]
    return [0.28, 0.42, 0.30]


def _simulate_program(
    prog_id: str,
    start_year: int,
    start_stage: str,
    indication: str,
    ind_group: str,
    mechanism: str,
    vc: str,
) -> list[RawRecord]:
    """Simulate a drug program and return one RawRecord per stage decision."""
    records: list[RawRecord] = []
    rates   = IND_SUCCESS.get(ind_group, IND_SUCCESS["oncology"])
    stage   = start_stage
    year    = start_year

    while stage in STAGE_NEXT:
        duration  = random.randint(*STAGE_DURATION[stage])
        end_year  = year + duration
        succeeded = random.random() < rates[stage]

        # Is the outcome observable by CURRENT_YEAR?
        observable = end_year <= CURRENT_YEAR

        if not observable:
            outcome  = "ongoing"
            decision = "go"
        elif succeeded:
            outcome  = "ongoing"   # stage passed; program continues
            decision = "go"
        else:
            outcome  = f"discontinued_{stage}"
            decision = "no-go"

        invest = _investment(stage, year)
        title  = (f"{mechanism} in {indication} "
                  f"({stage.replace('phase', 'Phase ').replace('preclinical','Preclinical').title()})")

        records.append(RawRecord(
            source      = SOURCE_NAME,
            source_id   = f"{prog_id}_{stage}",
            url         = f"https://bioventure.internal/history/{prog_id}/{stage}",
            title       = title,
            indication  = indication,
            mechanism   = mechanism,
            clinical_stage = stage,
            decision    = decision,
            outcome     = outcome,
            investment_usd = invest,
            raw_text    = (f"{indication} {mechanism} {stage} {ind_group} {vc} "
                           "clinical trial pipeline bioventure drug development"),
            extra = {
                "decision_year":  year,
                "cohort_start":   start_year,
                "stage_end_year": end_year,
                "ind_group":      ind_group,
                "vc":             vc,
                "duration_years": duration,
                "observable":     observable,
                "prog_id":        prog_id,
            },
        ))

        # Stop if failed or not yet observable
        if not observable or not succeeded:
            break

        next_stage = STAGE_NEXT[stage]

        # Reached approval
        if next_stage == "approved":
            approval_invest = _investment("phase3", end_year) * 0.25
            records.append(RawRecord(
                source      = SOURCE_NAME,
                source_id   = f"{prog_id}_approved",
                url         = f"https://bioventure.internal/history/{prog_id}/approved",
                title       = f"{mechanism} in {indication} (Approved — NDA/BLA)",
                indication  = indication,
                mechanism   = mechanism,
                clinical_stage = "approved",
                decision    = "go",
                outcome     = "approved",
                investment_usd = approval_invest,
                raw_text    = (f"FDA approved {indication} {mechanism} "
                               "NDA BLA regulatory approval commercialization"),
                extra = {
                    "decision_year":  end_year,
                    "cohort_start":   start_year,
                    "stage_end_year": end_year + 1,
                    "ind_group":      ind_group,
                    "vc":             vc,
                    "duration_years": 1,
                    "observable":     True,
                    "prog_id":        prog_id,
                },
            ))
            break

        stage = next_stage
        year  = end_year

    return records


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    before = count(db_path=DB_PATH)

    all_records: list[RawRecord] = []
    program_count = 0

    for start_year in START_YEARS:
        weights = _start_stage_weights(start_year)
        for idx in range(PROGRAMS_PER_YEAR):
            prog_id     = f"hc_{start_year}_{idx:03d}"
            indication, ind_group = random.choice(INDICATIONS)
            mechanism   = random.choice(MECHANISMS)
            vc          = random.choice(VCS)
            start_stage = random.choices(
                ["preclinical", "phase1", "phase2"], weights=weights
            )[0]

            recs = _simulate_program(
                prog_id, start_year, start_stage,
                indication, ind_group, mechanism, vc,
            )
            all_records.extend(recs)
            program_count += 1

    inserted = bulk_upsert(all_records, db_path=DB_PATH)
    after    = count(db_path=DB_PATH)

    print(f"\n{'═'*60}")
    print(f"  10-Year History Generator Complete")
    print(f"{'═'*60}")
    print(f"  Programs simulated:  {program_count}")
    print(f"  Stage records built: {len(all_records)}")
    print(f"  New records in DB:   {inserted}")
    print(f"  DB size before:      {before}")
    print(f"  DB size after:       {after}")

    # Quick cohort breakdown
    from collections import Counter
    years = Counter(
        r.extra["cohort_start"]
        for r in all_records
        if r.extra.get("cohort_start")
    )
    print(f"\n  Cohort breakdown by start year:")
    for yr in sorted(years):
        outcomes = Counter(r.outcome for r in all_records if r.extra.get("cohort_start") == yr)
        approved = outcomes.get("approved", 0)
        disc     = sum(v for k, v in outcomes.items() if "discontinued" in k)
        ongoing  = outcomes.get("ongoing", 0)
        print(f"    {yr}: {years[yr]:>3} stage-records  "
              f"(approved={approved}, discontinued={disc}, ongoing={ongoing})")


if __name__ == "__main__":
    main()
