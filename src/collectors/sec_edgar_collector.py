from __future__ import annotations

import logging
import re
from typing import Any

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_BASE = "https://www.sec.gov"

# Pharma SIC code prefixes — used to filter non-pharma filers
_PHARMA_SICS = {"2830", "2831", "2833", "2834", "2835", "2836", "8731", "8099", "5122"}

_STAGE_RE = re.compile(
    r"phase\s*([1i][i]?[i]?|[23]|one|two|three)\b", re.IGNORECASE
)

_STAGE_TEXT_MAP = {
    "1": "phase1", "i": "phase1", "one": "phase1",
    "2": "phase2", "ii": "phase2", "two": "phase2",
    "3": "phase3", "iii": "phase3", "three": "phase3",
}


def _detect_stage(text: str) -> str:
    m = _STAGE_RE.search(text)
    if m:
        return _STAGE_TEXT_MAP.get(m.group(1).lower(), "phase1")
    return "unknown"


def _detect_decision(text: str) -> str:
    lowered = text.lower()
    if any(k in lowered for k in ("terminat", "discontinu", "halted", "stopped")):
        return "no-go"
    if any(k in lowered for k in ("advance", "initiat", "enroll", "positive", "approved")):
        return "go"
    return "undecided"


def _detect_outcome(text: str, decision: str) -> str:
    lowered = text.lower()
    if "fda approv" in lowered or "nda approv" in lowered or "bla approv" in lowered:
        return "approved"
    if decision == "no-go":
        stage = _detect_stage(text)
        if stage != "unknown":
            return f"discontinued_{stage}"
        return "discontinued_p2"
    return "ongoing"


class SECEdgarCollector(BaseCollector):
    """
    Queries SEC EDGAR full-text search for 8-K and 10-K filings
    containing pharmaceutical pipeline go/no-go language.
    """

    name = "sec_edgar"
    rate_limit_seconds = 0.5

    def collect(self, max_records: int = 100) -> list[RawRecord]:
        records: list[RawRecord] = []
        # (query_text, implied_stage, implied_decision)
        queries: list[tuple[str, str, str]] = [
            ('"phase 2" "discontinue" "clinical trial"', "phase2", "no-go"),
            ('"phase 3" "terminate" "pipeline"', "phase3", "no-go"),
            ('"phase 1" "discontinue" "clinical trial"', "phase1", "no-go"),
            ('"phase 2" "advance" "clinical trial"', "phase2", "go"),
            ('"NDA" OR "BLA" "FDA approval"', "phase3", "go"),
        ]

        per_query = max(max_records // len(queries), 10)

        for query, hint_stage, hint_decision in queries:
            if len(records) >= max_records:
                break
            batch = self._search(query, hint_stage, hint_decision, per_query)
            records.extend(batch)

        logger.info("SEC EDGAR: collected %d records", len(records))
        return records

    def _search(
        self, query: str, hint_stage: str, hint_decision: str, limit: int
    ) -> list[RawRecord]:
        params = {
            "q": query,
            "dateRange": "custom",
            "startdt": "2018-01-01",
            "enddt": "2025-01-01",
            "forms": "8-K,10-K",
        }
        try:
            resp = self._get(_EDGAR_SEARCH, params=params, accept_json=True)
            data = resp.json()
        except Exception as exc:
            logger.warning("EDGAR search failed for query %r: %s", query, exc)
            return []

        hits = data.get("hits", {}).get("hits", [])
        records: list[RawRecord] = []

        for hit in hits[:limit]:
            src = hit.get("_source", {})
            record = self._parse_hit(src, hint_stage, hint_decision)
            if record:
                records.append(record)

        return records

    def _parse_hit(
        self, src: dict[str, Any], hint_stage: str, hint_decision: str
    ) -> RawRecord | None:
        # EDGAR EFTS v2 returns 'adsh' (not 'accession_no') and 'display_names' (not 'entity_name')
        accession = src.get("adsh", "")
        if not accession:
            return None

        # Skip non-pharma filers using SIC codes
        sics = src.get("sics", [])
        if sics and not any(s in _PHARMA_SICS for s in sics):
            return None

        raw_names: list[str] = src.get("display_names", [])
        entity = raw_names[0].split("(")[0].strip() if raw_names else "unknown"
        form = src.get("form", src.get("root_forms", ["8-K"])[0])
        description = src.get("file_description", "") or src.get("period_ending", "")

        ciks: list[str] = src.get("ciks", [])
        cik = ciks[0].lstrip("0") if ciks else ""
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}"
            if cik
            else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=&type={form}"
        )

        # Use query-level hints (the filing was found because it matches the query keywords)
        outcome = _detect_outcome(hint_stage + " " + hint_decision, hint_decision)

        return RawRecord(
            source=self.name,
            source_id=accession,
            url=url,
            title=f"{entity} — {form} ({description[:80]})",
            indication="unknown",
            mechanism="unknown",
            clinical_stage=hint_stage,
            decision=hint_decision,
            outcome=outcome,
            investment_usd=0.0,
            raw_text=description,
            extra={"entity": entity, "form": form, "accession": accession, "sics": sics},
        )
