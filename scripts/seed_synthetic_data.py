#!/usr/bin/env python3
"""
Seed the bioventure JSON store with realistic synthetic records that
mirror what the collectors would pull from real sources:
  - ClinicalTrials-style terminated/completed studies
  - VC portfolio investments at various stages
  - SEC 8-K pipeline go/no-go disclosures
  - Slide-extracted news items

Run once to populate data/bioventure.json before running the full pipeline.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.collectors.base_collector import RawRecord
from src.storage.repository import bulk_upsert, count

SEED = 42
random.seed(SEED)

DB_PATH = _ROOT / "data" / "bioventure.json"

# ── Domain vocabularies ───────────────────────────────────────────────────────

INDICATIONS = [
    "Non-small cell lung cancer", "Breast cancer", "Acute myeloid leukemia",
    "Colorectal cancer", "Ovarian cancer", "Diffuse large B-cell lymphoma",
    "Prostate cancer", "Glioblastoma", "Pancreatic cancer", "Melanoma",
    "Crohn's disease", "Ulcerative colitis", "Rheumatoid arthritis",
    "Systemic lupus erythematosus", "Multiple sclerosis",
    "Alzheimer's disease", "Parkinson's disease", "ALS",
    "Rare pediatric neurological disorder", "Spinal muscular atrophy",
    "Sickle cell disease", "Beta-thalassemia", "Gaucher disease",
    "Type 2 diabetes", "NASH", "Obesity", "Heart failure",
    "Atrial fibrillation", "HIV", "HBV", "RSV",
]

MECHANISMS = [
    "anti-PD-1 monoclonal antibody", "HER2 ADC",
    "CDK4/6 small molecule inhibitor", "KRAS G12C inhibitor",
    "CAR-T cell therapy", "bispecific T-cell engager",
    "anti-IL-6 antibody", "JAK1 inhibitor",
    "CRISPR-Cas9 gene editing", "AAV-delivered gene therapy",
    "mRNA therapeutic", "siRNA against PCSK9",
    "GLP-1 receptor agonist", "SGLT2 inhibitor",
    "anti-VEGF antibody", "BRAF/MEK combination",
    "Tau antisense oligonucleotide", "alpha-synuclein immunotherapy",
    "SMN2 splicing modifier", "enzyme replacement therapy",
]

VCS = [
    "Atlas Venture", "ARCH Venture", "Sofinnova",
    "Versant Ventures", "Flagship Pioneering", "OrbiMed",
    "5AM Ventures", "Third Rock Ventures", "RA Capital",
    "Foresite Capital",
]

STAGES = ["preclinical", "phase1", "phase1", "phase2", "phase2", "phase3"]

# Historical drug-approval transition rates by indication group (approximate)
SUCCESS_RATES = {
    "cancer": {"preclinical": 0.05, "phase1": 0.44, "phase2": 0.24, "phase3": 0.48},
    "immunology": {"preclinical": 0.06, "phase1": 0.55, "phase2": 0.38, "phase3": 0.61},
    "neurology": {"preclinical": 0.04, "phase1": 0.47, "phase2": 0.14, "phase3": 0.49},
    "rare": {"preclinical": 0.07, "phase1": 0.60, "phase2": 0.50, "phase3": 0.68},
    "metabolic": {"preclinical": 0.05, "phase1": 0.53, "phase2": 0.33, "phase3": 0.57},
    "infectious": {"preclinical": 0.06, "phase1": 0.57, "phase2": 0.42, "phase3": 0.63},
}

def _ind_group(indication: str) -> str:
    low = indication.lower()
    if any(k in low for k in ("cancer", "leukemia", "lymphoma", "melanoma", "sarcoma", "glioblastoma")):
        return "cancer"
    if any(k in low for k in ("crohn", "colitis", "arthritis", "lupus", "sclerosis")):
        return "immunology"
    if any(k in low for k in ("alzheimer", "parkinson", "als", "neurolog", "spinal")):
        return "neurology"
    if any(k in low for k in ("rare", "pediatric", "sickle", "thalassemia", "gaucher", "enzyme")):
        return "rare"
    if any(k in low for k in ("diabetes", "nash", "obesity", "heart", "atrial")):
        return "metabolic"
    if any(k in low for k in ("hiv", "hbv", "rsv", "infect")):
        return "infectious"
    return "cancer"


def _make_outcome(stage: str, indication: str) -> tuple[str, str, float]:
    group = _ind_group(indication)
    rates = SUCCESS_RATES.get(group, SUCCESS_RATES["cancer"])
    p_advance = rates.get(stage, 0.35)
    if random.random() < p_advance:
        outcome = "approved" if stage == "phase3" else "ongoing"
        decision = "go"
    else:
        outcome = f"discontinued_{stage}"
        decision = "no-go"
    return decision, outcome, p_advance


def _investment(stage: str) -> float:
    ranges = {
        "preclinical": (500_000, 5_000_000),
        "phase1": (3_000_000, 15_000_000),
        "phase2": (10_000_000, 60_000_000),
        "phase3": (30_000_000, 200_000_000),
    }
    lo, hi = ranges.get(stage, (1_000_000, 10_000_000))
    return round(random.uniform(lo, hi), -4)


def generate_clinical_trials_records(n: int = 80) -> list[RawRecord]:
    records = []
    for i in range(n):
        ind = random.choice(INDICATIONS)
        mech = random.choice(MECHANISMS)
        stage = random.choice(STAGES)
        decision, outcome, prior_p = _make_outcome(stage, ind)
        invest = _investment(stage)  # always record spend regardless of decision
        notes = (
            f"A phase {stage[-1] if stage != 'preclinical' else '0'} "
            f"interventional trial evaluating {mech} in patients with {ind}."
        )
        records.append(RawRecord(
            source="clinicaltrials",
            source_id=f"NCT{2000000 + i:07d}",
            url=f"https://clinicaltrials.gov/study/NCT{2000000 + i:07d}",
            title=f"{mech} in {ind} (Phase {'1' if stage == 'phase1' else '2' if stage == 'phase2' else '3'})",
            indication=ind,
            mechanism=mech,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=invest,
            raw_text=notes,
            extra={"prior_p": prior_p, "seed_idx": i},
        ))
    return records


def generate_vc_portfolio_records(n: int = 60) -> list[RawRecord]:
    records = []
    for i in range(n):
        vc = random.choice(VCS)
        ind = random.choice(INDICATIONS)
        mech = random.choice(MECHANISMS)
        stage = random.choice(["preclinical", "phase1", "phase2"])
        company_name = f"BioVenture-{i:03d}"
        decision, outcome, prior_p = _make_outcome(stage, ind)
        invest = _investment(stage)
        records.append(RawRecord(
            source=f"vc_{vc.lower().replace(' ', '_')}",
            source_id=f"vc_{i:04d}",
            url=f"https://example-vc.com/portfolio/{company_name.lower()}",
            title=company_name,
            indication=ind,
            mechanism=mech,
            clinical_stage=stage,
            decision="go",   # VC portfolio: existing investment = go decision
            outcome=outcome,
            investment_usd=invest,
            raw_text=(
                f"{company_name} is developing a {mech} for {ind}. "
                f"Currently in {stage}. Backed by {vc}. "
                f"Prior success probability: {prior_p:.0%}."
            ),
            extra={"vc": vc, "prior_p": prior_p},
        ))
    return records


def generate_sec_edgar_records(n: int = 40) -> list[RawRecord]:
    records = []
    companies = [
        "Moderna", "BioNTech", "Vertex", "Regeneron", "Alnylam",
        "Bluebird Bio", "Intellia", "CRISPR Therapeutics", "Editas",
        "Blueprint Medicines", "Relay Therapeutics", "C4 Therapeutics",
        "Karuna Therapeutics", "Arvinas", "Protagonist Therapeutics",
    ]
    for i in range(n):
        company = random.choice(companies)
        ind = random.choice(INDICATIONS)
        mech = random.choice(MECHANISMS)
        stage = random.choice(["phase2", "phase2", "phase3"])
        decision, outcome, prior_p = _make_outcome(stage, ind)
        form = random.choice(["8-K", "10-K"])
        if decision == "no-go":
            text_body = (
                f"{company} announced today the discontinuation of its {mech} "
                f"program in {ind} following a Phase {'2' if stage == 'phase2' else '3'} "
                f"interim analysis. The decision was based on insufficient efficacy signal."
            )
        else:
            text_body = (
                f"{company} reported positive {stage} data for {mech} in {ind}. "
                f"The company will advance the program to the next phase."
            )
        records.append(RawRecord(
            source="sec_edgar",
            source_id=f"0001{i:09d}",
            url=f"https://sec.gov/cgi-bin/browse-edgar?company={company.lower().replace(' ', '')}&CIK=&type={form}",
            title=f"{company} — {form}: {stage} {decision} for {ind[:40]}",
            indication=ind,
            mechanism=mech,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=_investment(stage),
            raw_text=text_body,
            extra={"company": company, "form": form, "prior_p": prior_p},
        ))
    return records


def generate_slide_records(n: int = 30) -> list[RawRecord]:
    conf_names = [
        "JPMorgan Healthcare Conference", "ASCO 2023", "ASH 2023",
        "BIO International Convention", "Biotech Showcase 2024",
    ]
    records = []
    for i in range(n):
        conf = random.choice(conf_names)
        ind = random.choice(INDICATIONS)
        mech = random.choice(MECHANISMS)
        stage = random.choice(STAGES)
        decision, outcome, prior_p = _make_outcome(stage, ind)
        invest = _investment(stage)
        records.append(RawRecord(
            source="slide_extractor",
            source_id=f"slide_{i:04d}",
            url=f"https://example-conference.com/{conf.lower().replace(' ', '-')}/slide-{i}",
            title=f"{conf} — {ind} {stage} decision slide",
            indication=ind,
            mechanism=mech,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=invest,
            raw_text=(
                f"Decision slide from {conf}. Program: {mech} for {ind}. "
                f"Stage: {stage}. Go/no-go decision: {decision}. "
                f"Evidence: {'positive biomarker data' if decision == 'go' else 'insufficient efficacy, trial terminated'}."
            ),
            extra={"conference": conf, "prior_p": prior_p},
        ))
    return records


def seed(db_path: Path = DB_PATH) -> int:
    all_records = (
        generate_clinical_trials_records(80) +
        generate_vc_portfolio_records(60) +
        generate_sec_edgar_records(40) +
        generate_slide_records(30)
    )
    inserted = bulk_upsert(all_records, db_path=db_path)
    print(f"Seeded {inserted} new records into {db_path}  (total generated: {len(all_records)})")
    return inserted


if __name__ == "__main__":
    seed()
    total = count(DB_PATH)
    print(f"Total records in store: {total}")
