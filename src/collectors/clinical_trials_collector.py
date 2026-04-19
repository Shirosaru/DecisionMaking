from __future__ import annotations

import logging
import re
from typing import Any

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_CT_API = "https://clinicaltrials.gov/api/v2/studies"

# Map NCT status to our vocabulary
_STATUS_MAP = {
    "COMPLETED": "ongoing",
    "TERMINATED": "discontinued_p2",
    "WITHDRAWN": "discontinued_p1",
    "SUSPENDED": "discontinued_p2",
    "ACTIVE_NOT_RECRUITING": "ongoing",
    "RECRUITING": "ongoing",
    "APPROVED_FOR_MARKETING": "approved",
}

_PHASE_MAP = {
    "PHASE1": "phase1",
    "PHASE2": "phase2",
    "PHASE3": "phase3",
    "PHASE4": "approved",
    "EARLY_PHASE1": "phase1",
    "NA": "preclinical",
}


def _extract_text(study: dict[str, Any]) -> str:
    proto = study.get("protocolSection", {})
    parts: list[str] = []
    for section in ("identificationModule", "descriptionModule", "conditionsModule",
                    "interventionsModule", "outcomesModule", "eligibilityModule"):
        sec = proto.get(section, {})
        if isinstance(sec, dict):
            parts.append(str(sec))
    return " ".join(parts)


def _first_sponsor(study: dict[str, Any]) -> str:
    sponsors = (
        study.get("protocolSection", {})
             .get("sponsorCollaboratorsModule", {})
             .get("leadSponsor", {})
    )
    return sponsors.get("name", "")


class ClinicalTrialsCollector(BaseCollector):
    """
    Pulls industry-sponsored clinical studies from ClinicalTrials.gov API v2.

    Two query strategies combined:
      1. Disease-area queries — broad coverage across all therapeutic areas
      2. Pharma-sponsor queries — targeted terminated/withdrawn trial pulls
         for 20 major pharma companies (Pfizer, Roche, Novartis, Merck, etc.)

    This captures:
      - Go decisions (completed/ongoing trials)
      - No-go decisions (terminated/withdrawn = killed programs)
      - The full spectrum from preclinical-adjacent (Phase 1) to NDA stage
    """

    name = "clinicaltrials"
    rate_limit_seconds = 0.4

    # Disease area queries (broad coverage)
    _DISEASE_QUERIES = [
        "cancer OR oncology OR tumor OR leukemia OR lymphoma OR sarcoma",
        "rare disease OR orphan drug OR inherited OR genetic disorder",
        "autoimmune OR immunology OR rheumatoid OR lupus OR crohn OR colitis",
        "neurology OR alzheimer OR parkinson OR ALS OR multiple sclerosis",
        "cardiovascular OR heart failure OR hypertension OR coronary",
        "metabolic OR diabetes OR obesity OR NASH OR fatty liver",
        "infectious disease OR HIV OR hepatitis OR antiviral OR antibacterial",
        "gene therapy OR cell therapy OR CAR-T OR CRISPR OR mRNA",
    ]
    _PER_DISEASE_QUERY = 300

    # Major pharma sponsors — targeted kill-decision capture
    # These queries pull TERMINATED/WITHDRAWN trials from mega-pharma
    # which represent the most valuable research decision signals
    _PHARMA_SPONSORS = [
        "Pfizer",
        "Roche",
        "Novartis",
        "Merck",
        "Johnson and Johnson",
        "AbbVie",
        "Bristol-Myers Squibb",
        "AstraZeneca",
        "GlaxoSmithKline",
        "Eli Lilly",
        "Sanofi",
        "Bayer",
        "Boehringer Ingelheim",
        "Amgen",
        "Gilead Sciences",
        "Takeda",
        "Biogen",
        "Regeneron",
        "Vertex Pharmaceuticals",
        "Moderna",
    ]
    _PER_PHARMA_QUERY = 150

    def collect(self, max_records: int = 2000) -> list[RawRecord]:
        records: list[RawRecord] = []
        seen_ids: set[str] = set()

        # --- Part 1: Disease-area queries ---
        per_query = min(self._PER_DISEASE_QUERY, max_records // len(self._DISEASE_QUERIES) + 50)
        for query_term in self._DISEASE_QUERIES:
            if len(records) >= max_records:
                break
            for rec in self._fetch_query_term(query_term, per_query, sponsor_filter=None):
                if rec.source_id not in seen_ids:
                    seen_ids.add(rec.source_id)
                    records.append(rec)
                if len(records) >= max_records:
                    break

        # --- Part 2: Pharma-sponsor terminated/withdrawn queries ---
        # Focus on TERMINATED = kill decisions (most valuable for training)
        pharma_budget = max_records - len(records)
        per_pharma = min(self._PER_PHARMA_QUERY, pharma_budget // max(len(self._PHARMA_SPONSORS), 1) + 20)

        for sponsor in self._PHARMA_SPONSORS:
            if len(records) >= max_records:
                break
            for rec in self._fetch_query_term(
                query_term=None,
                target=per_pharma,
                sponsor_filter=sponsor,
                status_filter="TERMINATED,WITHDRAWN,SUSPENDED",
            ):
                if rec.source_id not in seen_ids:
                    seen_ids.add(rec.source_id)
                    records.append(rec)
                if len(records) >= max_records:
                    break

        logger.info("ClinicalTrials: collected %d records", len(records))
        return records[:max_records]

    def _fetch_query_term(
        self,
        query_term: str | None,
        target: int,
        sponsor_filter: str | None = None,
        status_filter: str = "TERMINATED,COMPLETED,WITHDRAWN,SUSPENDED,ACTIVE_NOT_RECRUITING,RECRUITING",
    ) -> list[RawRecord]:
        records: list[RawRecord] = []
        next_page_token: str | None = None

        while len(records) < target:
            batch_size = min(100, target - len(records))
            params: dict[str, Any] = {
                "filter.overallStatus": status_filter,
                "pageSize": batch_size,
                "format": "json",
            }
            if query_term:
                params["query.term"] = query_term
                params["query.spons"] = "INDUSTRY"
            if sponsor_filter:
                params["query.spons"] = sponsor_filter
            if next_page_token:
                params["pageToken"] = next_page_token

            try:
                resp = self._get(_CT_API, params=params, accept_json=True)
                data = resp.json()
            except Exception as exc:
                logger.error("ClinicalTrials fetch error: %s", exc)
                break

            studies = data.get("studies", [])
            if not studies:
                break

            for study in studies:
                record = self._parse_study(study)
                if record:
                    records.append(record)

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        return records

    def _parse_study(self, study: dict[str, Any]) -> RawRecord | None:
        proto = study.get("protocolSection", {})
        id_mod = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design_mod = proto.get("designModule", {})
        cond_mod = proto.get("conditionsModule", {})
        interv_mod = proto.get("interventionsModule", {})
        desc_mod = proto.get("descriptionModule", {})

        nct_id = id_mod.get("nctId", "")
        if not nct_id:
            return None

        phases = design_mod.get("phases", ["NA"])
        phase_raw = phases[0] if phases else "NA"
        stage = _PHASE_MAP.get(phase_raw, "phase1")

        status_raw = status_mod.get("overallStatus", "")
        outcome = _STATUS_MAP.get(status_raw, "unknown")

        # Determine outcome with stage context (TERMINATED in Phase3 = discontinued_p3, etc.)
        if status_raw == "TERMINATED":
            outcome = f"discontinued_{stage}"

        conditions = cond_mod.get("conditions", [])
        indication = conditions[0] if conditions else "unknown"

        interventions = interv_mod.get("interventions", [])
        mechanism = interventions[0].get("name", "unknown") if interventions else "unknown"

        brief = desc_mod.get("briefSummary", "")
        title = id_mod.get("briefTitle", "")

        # Decision: TERMINATED = no-go, COMPLETED = go (moved forward or ended planned)
        decision = "no-go" if status_raw in ("TERMINATED", "WITHDRAWN", "SUSPENDED") else "go"

        return RawRecord(
            source=self.name,
            source_id=nct_id,
            url=f"https://clinicaltrials.gov/study/{nct_id}",
            title=title,
            indication=indication,
            mechanism=mechanism,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=0.0,
            raw_text=f"{title}. {brief}",
            extra={
                "sponsor": _first_sponsor(study),
                "status_raw": status_raw,
                "phase_raw": phase_raw,
            },
        )
