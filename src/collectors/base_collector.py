from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "BioVentureResearch/1.0 (academic-poc; contact: research@example.com)",
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
}


@dataclass
class RawRecord:
    """Normalised envelope returned by every collector."""

    source: str             # e.g. "clinicaltrials", "sec_edgar", "atlas_venture"
    source_id: str          # unique within source
    url: str
    title: str
    indication: str
    mechanism: str
    clinical_stage: str     # "preclinical","phase1","phase2","phase3","approved","discontinued"
    decision: str           # "go","no-go","undecided","acquired"
    outcome: str            # "approved","discontinued_p1","discontinued_p2","discontinued_p3","ongoing","unknown"
    investment_usd: float   # 0 if unknown
    raw_text: str           # full extracted text for NLP
    extra: dict[str, Any] = field(default_factory=dict)


class BaseCollector(ABC):
    """Abstract base for all data collectors."""

    name: str = "base"
    rate_limit_seconds: float = 1.0

    def __init__(self, timeout: int = 20) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout = timeout
        self._last_request_time: float = 0.0

    def _get(self, url: str, params: dict | None = None, accept_json: bool = True) -> requests.Response:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        if accept_json:
            self.session.headers["Accept"] = "application/json"
        else:
            self.session.headers["Accept"] = "text/html,application/xhtml+xml"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("GET %s failed: %s", url, exc)
            raise
        finally:
            self._last_request_time = time.monotonic()
        return resp

    @abstractmethod
    def collect(self, max_records: int = 200) -> list[RawRecord]:
        """Collect records and return normalised list."""
