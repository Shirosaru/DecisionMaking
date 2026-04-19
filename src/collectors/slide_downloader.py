from __future__ import annotations

"""
EDGAR Investor Presentation Slide Downloader.

Searches SEC EDGAR for 8-K filings that are investor presentations / pipeline
updates, downloads the EX-99.1 exhibit files (HTM or PDF), stores them in
data/slides/edgar/, and extracts structured clinical decision signals.

Pipeline:
  EDGAR EFTS search
    → filing index HTML  (list of exhibits)
      → EX-99.1 HTM/PDF download  (saved to data/slides/edgar/)
        → text extraction + clinical NLP
          → RawRecord
"""

import hashlib
import io
import logging
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_EDGAR_SEARCH  = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
_SEC_ROOT      = "https://www.sec.gov"

# Exhibit types we want to download (investor presentations, press releases)
_EXHIBIT_TYPES = {"EX-99.1", "EX-99.2", "EX-99.3"}

# File types we can actually read
_READABLE_TYPES = {".htm", ".html", ".pdf"}

# EDGAR search queries targeting investor presentations with clinical content.
# Note: EDGAR EFTS returns 500 on OR-syntax; use simple quoted phrases only.
_QUERIES: list[tuple[str, str, str]] = [
    ('"investor presentation" "pipeline" "phase"',        "unknown", "undecided"),
    ('"clinical data" "phase 2" "discontinu" "pipeline"', "phase2",  "no-go"),
    ('"clinical data" "phase 3" "terminat" "pipeline"',   "phase3",  "no-go"),
    ('"pipeline update" "phase 2" "advance"',             "phase2",  "go"),
    ('"FDA approval" "NDA" "pipeline" "phase 3"',         "phase3",  "go"),
    ('"first-in-human" "phase 1" "oncology" "pipeline"',  "phase1",  "go"),
    ('"go/no-go" "clinical" "pipeline"',                  "unknown", "undecided"),
    ('"ASCO" "pipeline" "phase" "clinical"',              "unknown", "undecided"),
    ('"pipeline milestones" "phase"',                      "unknown", "undecided"),
    ('"IND clearance" "phase 1"',                         "preclinical", "go"),
]

_PHARMA_SICS = {"2830","2831","2833","2834","2835","2836","8731","8099","5122"}

_STAGE_RE = re.compile(
    r"\bphase\s*([123i]+|one|two|three)\b|preclinical\b|pivotal\b",
    re.IGNORECASE,
)
_STAGE_MAP = {
    "1":"phase1","i":"phase1","one":"phase1",
    "2":"phase2","ii":"phase2","two":"phase2",
    "3":"phase3","iii":"phase3","three":"phase3",
}

_DECISION_CLUE = re.compile(
    r"\b(discontinu|terminat|halted|pulled|failed|go/no.go|advance|initiat|"
    r"enroll|positive data|approved|NDA|BLA|milestone|halt)\b",
    re.IGNORECASE,
)

_INDICATION_RE = re.compile(
    r"\b(cancer|carcinoma|oncology|leukemia|lymphoma|melanoma|glioblastoma|"
    r"rare disease|autoimmune|rheumatoid|lupus|crohn|colitis|"
    r"neurology|alzheimer|parkinson|ALS|multiple sclerosis|"
    r"cardiovascular|heart failure|atherosclerosis|hypertension|"
    r"metabolic|diabetes|obesity|NASH|NAFLD|"
    r"infectious|HIV|hepatitis|influenza|COVID|SARS|"
    r"inflammation|psoriasis|atopic dermatitis|"
    r"hematology|sickle cell|hemophilia|gene therapy|CNS|psychiatric)\b",
    re.IGNORECASE,
)
_MECHANISM_RE = re.compile(
    r"\b(antibody|monoclonal|bispecific|ADC|antibody.drug conjugate|"
    r"small molecule|inhibitor|kinase|checkpoint|PD.?1|PD.?L1|CTLA.?4|"
    r"cell therapy|CAR.?T|T.?cell|NK cell|"
    r"gene therapy|CRISPR|AAV|lentiviral|"
    r"RNA|siRNA|mRNA|antisense|oligonucleotide|"
    r"enzyme|protein|peptide|vaccine|psychedelic|psilocybin|MDMA|ketamine)\b",
    re.IGNORECASE,
)
_INVEST_RE = re.compile(r"\$\s*(\d[\d,.]*)\s*(million|M|billion|B)\b", re.IGNORECASE)


def _extract_stage(text: str, hint: str) -> str:
    m = _STAGE_RE.search(text)
    if not m:
        return hint
    raw = m.group(0).lower()
    if "preclinical" in raw:
        return "preclinical"
    if "pivotal" in raw:
        return "phase3"
    grp = m.group(1).lower() if m.group(1) else ""
    return _STAGE_MAP.get(grp, hint)


def _extract_decision(text: str, hint: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ("discontinu","terminat","fail","halt","no-go","pulled")):
        return "no-go"
    if any(w in lower for w in ("advance","initiat","enrol","positive","approved","nda","bla")):
        return "go"
    return hint


def _extract_outcome(stage: str, decision: str) -> str:
    if decision == "go":
        return "ongoing" if stage != "phase3" else "approved"
    if decision == "no-go":
        return f"discontinued_{stage}" if stage not in ("unknown","") else "discontinued_p2"
    return "ongoing"


def _extract_indication(text: str) -> str:
    m = _INDICATION_RE.search(text)
    return m.group(0).lower() if m else "unknown"


def _extract_mechanism(text: str) -> str:
    m = _MECHANISM_RE.search(text)
    return m.group(0).lower() if m else "unknown"


def _extract_investment(text: str) -> float:
    # Find largest dollar amount mentioned (likely the program investment)
    best = 0.0
    for m in _INVEST_RE.finditer(text):
        amt = float(m.group(1).replace(",", ""))
        mult = 1_000_000 if m.group(2).lower() in ("million","m") else 1_000_000_000
        val = amt * mult
        if val > best:
            best = val
    return best


def _htm_to_text(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script","style","nav","header","footer"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _pdf_to_text(raw: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages[:30]]
        return "\n".join(pages)
    except Exception as exc:
        logger.debug("pdfplumber failed: %s", exc)
        return ""


class SlideDownloader(BaseCollector):
    """
    Downloads and parses actual investor presentation slides/exhibits filed
    with SEC EDGAR as 8-K attachments (EX-99.1/2/3).

    Files are saved locally under data/slides/edgar/ using a hash-based name.
    On subsequent runs, already-downloaded files are read from cache.
    """

    name = "edgar_slides"
    rate_limit_seconds = 0.4   # SEC asks for <10 req/s

    def __init__(
        self,
        slides_dir: Path = Path("data/slides/edgar"),
        timeout: int = 30,
    ) -> None:
        super().__init__(timeout=timeout)
        self.slides_dir = Path(slides_dir)
        self.slides_dir.mkdir(parents=True, exist_ok=True)

    # ── public entry point ──────────────────────────────────────────────────

    def collect(self, max_records: int = 100) -> list[RawRecord]:
        records: list[RawRecord] = []
        per_query = max(max_records // len(_QUERIES), 3)

        for query, hint_stage, hint_decision in _QUERIES:
            if len(records) >= max_records:
                break
            batch = self._run_query(query, hint_stage, hint_decision, per_query)
            records.extend(batch)
            logger.info(
                "[EDGAR slides] q=%r stage=%s → %d records",
                query[:45], hint_stage, len(batch),
            )

        logger.info("SlideDownloader total: %d records", len(records))
        return records

    # ── internal: search → filing index → exhibit download ──────────────────

    def _run_query(
        self, query: str, hint_stage: str, hint_decision: str, limit: int
    ) -> list[RawRecord]:
        try:
            resp = self._get(
                _EDGAR_SEARCH,
                params={
                    "q": query,
                    "dateRange": "custom",
                    "startdt": "2020-01-01",
                    "enddt": "2025-12-31",
                    "forms": "8-K",
                },
                accept_json=True,
            )
            hits = resp.json().get("hits", {}).get("hits", [])
        except Exception as exc:
            logger.warning("EDGAR search failed: %s", exc)
            return []

        records: list[RawRecord] = []
        for hit in hits:
            if len(records) >= limit:
                break
            src = hit.get("_source", {})
            sics = src.get("sics", [])
            if sics and not any(s in _PHARMA_SICS for s in sics):
                continue

            adsh = src.get("adsh", "")
            ciks = src.get("ciks", [])
            if not adsh or not ciks:
                continue
            cik = ciks[0].lstrip("0")

            record = self._process_filing(adsh, cik, src, hint_stage, hint_decision)
            if record:
                records.append(record)

        return records

    def _process_filing(
        self,
        adsh: str,
        cik: str,
        src: dict[str, Any],
        hint_stage: str,
        hint_decision: str,
    ) -> RawRecord | None:
        """Find EX-99.x exhibit in filing, download it, extract text."""
        adsh_clean = adsh.replace("-", "")
        index_url = f"{_SEC_ROOT}/Archives/edgar/data/{cik}/{adsh_clean}/{adsh}-index.html"

        try:
            resp = self._get(index_url, accept_json=False)
        except Exception:
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        exhibit_url: str | None = None
        exhibit_filename: str | None = None

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            doc_type = cells[3].get_text(strip=True)
            if doc_type not in _EXHIBIT_TYPES:
                continue
            link = cells[2].find("a")
            if not link:
                continue
            href: str = link.get("href", "")
            # Skip XBRL, zip, XML
            ext = Path(href.split("?")[0]).suffix.lower()
            if ext not in _READABLE_TYPES:
                continue
            exhibit_url = _SEC_ROOT + href if href.startswith("/") else href
            exhibit_filename = href.split("/")[-1].split("?")[0]
            break

        if not exhibit_url or not exhibit_filename:
            return None

        # Download (or load from cache)
        text = self._fetch_exhibit(exhibit_url, exhibit_filename, adsh)
        if not text or len(text) < 200:
            return None

        if not _DECISION_CLUE.search(text[:8000]):
            return None

        # Extract signals
        text_slice = text[:10000]
        stage     = _extract_stage(text_slice, hint_stage)
        decision  = _extract_decision(text_slice, hint_decision)
        outcome   = _extract_outcome(stage, decision)
        indication= _extract_indication(text_slice)
        mechanism = _extract_mechanism(text_slice)
        investment= _extract_investment(text_slice)

        names = src.get("display_names", [])
        entity = names[0].split("(")[0].strip() if names else "unknown"
        form   = src.get("form", "8-K")
        title  = f"{entity} — {form} Investor Presentation ({adsh})"

        return RawRecord(
            source=self.name,
            source_id=adsh,
            url=exhibit_url,
            title=title[:200],
            indication=indication,
            mechanism=mechanism,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=investment,
            raw_text=text[:4000],
            extra={
                "entity": entity,
                "adsh": adsh,
                "exhibit_file": exhibit_filename,
                "local_path": str(self.slides_dir / f"{adsh.replace('-','_')}_{exhibit_filename}"),
            },
        )

    def _fetch_exhibit(self, url: str, filename: str, adsh: str) -> str:
        """Download exhibit, save to slides_dir, and return extracted text."""
        safe_adsh = adsh.replace("-", "_")
        local_path = self.slides_dir / f"{safe_adsh}_{filename}"

        # Use cached file if already downloaded
        if local_path.exists() and local_path.stat().st_size > 100:
            logger.debug("Cache hit: %s", local_path.name)
            raw = local_path.read_bytes()
        else:
            try:
                resp = self._get(url, accept_json=False)
                raw = resp.content
                local_path.write_bytes(raw)
                logger.debug("Downloaded: %s (%d bytes)", local_path.name, len(raw))
            except Exception as exc:
                logger.debug("Exhibit download failed %s: %s", url, exc)
                return ""

        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            return _pdf_to_text(raw)
        return _htm_to_text(raw)
