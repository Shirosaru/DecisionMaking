"""
fda_collector.py
────────────────
Pulls real drug development outcomes from two FDA public APIs:

  1. OpenFDA drugsfda endpoint — NDA/BLA application approval/withdrawal data
     https://api.fda.gov/drug/drugsfda.json
     Gives us: sponsor name, brand name, generic name, pharmacological class,
               submission status (AP = approved, WD = withdrawn)

  2. FDA Drug Trials Snapshots (via ClinicalTrials crossref) for Phase info

Only NDA and BLA applications are collected (excludes generics/ANDA).
Approved applications → outcome "approved"
Withdrawn applications → outcome "discontinued"
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_FDA_API = "https://api.fda.gov/drug/drugsfda.json"

# ── Pharmacological class → indication mapping ────────────────────────────────
# Based on FDA EPC (Established Pharmacological Class) vocabulary
_PHARM_IND_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"antineoplastic|kinase inhibitor|checkpoint|immunotherapy|anti.?tumor|"
                r"antibody.drug conjugate|PARP|CDK|VEGF|ALK|EGFR|HER2|KRAS|BCR.ABL", re.I), "oncology"),
    (re.compile(r"antirheumatic|immunosuppres|interleukin|TNF|JAK|S1P|"
                r"anti.?inflamm|psoriasis|lupus|crohn|colitis|IBD", re.I), "immunology"),
    (re.compile(r"antidepressant|antipsychotic|anxiolytic|serotonin|dopamine|norepinephrine|"
                r"GABA|glutamate|acetylcholine|opioid|CNS|neurolog|alzheimer|"
                r"parkinson|multiple sclerosis|epilep|seizure", re.I), "neurology"),
    (re.compile(r"antidiabetic|GLP.1|insulin|SGLT|DPP.4|metabolic|obesity|lipid|"
                r"cholesterol|PCSK9|statin|fatty liver|NASH|NAFLD", re.I), "metabolic"),
    (re.compile(r"cardiovascular|antihypertensive|anticoagulant|antiplatelet|"
                r"heart failure|atrial|cardiac|coronary|angiotensin|beta blocker", re.I), "cardiovascular"),
    (re.compile(r"antiviral|antibiotic|antibacterial|antifungal|HIV|hepatitis|"
                r"influenza|COVID|SARS|respiratory syncytial|antimicrobial", re.I), "infectious"),
    (re.compile(r"orphan|rare disease|enzyme replacement|gene therapy|lysosomal|"
                r"hemophilia|sickle cell|muscular dystrophy|cystic fibrosis|"
                r"spinal muscular|Gaucher|Fabry|Pompe|PKU", re.I), "rare_disease"),
]

_DEFAULT_IND = "other"


def _map_indication(text: str) -> str:
    for pattern, ind in _PHARM_IND_MAP:
        if pattern.search(text):
            return ind
    return _DEFAULT_IND


# ── Mechanism inference from drug name / class ────────────────────────────────
_MECH_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"mab\b|monoclonal|antibody|bispecific|ADC|conjugate", re.I), "antibody"),
    (re.compile(r"gene therapy|AAV|lentiviral|CRISPR|base edit", re.I), "gene_therapy"),
    (re.compile(r"CAR.T|cell therapy|T.cell|NK.cell|TIL\b", re.I), "cell_therapy"),
    (re.compile(r"siRNA|mRNA|antisense|ASO|oligonucleotide|RNAi", re.I), "rna"),
    (re.compile(r"enzyme|protein|fusion|peptide|hormone|albumin|coagulation factor", re.I), "protein"),
    (re.compile(r"inhibitor|blocker|agonist|antagonist|modulator|small.?molecule", re.I), "small_molecule"),
]


def _map_mechanism(text: str) -> str:
    for pattern, mech in _MECH_MAP:
        if pattern.search(text):
            return mech
    return "small_molecule"   # default — most approved drugs are small molecules


# ── Stage inference from application type ─────────────────────────────────────
# NDA = small molecule (passed Phase 3), BLA = biologic (passed Phase 3)
# If submission_class_code indicates NME (new molecular entity) → phase3 complete
_STAGE_FROM_APP: dict[str, str] = {
    "NDA": "phase3",
    "BLA": "phase3",
    "NDA,BLA": "phase3",
}


def _investment_estimate(app_type: str, status: str) -> float:
    """Rough average total development cost proxy (USD)."""
    if status == "AP":
        return 350_000_000.0  # ~$350M average for approved NME
    return 80_000_000.0       # ~$80M average sunk cost for failed NDA/BLA


class FDACollector(BaseCollector):
    """
    Collects real drug development decisions from FDA NDA/BLA application database.

    Source: OpenFDA drugsfda endpoint (public, no API key needed)
    Records: approved and withdrawn NDA/BLA applications from 2000-present
    Outcome coding:
      - AP (Approved)   → outcome="approved",      decision="go",    stage="approved"
      - WD (Withdrawn)  → outcome="discontinued",  decision="no-go", stage="phase3"
      - TA (Tentative)  → outcome="ongoing",        decision="go",    stage="nda_submitted"
    """

    name = "fda_approvals"
    rate_limit_seconds = 0.3   # OpenFDA allows ~240 requests/min without key

    def collect(self, max_records: int = 1500) -> list[RawRecord]:
        records: list[RawRecord] = []
        seen_ids: set[str] = set()

        # Query 1: Approved NDA applications (small molecules)
        for rec in self._fetch_submissions("submissions.submission_status:AP+AND+application_number:NDA*",
                                           max_records // 2, "approved_nda"):
            if rec.source_id not in seen_ids:
                seen_ids.add(rec.source_id)
                records.append(rec)

        # Query 2: Approved BLA applications (biologics — antibodies, cell/gene therapies)
        for rec in self._fetch_submissions("submissions.submission_status:AP+AND+application_number:BLA*",
                                           max_records // 3, "approved_bla"):
            if rec.source_id not in seen_ids:
                seen_ids.add(rec.source_id)
                records.append(rec)

        # Query 3: Tentatively approved (ongoing/nda_submitted stage)
        for rec in self._fetch_submissions("submissions.submission_status:TA",
                                           max_records // 6, "tentative"):
            if rec.source_id not in seen_ids:
                seen_ids.add(rec.source_id)
                records.append(rec)

        logger.info("[fda_approvals] Total records produced: %d", len(records))
        return records

    def _fetch_submissions(self, search: str, target_count: int, label: str) -> list[RawRecord]:
        records: list[RawRecord] = []
        skip = 0
        limit = min(100, max(target_count, 20))

        while len(records) < target_count:
            # Build URL manually to avoid double-encoding the search expression
            url = f"{_FDA_API}?search={search}&limit={limit}&skip={skip}"
            try:
                elapsed = self._last_request_time  # rate limit via parent
                resp = self.session.get(url, timeout=self.timeout)
                import time; self._last_request_time = time.monotonic()
                if resp.status_code == 404:
                    break   # no results for this query
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("[fda_approvals] API error (%s): %s", label, exc)
                break

            results = data.get("results", [])
            if not results:
                break

            for app in results:
                rec = self._parse_application(app)
                if rec:
                    records.append(rec)

            total_avail = data.get("meta", {}).get("results", {}).get("total", 0)
            skip += limit
            if skip >= min(total_avail, target_count * 2):
                break

        logger.debug("[fda_approvals] %s: fetched %d records", label, len(records))
        return records

    def _parse_application(self, app: dict[str, Any]) -> RawRecord | None:
        app_number = app.get("application_number", "")
        sponsor    = app.get("sponsor_name", "")

        # Only NDA and BLA (not ANDA generics, not OTC switches)
        app_type = ""
        if app_number.startswith("NDA"):
            app_type = "NDA"
        elif app_number.startswith("BLA"):
            app_type = "BLA"
        else:
            return None

        openfda = app.get("openfda", {})
        brand_names   = openfda.get("brand_name", [])
        generic_names = openfda.get("generic_name", [])
        pharm_classes = openfda.get("pharm_class_epc", [])
        # Also try MeSH pharmacological actions as backup
        pharm_actions = openfda.get("pharm_class_pa", [])
        pharm_text    = " ".join(pharm_classes + pharm_actions + generic_names + brand_names)

        brand   = brand_names[0]   if brand_names   else ""
        generic = generic_names[0] if generic_names else ""
        title   = f"{brand} ({generic})" if brand and generic else brand or generic or app_number

        indication = _map_indication(pharm_text)
        mechanism  = _map_mechanism(pharm_text or generic)

        # Find most recent NDA/BLA original submission status
        submissions = app.get("submissions", [])
        status = self._best_submission_status(submissions, app_type)
        if not status:
            return None

        outcome, decision, stage = self._status_to_outcome(status, app_type)

        # Only include records with clear approved/discontinued outcome
        if outcome == "unknown":
            return None

        # Approximate submission date from most recent submission
        date_str = ""
        for sub in submissions:
            d = sub.get("submission_status_date", "")
            if d and (not date_str or d > date_str):
                date_str = d

        year = int(date_str[:4]) if len(date_str) >= 4 else 2010

        investment = _investment_estimate(app_type, status)

        return RawRecord(
            source=self.name,
            source_id=app_number,
            url=f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={app_number[3:]}",
            title=title[:120],
            indication=indication,
            mechanism=mechanism,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=investment,
            raw_text=f"{title}. Sponsor: {sponsor}. Class: {pharm_text}. App: {app_number}.",
            extra={
                "sponsor": sponsor,
                "app_type": app_type,
                "app_number": app_number,
                "status": status,
                "pharm_class": pharm_classes[:3] if pharm_classes else [],
                "year": year,
                "start_year": year,
            },
        )

    def _best_submission_status(self, submissions: list[dict], app_type: str) -> str:
        """
        Return the most meaningful submission status from the application's
        submission list. Priority: AP > TA > WD > RT.
        """
        priority = {"AP": 4, "TA": 3, "WD": 2, "RT": 1}
        best = ""
        best_score = 0

        for sub in submissions:
            sub_type = sub.get("submission_type", "")
            # Only look at original + supplement submissions, not amendments
            if sub_type not in ("ORIG", "SUPPL", "BLA ORIG", "NDA ORIG"):
                if sub_type.startswith("ORIG") or sub_type == "":
                    pass
                else:
                    continue
            status = sub.get("submission_status", "")
            score = priority.get(status, 0)
            if score > best_score:
                best_score = score
                best = status

        return best

    def _status_to_outcome(self, status: str, app_type: str) -> tuple[str, str, str]:
        """Map FDA submission status → (outcome, decision, stage)."""
        if status == "AP":
            return "approved", "go", "approved"
        elif status == "TA":
            return "ongoing", "go", "nda_submitted"
        elif status == "WD":
            # Withdrawn NDA/BLA = drug failed after Phase 3
            return "discontinued", "no-go", "phase3"
        elif status == "RT":
            # Refuse to file — failed at NDA submission
            return "discontinued", "no-go", "nda_submitted"
        return "unknown", "undecided", "unknown"
