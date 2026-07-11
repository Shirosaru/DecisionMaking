from __future__ import annotations

"""
Slide / Press-Release Extractor — GlobeNewsWire backend.

Originally aimed at PDF slides from BioSpace/FierceBiotech (403/404).
Replaced with GlobeNewsWire biotechnology press releases:
  - Search results page uses static HTML (10 article links per query)
  - Individual article pages are fully static and have rich text
"""

import logging
import re
import urllib.parse
from bs4 import BeautifulSoup

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_GNW_BASE = "https://www.globenewswire.com"
_GNW_SEARCH = _GNW_BASE + "/en/search/keyword/{query}/industry/57"   # 57 = Biotechnology

# (search query text, hint_stage, hint_decision)
_SEARCH_QUERIES: list[tuple[str, str, str]] = [
    ("phase 2 discontinued clinical trial",    "phase2",      "no-go"),
    ("phase 3 terminated clinical",            "phase3",      "no-go"),
    ("phase 1 discontinued trial",             "phase1",      "no-go"),
    ("phase 2 met primary endpoint",           "phase2",      "go"),
    ("phase 3 NDA approved FDA biotech",       "phase3",      "go"),
    ("first-in-human phase 1 oncology",        "preclinical", "go"),
    ("biotech pipeline advance phase 2",       "phase2",      "go"),
    ("drugpatents-in-last-14days antibody patents", "unknown", "go"),
    ("biotech patented drug discovery",        "unknown",     "go"),
    ("antibody patents biotech company",       "unknown",     "go"),
    ("patent granted biotechnology therapeutics", "unknown",   "go"),
    ("novel patent pharmaceutical company",    "unknown",     "go"),
]

_STAGE_RE = re.compile(
    r"\bphase\s*([123i]+|one|two|three)\b|preclinical\b|pivotal\b|approved\b",
    re.IGNORECASE,
)
_STAGE_MAP = {
    "1": "phase1", "i": "phase1", "one": "phase1",
    "2": "phase2", "ii": "phase2", "two": "phase2",
    "3": "phase3", "iii": "phase3", "three": "phase3",
}
_DECISION_RE = re.compile(
    r"\b(discontinu|terminat|halted|failed|no.go|advance|initiat|enroll|positive|approved|NDA|BLA)\b",
    re.IGNORECASE,
)
_PATENT_RE = re.compile(
    r"\bpatents?\b|\buspto\b|\bintellectual property\b|\bwo\d{4}\b",
    re.IGNORECASE,
)
_INVEST_RE = re.compile(r"\$\s*(\d[\d,.]*)\s*(million|M|billion|B)\b", re.IGNORECASE)

_INDICATION_RE = re.compile(
    r"\b(cancer|carcinoma|oncology|leukemia|lymphoma|melanoma|"
    r"rare disease|autoimmune|rheumatoid|lupus|crohn|colitis|"
    r"neurology|alzheimer|parkinson|ALS|multiple sclerosis|"
    r"cardiovascular|heart failure|hypertension|"
    r"metabolic|diabetes|obesity|NASH|"
    r"infectious|HIV|hepatitis|COVID|"
    r"inflammation|psoriasis|atopic dermatitis|"
    r"hematology|sickle cell|hemophilia|gene therapy)\b",
    re.IGNORECASE,
)
_MECHANISM_RE = re.compile(
    r"\b(antibody|monoclonal|bispecific|ADC|"
    r"small molecule|inhibitor|kinase|checkpoint|PD.?1|PD.?L1|CTLA.?4|"
    r"cell therapy|CAR.?T|T.?cell|"
    r"gene therapy|CRISPR|AAV|"
    r"RNA|siRNA|mRNA|antisense|"
    r"enzyme|protein|peptide|vaccine)\b",
    re.IGNORECASE,
)

_COMPANY_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&'.,-]*(?:\s+[A-Z][A-Za-z0-9&'.,-]*){0,6}\s+"
    r"(?:Inc\.?|Incorporated|Ltd\.?|Limited|Corp\.?|Corporation|plc|AG|N\.V\.|NV|S\.A\.|SA|"
    r"Therapeutics|Pharmaceuticals|Pharma|Biosciences|Biotechnology|Biotech|Bio|Medical|Health))\b"
)

_LEADING_COMPANY_NOISE = {
    "A", "An", "And", "On", "For", "To", "Of", "The",
    "Now", "Brings", "Bring", "Positive", "Novel", "Announces", "Announce",
    "Agreement", "Acquire", "Acquires", "Acquisition", "Portfolio", "Company",
}

_COMPANY_BLACKLIST = {
    "skip topnav biotech",
    "skip topnav biotechnology",
    "nature biotechnology",
    "biotech",
}

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "ltd", "limited", "corp", "corporation",
    "plc", "ag", "nv", "sa",
    "therapeutics", "pharmaceuticals", "pharma", "biosciences",
    "biotechnology", "biotech", "bio", "medical", "health",
}

_EXCLUDE_TITLE_RE = re.compile(
    r"global markets report|market report published|research and markets|grand view research",
    re.IGNORECASE,
)


def _extract_stage(text: str, hint: str) -> str:
    m = _STAGE_RE.search(text)
    if not m:
        return hint
    raw = m.group(0).lower()
    if "preclinical" in raw:
        return "preclinical"
    if "pivotal" in raw or "approved" in raw:
        return "phase3"
    grp = m.group(1).lower() if m.group(1) else ""
    return _STAGE_MAP.get(grp, hint)


def _extract_decision(text: str, hint: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ("discontinu", "terminat", "fail", "halt", "no-go", "pulled")):
        return "no-go"
    if any(w in lower for w in ("advance", "initiat", "enrol", "positive", "approved", "nda", "bla")):
        return "go"
    return hint


def _extract_outcome(stage: str, decision: str) -> str:
    if decision == "go":
        return "ongoing" if stage != "phase3" else "approved"
    return f"discontinued_{stage}" if stage not in ("unknown", "") else "discontinued_p2"


def _extract_indication(text: str) -> str:
    m = _INDICATION_RE.search(text)
    return m.group(0).lower() if m else "unknown"


def _extract_mechanism(text: str) -> str:
    m = _MECHANISM_RE.search(text)
    return m.group(0).lower() if m else "unknown"


def _extract_investment(text: str) -> float:
    m = _INVEST_RE.search(text)
    if not m:
        return 0.0
    amount = float(m.group(1).replace(",", ""))
    mult = 1_000_000 if m.group(2).lower() in ("million", "m") else 1_000_000_000
    return amount * mult


def _extract_company(text: str, title: str) -> str:
    """Best-effort company extraction from title/body for downstream tracking."""
    for candidate in (title, text[:1200]):
        m = _COMPANY_RE.search(candidate)
        if m:
            tokens = m.group(1).replace(",", " ").split()
            while tokens and tokens[0].strip(". ") in _LEADING_COMPANY_NOISE:
                tokens = tokens[1:]
            cleaned = " ".join(tokens).strip(" ,. ")
            cleaned = re.sub(r"\s+", " ", cleaned)
            cleaned = re.sub(r"^Skip TopNav\s+", "", cleaned, flags=re.IGNORECASE)

            # Remove headline duplication like "Foo Bio Inc. Foo Bio Inc".
            dup = re.match(r"^(?P<x>.+?)\s+(?P=x)$", cleaned, flags=re.IGNORECASE)
            if dup:
                cleaned = dup.group("x").strip(" ,. ")

            words = cleaned.split()
            if len(words) >= 4 and len(words) % 2 == 0:
                mid = len(words) // 2
                left = [w.lower().strip(".,") for w in words[:mid]]
                right = [w.lower().strip(".,") for w in words[mid:]]
                if left == right:
                    cleaned = " ".join(words[:mid]).strip(" ,. ")
                    words = cleaned.split()

            # Keep only up to first legal/company suffix to avoid trailing noise.
            for i, w in enumerate(words):
                if w.lower().strip(".,") in _LEGAL_SUFFIXES:
                    cleaned = " ".join(words[: i + 1]).strip(" ,. ")
                    break

            if cleaned and cleaned.lower() not in _COMPANY_BLACKLIST:
                return cleaned
    return "unknown"


class SlideExtractor(BaseCollector):
    """
    Extracts bioventure decision records from GlobeNewsWire biotechnology
    press releases via keyword search → article fetch pipeline.
    """

    name = "slide_extractor"
    rate_limit_seconds = 1.0

    def collect(self, max_records: int = 100) -> list[RawRecord]:
        records: list[RawRecord] = []
        seen_urls: set[str] = set()
        per_query = max(max_records // len(_SEARCH_QUERIES), 3)

        for q_text, hint_stage, hint_decision in _SEARCH_QUERIES:
            if len(records) >= max_records:
                break
            batch = self._run_query(q_text, hint_stage, hint_decision, per_query)
            for rec in batch:
                if rec.url in seen_urls:
                    continue
                seen_urls.add(rec.url)
                records.append(rec)
            logger.info(
                "[GNW] query=%r stage=%s decision=%s -> %d records",
                q_text[:40], hint_stage, hint_decision, len(batch),
            )

        logger.info("SlideExtractor total: %d records", len(records))
        return records

    def _run_query(
        self, q_text: str, hint_stage: str, hint_decision: str, limit: int
    ) -> list[RawRecord]:
        enc = urllib.parse.quote(q_text)
        search_url = _GNW_SEARCH.format(query=enc)

        try:
            resp = self._get(search_url, accept_json=False)
        except Exception as exc:
            logger.warning("GNW search failed: %s", exc)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        links = [
            a["href"] for a in soup.find_all("a", href=True)
            if "/news-release/" in a.get("href", "")
        ]
        links = list(dict.fromkeys(links))[:limit * 2]   # deduplicate, budget extra for filtering

        records: list[RawRecord] = []
        for href in links:
            if len(records) >= limit:
                break
            record = self._fetch_article(href, hint_stage, hint_decision)
            if record:
                records.append(record)

        return records

    def _fetch_article(
        self, href: str, hint_stage: str, hint_decision: str
    ) -> RawRecord | None:
        url = href if href.startswith("http") else _GNW_BASE + href
        try:
            resp = self._get(url, accept_json=False)
        except Exception as exc:
            logger.debug("GNW article fetch failed %s: %s", url, exc)
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        # Get title
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else href.rstrip("/").split("/")[-1][:100]
        if _EXCLUDE_TITLE_RE.search(title):
            return None

        # Get article body text
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)[:5000]

        has_decision_signal = bool(_DECISION_RE.search(text))
        has_patent_signal = bool(_PATENT_RE.search(text))
        if not (has_decision_signal or has_patent_signal):
            return None   # not a relevant press release

        stage     = _extract_stage(text, hint_stage)
        decision  = _extract_decision(text, hint_decision)
        outcome   = _extract_outcome(stage, decision)
        indication= _extract_indication(text)
        mechanism = _extract_mechanism(text)
        investment= _extract_investment(text)
        company   = _extract_company(text, title)
        source_id = re.sub(r"\W+", "_", href.strip("/").split("/")[-1])[:80]

        return RawRecord(
            source=self.name,
            source_id=source_id,
            url=url,
            title=title[:200],
            indication=indication,
            mechanism=mechanism,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=investment,
            raw_text=text[:3000],
            extra={
                "gnw_href": href,
                "company_guess": company,
                "patent_signal": has_patent_signal,
            },
        )
