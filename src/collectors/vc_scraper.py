from __future__ import annotations

"""
VC / Pharma Portfolio Collector — PubMed API backend.

Originally designed as a VC website scraper, but those sites are
JavaScript-rendered and inaccessible to a plain HTTP client.  Replaced with
PubMed E-utilities API which provides stable, rich, free access to published
clinical trial outcome papers — a direct proxy for real go/no-go decisions.
"""

import logging
import re
from typing import Any

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_ESEARCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
_EFETCH   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
_PUBMED_BASE = "https://pubmed.ncbi.nlm.nih.gov"

# (search_query, hint_stage, hint_decision)
_QUERIES: list[tuple[str, str, str]] = [
    ('"phase 1" "terminated"[tiab] "clinical trial"[pt]',          "phase1",      "no-go"),
    ('"phase 2" "terminated"[tiab] "clinical trial"[pt]',          "phase2",      "no-go"),
    ('"phase 3" "terminated"[tiab] "clinical trial"[pt]',          "phase3",      "no-go"),
    ('"phase 2" "discontinued"[tiab] "clinical trial"[pt]',        "phase2",      "no-go"),
    ('"phase 2" "positive" "met primary endpoint" "clinical trial"', "phase2",    "go"),
    ('"phase 3" "FDA approval" OR "NDA" "clinical trial"[pt]',     "phase3",      "go"),
    ('"first-in-human" "phase 1" "oncology" clinical trial',        "preclinical", "go"),
    ('"IND application" "first-in-human" "preclinical"',            "preclinical", "go"),
]

_STAGE_RE = re.compile(r"\bphase\s*([123i]+|one|two|three)\b", re.IGNORECASE)
_STAGE_MAP = {
    "1": "phase1", "i": "phase1", "one": "phase1",
    "2": "phase2", "ii": "phase2", "two": "phase2",
    "3": "phase3", "iii": "phase3", "three": "phase3",
}

_INDICATION_RE = re.compile(
    r"\b(cancer|carcinoma|oncology|leukemia|lymphoma|melanoma|glioblastoma|"
    r"rare disease|autoimmune|rheumatoid arthritis|lupus|crohn|colitis|"
    r"neurology|alzheimer|parkinson|ALS|multiple sclerosis|"
    r"cardiovascular|heart failure|atherosclerosis|hypertension|"
    r"metabolic|diabetes|obesity|NASH|NAFLD|"
    r"infectious|HIV|hepatitis|influenza|COVID|SARS|"
    r"inflammation|psoriasis|atopic dermatitis|"
    r"hematology|sickle cell|hemophilia|gene therapy)\b",
    re.IGNORECASE,
)

_MECHANISM_RE = re.compile(
    r"\b(antibody|monoclonal|bispecific|ADC|antibody.drug conjugate|"
    r"small molecule|inhibitor|kinase|checkpoint|PD.?1|PD.?L1|CTLA.?4|"
    r"cell therapy|CAR.?T|T.?cell|NK cell|"
    r"gene therapy|CRISPR|AAV|lentiviral|"
    r"RNA|siRNA|mRNA|antisense|oligonucleotide|"
    r"enzyme|protein|peptide|vaccine)\b",
    re.IGNORECASE,
)


def _extract_stage(text: str, hint: str) -> str:
    m = _STAGE_RE.search(text)
    if m:
        return _STAGE_MAP.get(m.group(1).lower(), hint)
    return hint


def _extract_indication(text: str) -> str:
    m = _INDICATION_RE.search(text)
    return m.group(0).lower() if m else "unknown"


def _extract_mechanism(text: str) -> str:
    m = _MECHANISM_RE.search(text)
    return m.group(0).lower() if m else "unknown"


def _hint_outcome(stage: str, decision: str) -> str:
    if decision == "go":
        return "ongoing" if stage != "phase3" else "approved"
    return f"discontinued_{stage}" if stage != "unknown" else "discontinued_p2"


class VCScraper(BaseCollector):
    """
    Bioventure clinical decision collector backed by PubMed E-utilities.

    Targets published papers reporting clinical trial outcomes to surface
    real-world go/no-go decisions across all development stages.
    """

    name = "pubmed"
    rate_limit_seconds = 0.35   # NCBI allows ~3 req/s without API key

    def collect(self, max_records: int = 200) -> list[RawRecord]:
        records: list[RawRecord] = []
        per_query = max(max_records // len(_QUERIES), 5)

        for query, hint_stage, hint_decision in _QUERIES:
            if len(records) >= max_records:
                break
            batch = self._run_query(query, hint_stage, hint_decision, per_query)
            records.extend(batch)
            logger.info(
                "[PubMed] stage=%s decision=%s -> %d records",
                hint_stage, hint_decision, len(batch),
            )

        logger.info("PubMed collector total: %d records", len(records))
        return records

    def _run_query(
        self, query: str, hint_stage: str, hint_decision: str, limit: int
    ) -> list[RawRecord]:
        # Step 1: esearch for PMIDs
        try:
            sr = self._get(
                _ESEARCH,
                params={
                    "db": "pubmed", "term": query, "retmax": limit,
                    "retmode": "json", "usehistory": "n", "sort": "relevance",
                },
                accept_json=True,
            )
            pmids: list[str] = sr.json().get("esearchresult", {}).get("idlist", [])
        except Exception as exc:
            logger.warning("PubMed esearch failed: %s", exc)
            return []

        if not pmids:
            return []

        # Step 2: esummary for title/journal/date
        try:
            sumr = self._get(
                _ESUMMARY,
                params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
                accept_json=True,
            )
            summary: dict[str, Any] = sumr.json().get("result", {})
        except Exception as exc:
            logger.warning("PubMed esummary failed: %s", exc)
            return []

        # Step 3: efetch for abstract text
        try:
            absr = self._get(
                _EFETCH,
                params={
                    "db": "pubmed", "id": ",".join(pmids),
                    "rettype": "abstract", "retmode": "text",
                },
                accept_json=False,
            )
            abstract_blocks = re.split(r"\n\d+\. ", "\n" + absr.text)
            abstracts = [b.strip() for b in abstract_blocks if b.strip()]
        except Exception as exc:
            logger.warning("PubMed efetch failed: %s", exc)
            abstracts = []

        records: list[RawRecord] = []
        for i, pmid in enumerate(pmids):
            art: dict[str, Any] = summary.get(pmid, {})
            title: str = art.get("title", "")
            if not title:
                continue

            abstract = abstracts[i] if i < len(abstracts) else ""
            full_text = f"{title}. {abstract}"

            stage     = _extract_stage(full_text, hint_stage)
            indication= _extract_indication(full_text)
            mechanism = _extract_mechanism(full_text)
            outcome   = _hint_outcome(stage, hint_decision)

            records.append(RawRecord(
                source="pubmed",
                source_id=pmid,
                url=f"{_PUBMED_BASE}/{pmid}/",
                title=title[:200],
                indication=indication,
                mechanism=mechanism,
                clinical_stage=stage,
                decision=hint_decision,
                outcome=outcome,
                investment_usd=0.0,
                raw_text=full_text[:3000],
                extra={
                    "journal": art.get("source", ""),
                    "pubdate": art.get("pubdate", ""),
                    "pmid": pmid,
                },
            ))

        return records
