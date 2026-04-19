"""
ema_collector.py
────────────────
Collects real drug approval/refusal/withdrawal decisions from the European
Medicines Agency (EMA) medicines register.

Source:  EMA public XLSX download (updated nightly)
         https://www.ema.europa.eu/en/documents/report/medicines-output-medicines-report_en.xlsx
License: Open data — EU Open Data Portal (CC BY 4.0)

Coverage:
  • ~1,000-1,500 human medicines centrally authorised in the EU
  • Status: AUTHORISED, WITHDRAWN, REFUSED, SUSPENDED
  • Active substance, indication category, decision date
  • Covers all big pharma AND biotech (Pfizer, Roche, Novartis, AZ, BMS,
    J&J, Lilly, Sanofi, AbbVie, Amgen, Biogen, Vertex, Regeneron…)

Decision mapping:
  AUTHORISED  → approved (go)
  REFUSED     → discontinued (no-go — failed regulatory review)
  WITHDRAWN   → discontinued (company voluntarily pulled)
  SUSPENDED   → discontinued (safety concerns)
"""
from __future__ import annotations

import io
import logging
import re
from typing import Any

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_EMA_XLSX = ("https://www.ema.europa.eu/en/documents/report/"
             "medicines-output-medicines-report_en.xlsx")

# Status → outcome
_STATUS_MAP = {
    "AUTHORISED":    ("approved",      "go"),
    "WITHDRAWN":     ("discontinued",  "no-go"),
    "REFUSED":       ("discontinued",  "no-go"),
    "SUSPENDED":     ("discontinued",  "no-go"),
    "REVOKED":       ("discontinued",  "no-go"),
    "NOT AUTHORISED": ("discontinued", "no-go"),
}

# Regex patterns for mapping EMA therapeutic areas → our vocabulary
_IND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"cancer|oncol|neoplasm|carcinoma|leukemia|lymphoma|melanoma|sarcoma|"
                r"myeloma|tumor|tumour|glioma", re.I), "oncology"),
    (re.compile(r"rare|orphan|lysosomal|gaucher|fabry|pompe|niemann|hunter|"
                r"hemophilia|haemophilia|sickle.cell|muscular.dystrophy|"
                r"cystic.fibr|spinal.muscular|phenylketon|tay.sachs", re.I), "rare_disease"),
    (re.compile(r"autoimmun|rheumatoid|arthritis|lupus|crohn|colitis|psoriasis|"
                r"sclerosis|immunol|inflamm|ankylosing|sjogren|atopic|eczema|"
                r"uveitis|vasculitis", re.I), "immunology"),
    (re.compile(r"alzheimer|parkinson|dementia|neurodegen|ALS\b|amyotroph|"
                r"huntington|epilep|seizure|schizophrenia|depression|anxiety|"
                r"bipolar|neurolog|migraine|neuropathy|psychiatric", re.I), "neurology"),
    (re.compile(r"cardiovasc|heart.failure|atrial|coronary|hypertension|"
                r"cholesterol|atheroscler|myocardial|stroke|thrombosis|"
                r"anticoagul|angina|cardiac", re.I), "cardiovascular"),
    (re.compile(r"diabetes|obesity|metabolic|NASH\b|NAFLD|fatty.liver|"
                r"insulin|GLP.1|SGLT|lipid|triglycerid", re.I), "metabolic"),
    (re.compile(r"HIV|hepatitis|influenza|COVID|SARS|antibiotic|antibacter|"
                r"antiviral|antifungal|tuberculosis|malaria|infectious|"
                r"RSV\b|pneumonia|bacterial", re.I), "infectious"),
]

_DEFAULT_IND = "other"


def _map_indication(text: str) -> str:
    for pattern, ind in _IND_PATTERNS:
        if pattern.search(text or ""):
            return ind
    return _DEFAULT_IND


def _map_mechanism(active_substance: str) -> str:
    name = (active_substance or "").lower()
    if any(x in name for x in ("mab", "zumab", "ximab", "lumab", "numab", "vedotin")):
        return "antibody"
    if any(x in name for x in ("gene ", "crispr", "aav")):
        return "gene_therapy"
    if any(x in name for x in ("cell therapy", "car-t", "cart")):
        return "cell_therapy"
    if any(x in name for x in ("sirna", "mrna", "antisense", "oligonucle", "aptamer")):
        return "rna"
    if any(x in name for x in ("alfa", "beta", "gamma", "erythropoiet", "factor viii",
                                "insulin ", "glucagon", "interferon")):
        return "protein"
    return "small_molecule"


class EMACollector(BaseCollector):
    """
    Collects EU drug approval/refusal/withdrawal decisions from the EMA medicines XLSX.

    The XLSX is ~1-2 MB, updated nightly by EMA, and contains the complete history
    of centrally-authorised medicines in the EU — covering all therapeutic areas
    and all sponsor types (big pharma + biotech).
    """

    name = "ema_medicines"
    rate_limit_seconds = 0.0   # Single large file download

    def collect(self, max_records: int = 3000) -> list[RawRecord]:
        logger.info("[ema] Downloading EMA medicines register XLSX …")
        try:
            data = self._download_xlsx()
        except Exception as exc:
            logger.error("[ema] Failed to download XLSX: %s", exc)
            return []

        if not data:
            logger.warning("[ema] XLSX download returned no bytes")
            return []

        logger.info("[ema] Parsing XLSX (%d bytes) …", len(data))
        rows = self._parse_xlsx(data)
        logger.info("[ema] Parsed %d rows from EMA register", len(rows))

        records: list[RawRecord] = []
        seen: set[str] = set()

        for row in rows[:max_records]:
            rec = self._build_record(row)
            if rec and rec.source_id not in seen:
                seen.add(rec.source_id)
                records.append(rec)

        logger.info("[ema] Produced %d records", len(records))
        return records

    # ── Download ──────────────────────────────────────────────────────────────

    def _download_xlsx(self) -> bytes:
        resp = self.session.get(_EMA_XLSX, timeout=120, stream=True)
        resp.raise_for_status()
        chunks = []
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    # ── Parse Excel ───────────────────────────────────────────────────────────

    def _parse_xlsx(self, data: bytes) -> list[dict]:
        """Parse EMA XLSX into list of row dicts.  Tries openpyxl then xlrd."""
        import importlib
        try:
            import openpyxl
            return self._parse_openpyxl(data)
        except ImportError:
            pass
        try:
            import pandas as pd
            return self._parse_pandas(data)
        except Exception as exc:
            logger.error("[ema] Could not parse XLSX: %s", exc)
            return []

    def _parse_openpyxl(self, data: bytes) -> list[dict]:
        """
        EMA XLSX layout:
          Row 1:  metadata ("Content type: Medicine …")
          Rows 2-8: blank / metadata
          Row 9:  real column headers
          Row 10+: data rows
        We scan for the header row by looking for 'Medicine status' or 'Medicine status'
        among cell values, then read from the following row onward.
        """
        import openpyxl
        wb = openpyxl.load_workbook(filename=io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        wb.close()

        # Find header row (contains "Medicine status" or "Name of medicine")
        header_idx = None
        for idx, row in enumerate(all_rows):
            row_strs = [str(v).strip() for v in row if v is not None]
            if any("medicine status" in s.lower() or "name of medicine" in s.lower()
                   for s in row_strs):
                header_idx = idx
                break

        if header_idx is None:
            logger.warning("[ema] Could not locate header row in XLSX")
            return []

        raw_headers = all_rows[header_idx]
        headers = [str(h).strip() if h is not None else f"col{i}"
                   for i, h in enumerate(raw_headers)]

        result = []
        for row in all_rows[header_idx + 1 :]:
            if all(v is None for v in row):
                continue
            result.append({headers[i]: row[i] for i in range(min(len(headers), len(row)))})
        return result

    def _parse_pandas(self, data: bytes) -> list[dict]:
        import pandas as pd
        # EMA XLSX: skip metadata rows, real headers at row 9 (0-indexed: 8)
        df = pd.read_excel(io.BytesIO(data), engine="openpyxl", header=8)
        df.columns = [str(c).strip() for c in df.columns]
        return df.where(df.notna(), None).to_dict(orient="records")

    # ── Build record ──────────────────────────────────────────────────────────

    def _build_record(self, row: dict) -> RawRecord | None:
        # Actual EMA XLSX columns (as of 2025/2026):
        # "Category", "Name of medicine", "EMA product number",
        # "Medicine status", "Opinion status",
        # "Latest procedure affecting product information",
        # "International non-proprietary name (INN) / common name",
        # "Active substance", "Therapeutic area (MeSH)",
        # "Species\n(veterinary)"

        category   = _str(row, "Category", "category") or ""
        if "veterinary" in category.lower():
            return None   # skip animal medicines

        name        = _str(row, "Name of medicine", "Medicine name", "Name", "medicine_name")
        active      = _str(row, "Active substance", "International non-proprietary name (INN) / common name",
                           "INN / common name", "active_substance")
        product_no  = _str(row, "EMA product number", "Product number", "EMEA product number")
        status_raw  = _str(row, "Medicine status", "Status", "Authorisation status") or ""
        area        = _str(row, "Therapeutic area (MeSH)", "Therapeutic area",
                           "Condition / indication", "therapeutic_area")
        auth_date   = _str(row, "Marketing authorisation date", "Authorisation date",
                           "Latest procedure affecting product information")

        if not name and not active:
            return None

        status = status_raw.upper().strip()
        if not status:
            return None

        # Map status → outcome / decision
        outcome_tuple = None
        for key, val in _STATUS_MAP.items():
            if key in status:
                outcome_tuple = val
                break
        if not outcome_tuple:
            return None   # skip unknown status rows

        outcome, decision = outcome_tuple
        stage = "approved" if outcome == "approved" else "nda_submitted"

        indication = _map_indication(f"{area or ''} {name or ''} {active or ''}")
        mechanism  = _map_mechanism(active or name or "")
        investment = 350_000_000.0 if outcome == "approved" else 120_000_000.0
        year       = _extract_year(auth_date)

        product_label = (product_no or f"{(name or '')[:30]}").replace(" ", "_").replace("/", "-")
        source_id = f"ema_{product_label}"

        title    = f"{name or active} ({active or 'unknown'})"
        raw_text = (f"{name}. Active: {active}. Status: {status_raw}. "
                    f"Therapeutic area: {area}.")

        return RawRecord(
            source=self.name,
            source_id=source_id,
            url=f"https://www.ema.europa.eu/en/medicines/search?search_api_views_fulltext={product_no or ''}",
            title=title[:120],
            indication=indication,
            mechanism=mechanism,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=investment,
            raw_text=raw_text,
            extra={
                "medicine_name": name,
                "active_substance": active,
                "status": status_raw,
                "therapeutic_area": area,
                "authorisation_date": auth_date,
                "product_number": product_no,
                "year": year,
            },
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _str(row: dict, *keys: str) -> str | None:
    """Try multiple column name variants, return first match or None."""
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip() and str(val).strip().lower() != "nan":
            return str(val).strip()
    return None


def _extract_year(date_str: str | None) -> int:
    if not date_str:
        return 2000
    m = re.search(r"(19|20)\d{2}", str(date_str))
    return int(m.group()) if m else 2000
