"""
chembl_collector.py
───────────────────
Collects real drug research decisions from ChEMBL — the world's largest
publicly available database of bioactive drug-like molecules.

Source:  EBI ChEMBL API  https://www.ebi.ac.uk/chembl/api/data/
License: CC BY-SA 3.0 (open access)

Coverage:
  • 16,754 drug molecules with max clinical phase ≥ 1
  • 58,741 drug-indication pairs (molecule × indication × max phase)
  • Includes ALL pharma (Pfizer, Roche, Novartis, Merck, AZ, J&J, Lilly,
    Sanofi, BMS, AbbVie, GSK, Bayer, Boehringer, Amgen, Gilead, Takeda...)
    AND all biotech/VC-backed companies

Decision mapping (per drug-indication pair):
  max_phase_for_ind = 4.0 → approved     (go all the way)
  max_phase_for_ind = 3.0 → ongoing      (Phase 3 active or recently stopped)
  max_phase_for_ind = 2.0 → discontinued_phase2  (stopped at Phase 2 — key kill)
  max_phase_for_ind = 1.0 → discontinued_phase1  (stopped at Phase 1)
  withdrawn_flag on molecule → discontinued

This is the richest free source of research go/no-go decisions.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_API = "https://www.ebi.ac.uk/chembl/api/data"

# ── EFO/MeSH indication → our vocabulary ─────────────────────────────────────
import re

_IND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"neoplasm|cancer|carcinoma|oncol|leukemia|lymphoma|melanoma|"
                r"glioblastoma|sarcoma|myeloma|tumor|tumour", re.I), "oncology"),
    (re.compile(r"rare disease|orphan|lysosomal|gaucher|fabry|pompe|niemann|"
                r"hunter|hurler|hemophilia|haemophilia|sickle cell|muscular dystrophy|"
                r"cystic fibr|spinal muscular|phenylketon|pku|tay.sachs", re.I), "rare_disease"),
    (re.compile(r"autoimmun|rheumatoid|arthritis|lupus|crohn|colitis|psoriasis|"
                r"multiple sclerosis|immunol|inflamm|ankylosing|sjogren|"
                r"atopic|eczema|uveitis|vasculitis", re.I), "immunology"),
    (re.compile(r"alzheimer|parkinson|dementia|neurodegen|ALS\b|amyotroph|"
                r"huntington|epilep|seizure|schizophrenia|depression|anxiety|"
                r"bipolar|neurolog|CNS\b|migraine|peripheral neuropathy", re.I), "neurology"),
    (re.compile(r"cardiovasc|heart failure|atrial|coronary|hypertension|"
                r"cholesterol|atheroscler|myocardial|stroke|thrombosis|"
                r"anticoagul|antiarrhythm|angina", re.I), "cardiovascular"),
    (re.compile(r"diabetes|obesity|metabolic|NASH\b|NAFLD|fatty liver|"
                r"insulin|GLP.1|SGLT|lipid|triglycerid|hypoglycaem", re.I), "metabolic"),
    (re.compile(r"HIV|hepatitis|influenza|COVID|SARS|antibiotic|antibacter|"
                r"antiviral|antifungal|tuberculosis|malaria|infectious|"
                r"respiratory syncytial|RSV\b|pneumonia", re.I), "infectious"),
]

_DEFAULT_IND = "other"


def _map_indication(text: str) -> str:
    for pattern, ind in _IND_PATTERNS:
        if pattern.search(text):
            return ind
    return _DEFAULT_IND


# ── Mechanism mapping from molecule_type + free text ─────────────────────────
_MECH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"monoclonal|antibody|bispecific|ADC|conjugate|mab\b", re.I), "antibody"),
    (re.compile(r"gene therapy|AAV|lentiviral|CRISPR|base edit", re.I), "gene_therapy"),
    (re.compile(r"CAR.T|cell therapy|T.cell|NK.cell|TIL\b|adoptive", re.I), "cell_therapy"),
    (re.compile(r"siRNA|mRNA|antisense|ASO\b|oligonucleotide|RNAi|"
                r"aptamer|nucleotide", re.I), "rna"),
    (re.compile(r"enzyme replacement|fusion protein|coagulation factor|"
                r"peptide hormone|albumin fusion", re.I), "protein"),
]

_MOL_TYPE_MAP = {
    "Antibody":       "antibody",
    "Protein":        "protein",
    "Oligonucleotide":"rna",
    "Cell":           "cell_therapy",
    "Oligosaccharide":"protein",
    "Small molecule": "small_molecule",
}


def _map_mechanism(mol_type: str, mechanism_text: str) -> str:
    for pattern, mech in _MECH_PATTERNS:
        if pattern.search(mechanism_text or ""):
            return mech
    return _MOL_TYPE_MAP.get(mol_type, "small_molecule")


# ── Phase → stage / outcome / decision ───────────────────────────────────────
def _phase_to_record(max_phase: float, withdrawn: bool) -> tuple[str, str, str]:
    """Return (stage, outcome, decision)."""
    if withdrawn:
        return "phase3", "discontinued", "no-go"
    if max_phase >= 4.0:
        return "approved", "approved", "go"
    if max_phase >= 3.0:
        return "phase3", "ongoing", "go"
    if max_phase >= 2.0:
        return "phase2", "discontinued_phase2", "no-go"
    if max_phase >= 1.0:
        return "phase1", "discontinued_phase1", "no-go"
    return "preclinical", "discontinued_preclinical", "no-go"


# ── Approximate investment by stage ──────────────────────────────────────────
_INVEST_BY_STAGE = {
    "approved":     500_000_000.0,
    "phase3":       180_000_000.0,
    "phase2":        40_000_000.0,
    "phase1":        12_000_000.0,
    "preclinical":    3_000_000.0,
}


class ChEMBLCollector(BaseCollector):
    """
    Collects drug research decisions from the ChEMBL database.

    Strategy:
      1. Fetch drug_indication records (molecule × indication × max_phase)
      2. Batch-fetch molecule metadata (name, type, withdrawn, first_approval)
      3. Build one RawRecord per (molecule, indication) pair

    Each record = one research decision:
      - Drug entered Phase 1 (IND decision)
      - Drug advanced to Phase 2/3 (stage-advance go decision)
      - Drug stopped at Phase 2 (kill decision — the most informative)
      - Drug approved (full success)
    """

    name = "chembl"
    rate_limit_seconds = 0.15   # ChEMBL allows ~20 req/s

    def collect(self, max_records: int = 8000) -> list[RawRecord]:
        logger.info("[chembl] Starting collection (target=%d)", max_records)

        # Step 1: collect drug_indication pages
        ind_records = self._fetch_drug_indications(max_records)
        logger.info("[chembl] Fetched %d drug-indication pairs", len(ind_records))

        # Step 2: batch-fetch molecule metadata
        mol_ids = list({r["molecule_chembl_id"] for r in ind_records})
        mol_meta = self._fetch_molecule_batch(mol_ids)
        logger.info("[chembl] Fetched metadata for %d molecules", len(mol_meta))

        # Step 3: build RawRecords
        records: list[RawRecord] = []
        seen: set[str] = set()

        for ind_rec in ind_records:
            mol_id = ind_rec["molecule_chembl_id"]
            mol = mol_meta.get(mol_id, {})

            rec = self._build_record(ind_rec, mol)
            if rec and rec.source_id not in seen:
                seen.add(rec.source_id)
                records.append(rec)

        logger.info("[chembl] Total records produced: %d", len(records))
        return records

    # ── Fetch drug_indication pages ───────────────────────────────────────────

    def _fetch_drug_indications(self, max_count: int) -> list[dict]:
        results: list[dict] = []
        offset = 0
        limit = 1000

        while len(results) < max_count:
            url = (f"{_API}/drug_indication.json"
                   f"?max_phase_for_ind__gte=1&limit={limit}&offset={offset}&format=json")
            try:
                self.session.headers["Accept"] = "application/json"
                time.sleep(self.rate_limit_seconds)
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("[chembl] drug_indication fetch error at offset %d: %s", offset, exc)
                break

            batch = data.get("drug_indications", [])
            if not batch:
                break

            results.extend(batch)

            total = data.get("page_meta", {}).get("total_count", 0)
            offset += limit
            if offset >= min(total, max_count * 2):
                break

        return results[:max_count]

    # ── Batch-fetch molecule metadata ─────────────────────────────────────────

    def _fetch_molecule_batch(self, mol_ids: list[str]) -> dict[str, dict]:
        meta: dict[str, dict] = {}
        batch_size = 100

        for i in range(0, len(mol_ids), batch_size):
            batch = mol_ids[i : i + batch_size]
            ids_str = ",".join(batch)
            url = (f"{_API}/molecule.json"
                   f"?molecule_chembl_id__in={ids_str}&limit={batch_size}&format=json")
            try:
                time.sleep(self.rate_limit_seconds)
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                for mol in data.get("molecules", []):
                    meta[mol["molecule_chembl_id"]] = mol
            except Exception as exc:
                logger.warning("[chembl] molecule batch fetch error: %s", exc)

        return meta

    # ── Build a single RawRecord ──────────────────────────────────────────────

    def _build_record(self, ind_rec: dict, mol: dict) -> RawRecord | None:
        mol_id = ind_rec.get("molecule_chembl_id", "")
        if not mol_id:
            return None

        # Indication text
        efo_term   = ind_rec.get("efo_term", "") or ""
        mesh_head  = ind_rec.get("mesh_heading", "") or ""
        ind_text   = efo_term or mesh_head or "unknown"
        indication = _map_indication(f"{efo_term} {mesh_head}")

        # Phase
        try:
            max_phase = float(ind_rec.get("max_phase_for_ind", 1))
        except (TypeError, ValueError):
            max_phase = 1.0

        # Molecule metadata
        pref_name   = (mol.get("pref_name") or mol_id).title()
        mol_type    = mol.get("molecule_type", "Small molecule")
        withdrawn   = bool(mol.get("withdrawn_flag", False))
        first_appr  = mol.get("first_approval")  # year or None
        black_box   = bool(mol.get("black_box_warning", 0))
        orphan      = bool(mol.get("orphan", 0))

        # Override phase if we know it's approved (first_approval set)
        if first_appr and max_phase < 4.0:
            max_phase = 4.0

        stage, outcome, decision = _phase_to_record(max_phase, withdrawn)

        mechanism = _map_mechanism(mol_type, f"{pref_name} {efo_term}")
        investment = _invest_by_stage = _INVEST_BY_STAGE.get(stage, 10_000_000.0)

        # Build a stable source_id from molecule + indication mesh code
        mesh_id = ind_rec.get("mesh_id", "")
        source_id = f"{mol_id}_{mesh_id or efo_term[:20].replace(' ', '_')}"

        title = f"{pref_name} in {ind_text}"
        raw_text = (f"{pref_name}. Indication: {ind_text}. "
                    f"Phase: {max_phase}. Type: {mol_type}. "
                    f"First approval: {first_appr or 'none'}. "
                    f"Withdrawn: {withdrawn}.")

        # Approximate start year from first_approval (work backward from typical dev time)
        year = int(first_appr) - 10 if first_appr else 2010

        return RawRecord(
            source=self.name,
            source_id=source_id,
            url=f"https://www.ebi.ac.uk/chembl/compound_report_card/{mol_id}/",
            title=title[:120],
            indication=indication,
            mechanism=mechanism,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=investment,
            raw_text=raw_text,
            extra={
                "molecule_chembl_id": mol_id,
                "pref_name": pref_name,
                "molecule_type": mol_type,
                "first_approval": first_appr,
                "max_phase": max_phase,
                "withdrawn_flag": withdrawn,
                "black_box_warning": black_box,
                "orphan": orphan,
                "efo_term": efo_term,
                "mesh_heading": mesh_head,
                "start_year": year,
                "year": year,
            },
        )
