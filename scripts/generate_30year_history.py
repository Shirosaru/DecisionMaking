#!/usr/bin/env python3
"""
generate_30year_history.py
──────────────────────────
Simulate 30 years (1996-2026) of bioventure drug-development pipeline history.

Full stage pipeline (with IND filing and NDA submission):
    preclinical → ind_filing → phase1 (PI) → phase2 → phase3 → nda_submitted → approved

Produces ~4 500-6 000 stage-decision records for ~1 740 programs across
29 start cohorts (1996-2024), calibrated to:
  • BIO 2011-2020 Clinical Development Success Rates
  • FDA historical approval data (1996-2026)
  • IND clinical-hold rate: ~10% oncology, ~5% rare disease
  • Decade-based investment inflation (1996-base × ~8%/yr)
  • NDA review cycle: 6-18 months, ~85% approval rate for submitted NDAs

Run once:
    python3 generate_30year_history.py
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
SEED             = 19960417
PROGRAMS_PER_YEAR = 60           # × 29 cohort years = 1 740 programs
START_YEARS      = range(1996, 2025)
CURRENT_YEAR     = 2026
DB_PATH          = _ROOT / "data" / "bioventure.json"
SOURCE_NAME      = "hist30_cohort"

random.seed(SEED)

# ── Vocabularies ───────────────────────────────────────────────────────────────
INDICATIONS: list[tuple[str, str]] = [
    # (indication, therapeutic_area)
    ("Non-small cell lung cancer",       "oncology"),
    ("Breast cancer",                    "oncology"),
    ("Acute myeloid leukemia",           "oncology"),
    ("Colorectal cancer",                "oncology"),
    ("Ovarian cancer",                   "oncology"),
    ("Melanoma",                         "oncology"),
    ("Prostate cancer",                  "oncology"),
    ("Glioblastoma",                     "oncology"),
    ("Pancreatic cancer",                "oncology"),
    ("Diffuse large B-cell lymphoma",    "oncology"),
    ("Hepatocellular carcinoma",         "oncology"),
    ("Renal cell carcinoma",             "oncology"),
    ("Cervical cancer",                  "oncology"),
    ("Head and neck cancer",             "oncology"),
    ("Crohn's disease",                  "immunology"),
    ("Ulcerative colitis",               "immunology"),
    ("Rheumatoid arthritis",             "immunology"),
    ("Systemic lupus erythematosus",     "immunology"),
    ("Psoriatic arthritis",              "immunology"),
    ("Ankylosing spondylitis",           "immunology"),
    ("Atopic dermatitis",                "immunology"),
    ("Asthma",                           "immunology"),
    ("Alzheimer's disease",              "neurology"),
    ("Parkinson's disease",              "neurology"),
    ("ALS",                              "neurology"),
    ("Multiple sclerosis",               "neurology"),
    ("Huntington's disease",             "neurology"),
    ("Migraine (prevention)",            "neurology"),
    ("Treatment-resistant depression",   "neurology"),
    ("Spinal muscular atrophy",          "rare_disease"),
    ("Sickle cell disease",              "rare_disease"),
    ("Beta-thalassemia",                 "rare_disease"),
    ("Gaucher disease",                  "rare_disease"),
    ("Fabry disease",                    "rare_disease"),
    ("Duchenne muscular dystrophy",      "rare_disease"),
    ("Cystic fibrosis",                  "rare_disease"),
    ("Hemophilia A",                     "rare_disease"),
    ("Pompe disease",                    "rare_disease"),
    ("Type 2 diabetes",                  "metabolic"),
    ("NASH/MASH",                        "metabolic"),
    ("Obesity",                          "metabolic"),
    ("Dyslipidemia",                     "metabolic"),
    ("Heart failure",                    "cardiovascular"),
    ("Atrial fibrillation",              "cardiovascular"),
    ("Hypertrophic cardiomyopathy",      "cardiovascular"),
    ("Acute coronary syndrome",          "cardiovascular"),
    ("HIV",                              "infectious"),
    ("Hepatitis B",                      "infectious"),
    ("RSV infection",                    "infectious"),
    ("Influenza",                        "infectious"),
    ("SARS-CoV-2",                       "infectious"),
    ("Clostridioides difficile",         "infectious"),
]

MECHANISMS_BY_ERA: dict[str, list[str]] = {
    # Pre-2005: mostly small molecules and early biologics
    "1996-2004": [
        "small molecule kinase inhibitor",
        "selective serotonin reuptake inhibitor",
        "COX-2 inhibitor",
        "statin/lipid-lowering agent",
        "nucleoside reverse transcriptase inhibitor",
        "protease inhibitor",
        "monoclonal antibody (first-gen)",
        "recombinant protein",
        "antisense oligonucleotide",
        "beta-1 selective blocker",
        "ACE inhibitor",
        "aromatase inhibitor",
        "platinum-based chemotherapy",
        "taxane combination",
    ],
    # 2005-2014: targeted therapies, next-gen biologics
    "2005-2014": [
        "anti-PD-1 monoclonal antibody",
        "HER2-targeted antibody",
        "VEGF/VEGFR inhibitor",
        "BCR-ABL kinase inhibitor",
        "BRAF inhibitor",
        "MEK inhibitor",
        "PI3K/mTOR inhibitor",
        "EGFR inhibitor",
        "anti-IL-6 antibody",
        "anti-TNF biologic",
        "JAK1/2 inhibitor",
        "CDK4/6 inhibitor",
        "PD-L1 antibody",
        "siRNA lipid nanoparticle",
        "CAR-T cell therapy (first-gen)",
        "bispecific T-cell engager",
        "complement inhibitor",
        "enzyme replacement therapy",
    ],
    # 2015-2026: precision medicine, gene therapy, CRISPR era
    "2015-2026": [
        "KRAS G12C inhibitor",
        "KRAS G12D inhibitor",
        "CRISPR-Cas9 gene editing",
        "AAV-delivered gene therapy",
        "mRNA therapeutic (LNP)",
        "LNP-siRNA",
        "GLP-1/GIP dual agonist",
        "SGLT2 inhibitor",
        "HER2 ADC",
        "TROP2 ADC",
        "TYK2 inhibitor",
        "PARP inhibitor",
        "IDH1/2 inhibitor",
        "RET selective inhibitor",
        "ALK/ROS1 inhibitor",
        "HIF-2alpha inhibitor",
        "SMN2 splicing modifier",
        "alpha-synuclein immunotherapy",
        "tau antisense oligonucleotide",
        "CAR-T cell therapy (next-gen)",
        "NK cell engager",
        "bispecific PD-1/LAG-3",
        "BCL-2 inhibitor",
        "FLT3/AXL dual inhibitor",
        "BTK inhibitor (covalent/non-covalent)",
    ],
}

VCS: list[str] = [
    "OrbiMed", "Versant Ventures", "Atlas Venture", "SR One",
    "5AM Ventures", "Foresite Capital", "Sofinnova Partners",
    "Third Rock Ventures", "RA Capital", "Flagship Pioneering",
    "Arch Venture Partners", "NEA Healthcare", "GV Life Sciences",
    "Frazier Healthcare", "Deerfield Management", "Perceptive Advisors",
    "Bain Capital Life Sciences", "MPM Capital", "Polaris Partners",
    "Canaan Partners", "Index Ventures", "Novo Holdings",
    "Pfizer Ventures", "Johnson & Johnson Innovation", "Roche Ventures",
    "Bristol Myers Squibb Ventures", "Eli Lilly Ventures",
]

# ── Industry success rates (BIO analysis; decade-adjusted) ────────────────────
# Format: {therapeutic_area: {stage: success_rate}}
# IND = pre-Phase1 IND clearance; pi = Phase 1; nda = NDA approval rate

IND_SUCCESS_BY_ERA: dict[str, dict[str, dict[str, float]]] = {
    # 1996-2004: lower success rates (pre-biomarker era)
    "early": {
        "oncology":      {"ind_filing": 0.82, "phase1": 0.38, "phase2": 0.18, "phase3": 0.42, "nda_submitted": 0.80},
        "immunology":    {"ind_filing": 0.88, "phase1": 0.48, "phase2": 0.30, "phase3": 0.55, "nda_submitted": 0.83},
        "neurology":     {"ind_filing": 0.85, "phase1": 0.40, "phase2": 0.11, "phase3": 0.42, "nda_submitted": 0.78},
        "rare_disease":  {"ind_filing": 0.90, "phase1": 0.55, "phase2": 0.38, "phase3": 0.60, "nda_submitted": 0.88},
        "metabolic":     {"ind_filing": 0.87, "phase1": 0.46, "phase2": 0.25, "phase3": 0.51, "nda_submitted": 0.82},
        "cardiovascular":{"ind_filing": 0.88, "phase1": 0.50, "phase2": 0.28, "phase3": 0.52, "nda_submitted": 0.81},
        "infectious":    {"ind_filing": 0.89, "phase1": 0.52, "phase2": 0.35, "phase3": 0.58, "nda_submitted": 0.85},
    },
    # 2005-2014: targeted therapies improve Phase 2/3
    "mid": {
        "oncology":      {"ind_filing": 0.85, "phase1": 0.44, "phase2": 0.22, "phase3": 0.46, "nda_submitted": 0.83},
        "immunology":    {"ind_filing": 0.90, "phase1": 0.53, "phase2": 0.36, "phase3": 0.59, "nda_submitted": 0.85},
        "neurology":     {"ind_filing": 0.86, "phase1": 0.46, "phase2": 0.14, "phase3": 0.47, "nda_submitted": 0.80},
        "rare_disease":  {"ind_filing": 0.92, "phase1": 0.61, "phase2": 0.45, "phase3": 0.66, "nda_submitted": 0.90},
        "metabolic":     {"ind_filing": 0.88, "phase1": 0.51, "phase2": 0.29, "phase3": 0.55, "nda_submitted": 0.83},
        "cardiovascular":{"ind_filing": 0.89, "phase1": 0.55, "phase2": 0.32, "phase3": 0.57, "nda_submitted": 0.82},
        "infectious":    {"ind_filing": 0.90, "phase1": 0.55, "phase2": 0.38, "phase3": 0.61, "nda_submitted": 0.86},
    },
    # 2015-2026: precision medicine / biomarker era
    "recent": {
        "oncology":      {"ind_filing": 0.88, "phase1": 0.48, "phase2": 0.27, "phase3": 0.51, "nda_submitted": 0.85},
        "immunology":    {"ind_filing": 0.91, "phase1": 0.57, "phase2": 0.40, "phase3": 0.63, "nda_submitted": 0.87},
        "neurology":     {"ind_filing": 0.87, "phase1": 0.49, "phase2": 0.17, "phase3": 0.51, "nda_submitted": 0.82},
        "rare_disease":  {"ind_filing": 0.93, "phase1": 0.65, "phase2": 0.50, "phase3": 0.71, "nda_submitted": 0.92},
        "metabolic":     {"ind_filing": 0.89, "phase1": 0.55, "phase2": 0.33, "phase3": 0.60, "nda_submitted": 0.84},
        "cardiovascular":{"ind_filing": 0.90, "phase1": 0.59, "phase2": 0.36, "phase3": 0.61, "nda_submitted": 0.83},
        "infectious":    {"ind_filing": 0.91, "phase1": 0.58, "phase2": 0.42, "phase3": 0.65, "nda_submitted": 0.87},
    },
}

# Preclinical-to-IND filing rate
PRECLINICAL_SUCCESS = {"early": 0.08, "mid": 0.09, "recent": 0.11}

# Stage duration ranges (years): (min, max)
STAGE_DURATION: dict[str, tuple[int, int]] = {
    "preclinical":  (1, 4),
    "ind_filing":   (0, 1),     # 0 = within same year
    "phase1":       (1, 3),
    "phase2":       (2, 5),
    "phase3":       (2, 6),
    "nda_submitted":(1, 2),
}

# Investment ranges (USD, 1996 base); inflate ~7%/yr
STAGE_INVEST_1996: dict[str, tuple[float, float]] = {
    "preclinical":   (500_000,    8_000_000),
    "ind_filing":    (500_000,    3_000_000),
    "phase1":        (3_000_000,  20_000_000),
    "phase2":        (15_000_000, 100_000_000),
    "phase3":        (60_000_000, 500_000_000),
    "nda_submitted": (10_000_000,  60_000_000),
}

FULL_PIPELINE = ["preclinical", "ind_filing", "phase1", "phase2", "phase3", "nda_submitted"]
STAGE_NEXT    = {s: FULL_PIPELINE[i + 1] for i, s in enumerate(FULL_PIPELINE[:-1])}
STAGE_NEXT["nda_submitted"] = "approved"


def _era(year: int) -> str:
    if year <= 2004: return "early"
    if year <= 2014: return "mid"
    return "recent"


def _mechanism(year: int) -> str:
    if year <= 2004:
        return random.choice(MECHANISMS_BY_ERA["1996-2004"])
    if year <= 2014:
        return random.choice(MECHANISMS_BY_ERA["2005-2014"])
    return random.choice(MECHANISMS_BY_ERA["2015-2026"])


def _investment(stage: str, year: int) -> float:
    lo, hi = STAGE_INVEST_1996[stage]
    inflation = 1.07 ** (year - 1996)
    return random.uniform(lo * inflation, hi * inflation)


def _start_stage_weights(year: int) -> list[float]:
    """Later cohorts are more likely to enter at IND/Phase1 (earlier detection)."""
    if year <= 2002:
        return [0.65, 0.20, 0.15]   # preclinical, ind_filing, phase1
    if year <= 2010:
        return [0.50, 0.28, 0.22]
    if year <= 2018:
        return [0.38, 0.34, 0.28]
    return [0.28, 0.38, 0.34]


def _simulate_program(
    prog_id: str,
    start_year: int,
    start_stage: str,
    indication: str,
    ind_group: str,
    mechanism: str,
    vc: str,
) -> list[RawRecord]:
    era    = _era(start_year)
    rates  = IND_SUCCESS_BY_ERA[era].get(ind_group, IND_SUCCESS_BY_ERA[era]["oncology"])
    pc_rate = PRECLINICAL_SUCCESS[era]

    records: list[RawRecord] = []
    stage   = start_stage
    year    = start_year

    while stage in STAGE_NEXT:
        duration  = random.randint(*STAGE_DURATION[stage])
        end_year  = year + duration
        observable = end_year <= CURRENT_YEAR

        # Compute success probability for this stage
        if stage == "preclinical":
            success_rate = pc_rate
        else:
            success_rate = rates.get(stage, 0.50)

        succeeded = random.random() < success_rate

        if not observable:
            outcome  = "ongoing"
            decision = "go"
        elif succeeded:
            outcome  = "ongoing"   # stage cleared, next stage begins
            decision = "go"
        else:
            outcome  = f"discontinued_{stage}"
            decision = "no-go"

        invest = _investment(stage, year)

        stage_label = (stage.replace("ind_filing", "IND Filing")
                            .replace("nda_submitted", "NDA Submitted")
                            .replace("phase", "Phase ").title())
        title = f"{mechanism} in {indication} ({stage_label})"

        records.append(RawRecord(
            source      = SOURCE_NAME,
            source_id   = f"{prog_id}_{stage}",
            url         = f"https://bioventure.internal/hist30/{prog_id}/{stage}",
            title       = title,
            indication  = indication,
            mechanism   = mechanism,
            clinical_stage = stage,
            decision    = decision,
            outcome     = outcome,
            investment_usd = invest,
            raw_text    = (
                f"{indication} {mechanism} {stage} {ind_group} {vc} {era} era "
                f"clinical trial pipeline drug development {start_year}"
            ),
            extra = {
                "decision_year":  year,
                "cohort_start":   start_year,
                "stage_end_year": end_year,
                "ind_group":      ind_group,
                "vc":             vc,
                "duration_years": duration,
                "observable":     observable,
                "prog_id":        prog_id,
                "era":            era,
            },
        ))

        if not observable or not succeeded:
            break

        next_stage = STAGE_NEXT[stage]
        if next_stage == "approved":
            invest_nda_approval = _investment("nda_submitted", end_year) * 0.20
            records.append(RawRecord(
                source      = SOURCE_NAME,
                source_id   = f"{prog_id}_approved",
                url         = f"https://bioventure.internal/hist30/{prog_id}/approved",
                title       = f"{mechanism} in {indication} (FDA Approved — NDA/BLA)",
                indication  = indication,
                mechanism   = mechanism,
                clinical_stage = "approved",
                decision    = "go",
                outcome     = "approved",
                investment_usd = invest_nda_approval,
                raw_text    = (
                    f"FDA approved {indication} {mechanism} NDA BLA "
                    f"regulatory approval commercialization {era} era"
                ),
                extra = {
                    "decision_year":  end_year,
                    "cohort_start":   start_year,
                    "stage_end_year": end_year + 1,
                    "ind_group":      ind_group,
                    "vc":             vc,
                    "duration_years": 1,
                    "observable":     True,
                    "prog_id":        prog_id,
                    "era":            era,
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
            prog_id     = f"h30_{start_year}_{idx:03d}"
            indication, ind_group = random.choice(INDICATIONS)
            mechanism   = _mechanism(start_year)
            vc          = random.choice(VCS)
            start_stage = random.choices(
                ["preclinical", "ind_filing", "phase1"], weights=weights
            )[0]

            recs = _simulate_program(
                prog_id, start_year, start_stage,
                indication, ind_group, mechanism, vc,
            )
            all_records.extend(recs)
            program_count += 1

    inserted = bulk_upsert(all_records, db_path=DB_PATH)
    after    = count(db_path=DB_PATH)

    print(f"\n{'═'*66}")
    print(f"  30-Year History Generator Complete (1996-2026)")
    print(f"{'═'*66}")
    print(f"  Programs simulated:      {program_count:>6}")
    print(f"  Stage records built:     {len(all_records):>6}")
    print(f"  New records in DB:       {inserted:>6}")
    print(f"  DB size before:          {before:>6}")
    print(f"  DB size after:           {after:>6}")

    # Era + cohort summary
    from collections import Counter
    era_counts: dict[str, Counter] = {}
    for r in all_records:
        era = r.extra.get("era", "?")
        if era not in era_counts:
            era_counts[era] = Counter()
        era_counts[era][r.outcome] += 1

    print(f"\n  ── By era ──")
    ERA_LABELS = {"early": "1996-2004", "mid": "2005-2014", "recent": "2015-2026"}
    for era in ["early", "mid", "recent"]:
        c = era_counts.get(era, Counter())
        n_approvals = c.get("approved", 0)
        n_disc      = sum(v for k, v in c.items() if "discontinued" in k)
        n_ongoing   = c.get("ongoing", 0)
        total       = sum(c.values())
        print(f"    {ERA_LABELS[era]}: {total:>5} records  "
              f"(approved={n_approvals:>3}, discontinued={n_disc:>4}, ongoing={n_ongoing:>4})")

    # Top 3 start cohorts by records
    year_rec = Counter(r.extra.get("cohort_start") for r in all_records)
    print(f"\n  ── Sample cohort sizes ──")
    for yr, n in sorted(year_rec.items()):
        if yr % 5 == 1 or yr in (1996, 2024):
            print(f"    {yr}: {n:>4} stage-records")


if __name__ == "__main__":
    main()
