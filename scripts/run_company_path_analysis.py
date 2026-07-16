#!/usr/bin/env python3
"""
Company discovery-path analyzer.

Given one or more company names, searches the bioventure history in
data/bioventure.json, summarizes the path they pursued, and produces a
simple GO / NO-GO decision based on the historical record.

Usage:
  python3 scripts/run_company_path_analysis.py Company A Company B
  python3 scripts/run_company_path_analysis.py --latest-patents
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.learning.decision_model import SuccessPredictor
from src.storage.repository import fetch_all

DB_PATH = _ROOT / "data" / "bioventure.json"
REPORT_PATH = _ROOT / "data" / "reports" / "company_path_report.txt"

_STAGE_ORDER = {
    "preclinical": 0,
    "ind_filing": 1,
    "phase1": 2,
    "phase2": 3,
    "phase3": 4,
    "nda_submitted": 5,
    "approved": 6,
    "unknown": -1,
}

_LEGAL_SUFFIXES = {
    "inc", "ltd", "limited", "corp", "corporation", "plc", "ag",
    "nv", "sa", "therapeutics", "pharmaceuticals", "pharma",
    "biosciences", "biotechnology", "biotech", "bio", "medical", "health",
}

_STOPWORDS = {"the", "and", "of", "for", "to", "in", "on", "a", "an"}
_GENERIC_COMPANY_WORDS = {
    "therapeutics", "pharmaceuticals", "pharma", "biopharma", "biotech",
    "biotechnology", "bio", "medicine", "medical", "health",
    "cancer", "oncology", "disease", "discovery", "global", "markets",
    "report", "reports", "published", "company", "portfolio",
}

_REPORT_TITLE_RE = re.compile(
    r"global markets report|market size|market report published|segments the market|"
    r"research and markets|grand view research|market outlook",
    re.IGNORECASE,
)


def canonical_company_name(name: str) -> str:
    text = re.sub(r"\s+", " ", (name or "").strip())
    text = re.sub(r"^Skip TopNav\s+", "", text, flags=re.IGNORECASE)

    # Remove obvious headline duplication like "Foo Bio Inc. Foo Bio Inc."
    dup = re.match(r"^(?P<x>.+?)\s+(?P=x)$", text, flags=re.IGNORECASE)
    if dup:
        text = dup.group("x").strip()

    words = text.split()
    for idx, word in enumerate(words):
        if word.lower().strip(".,") in _LEGAL_SUFFIXES:
            text = " ".join(words[: idx + 1])
            break

    return re.sub(r"\s+", " ", text).strip(" ,.;")


def company_tokens(name: str) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9&'-]+", name.lower()):
        if len(token) <= 2 or token in _STOPWORDS or token in _GENERIC_COMPANY_WORDS:
            continue
        tokens.append(token)
    return tokens


def row_blob(row: dict[str, Any]) -> str:
    extra = row.get("extra") or {}
    parts = [
        row.get("source", ""),
        row.get("title", ""),
        row.get("indication", ""),
        row.get("mechanism", ""),
        row.get("raw_text", ""),
        row.get("url", ""),
        extra.get("company_guess", ""),
        extra.get("company", ""),
        extra.get("company_name", ""),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def row_date_key(row: dict[str, Any]) -> str:
    extra = row.get("extra") or {}
    for key in ("pubdate", "decision_year", "date", "year"):
        val = extra.get(key)
        if val:
            return str(val)

    url = row.get("url", "")
    m = re.search(r"/news-release/(\d{4})/(\d{2})/(\d{2})/", url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return f"{row.get('id', 0):010d}"


def find_company_records(rows: list[dict[str, Any]], company: str) -> list[dict[str, Any]]:
    query = canonical_company_name(company)
    q_blob = query.lower()
    q_tokens = company_tokens(query)

    matches: list[dict[str, Any]] = []
    for row in rows:
        blob = row_blob(row)
        if q_blob and q_blob in blob:
            matches.append(row)
            continue

        if q_tokens and all(tok in blob for tok in q_tokens):
            matches.append(row)
            continue

        extra = row.get("extra") or {}
        guess = canonical_company_name(str(extra.get("company_guess", "")))
        if guess and guess.lower() == q_blob:
            matches.append(row)
            continue

    matches.sort(key=lambda r: row_date_key(r))
    return matches


def dominant(items: list[str], fallback: str = "unknown") -> str:
    if not items:
        return fallback
    return Counter(items).most_common(1)[0][0]


def summarize_path(
    company: str,
    records: list[dict[str, Any]],
    model: SuccessPredictor,
) -> dict[str, Any]:
    stages = Counter(r.get("clinical_stage", "unknown") for r in records)
    decisions = Counter(r.get("decision", "undecided") for r in records)
    outcomes = Counter(r.get("outcome", "unknown") for r in records)
    sources = Counter(r.get("source", "unknown") for r in records)
    indications = Counter((r.get("indication") or "unknown") for r in records)
    mechanisms = Counter((r.get("mechanism") or "unknown") for r in records)
    patent_hits = sum(1 for r in records if (r.get("extra") or {}).get("patent_signal"))

    path_parts: list[str] = []
    if patent_hits:
        path_parts.append("IP/patent-led")
    if any(stages.get(s, 0) for s in ("phase1", "phase2", "phase3", "nda_submitted", "approved")):
        path_parts.append("clinical-development")
    if stages.get("preclinical", 0) and not any(stages.get(s, 0) for s in ("phase1", "phase2", "phase3", "nda_submitted", "approved")):
        path_parts.append("preclinical-discovery")
    if decisions.get("acquired", 0):
        path_parts.append("partnering/M&A")

    latest = records[-1] if records else {}
    latest_stage = latest.get("clinical_stage", "unknown")
    latest_indication = dominant(list(indications.elements()))
    latest_mechanism = dominant(list(mechanisms.elements()))

    history_text = (
        f"Historical record for {company}: "
        f"sources={dict(sources)}, stages={dict(stages)}, decisions={dict(decisions)}, "
        f"outcomes={dict(outcomes)}, indications={dict(indications)}, mechanisms={dict(mechanisms)}."
    )

    model_row = {
        "title": f"{company} discovery path summary",
        "indication": latest_indication,
        "mechanism": latest_mechanism,
        "clinical_stage": latest_stage,
        "raw_text": history_text,
    }
    model_result = model.explain(model_row)

    # Simpler history-based decision that mirrors the user's request.
    score = 0
    if decisions.get("acquired", 0):
        score += 1
    if outcomes.get("approved", 0) or outcomes.get("ongoing", 0):
        score += 3
    if stages.get("nda_submitted", 0) or stages.get("phase3", 0):
        score += 2
    if stages.get("phase2", 0):
        score += 1
    if stages.get("preclinical", 0) and not any(stages.get(s, 0) for s in ("phase1", "phase2", "phase3", "nda_submitted", "approved")):
        score -= 1
    if decisions.get("no-go", 0) > decisions.get("go", 0):
        score -= 2
    if patent_hits and not any(stages.get(s, 0) for s in ("phase1", "phase2", "phase3", "nda_submitted", "approved")):
        score -= 1

    verdict = "GO" if score >= 1 else "NO-GO"
    if model_result["verdict"] != verdict:
        # Prefer the model if it sees a stronger signal.
        verdict = model_result["verdict"] if abs(model_result["p_success"] - 0.5) >= 0.1 else verdict

    return {
        "company": company,
        "company_clean": canonical_company_name(company),
        "n_records": len(records),
        "path": ", ".join(path_parts) if path_parts else "unclear / mixed",
        "summary": history_text,
        "verdict": verdict,
        "p_success": model_result["p_success"],
        "model_verdict": model_result["verdict"],
        "model_summary": model_result["summary"],
        "latest_stage": latest_stage,
        "top_indication": latest_indication,
        "top_mechanism": latest_mechanism,
        "records": records,
        "model_result": model_result,
    }


def load_latest_patent_companies(rows: list[dict[str, Any]], limit: int = 10) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in reversed(rows):
        if row.get("source") != "slide_extractor":
            continue
        extra = row.get("extra") or {}
        if not extra.get("patent_signal"):
            continue
        if _REPORT_TITLE_RE.search(str(row.get("title", ""))):
            continue
        company = canonical_company_name(str(extra.get("company_guess", "")))
        if not company:
            continue
        if not company_tokens(company):
            continue
        key = company.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(company)
        if len(names) >= limit:
            break
    return names


def render_record_line(row: dict[str, Any]) -> str:
    date = row_date_key(row)
    title = row.get("title", "")
    source = row.get("source", "")
    stage = row.get("clinical_stage", "unknown")
    decision = row.get("decision", "undecided")
    return f"- {date} | {source} | {stage} | {decision} | {title}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze company discovery paths from the bioventure history.")
    parser.add_argument("companies", nargs="*", help="Company names to analyze")
    parser.add_argument("--latest-patents", action="store_true", help="Use the latest patent-related companies from bioventure")
    parser.add_argument("--limit", type=int, default=10, help="Limit when using --latest-patents")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Path to bioventure database JSON")
    parser.add_argument("--report", type=Path, default=REPORT_PATH, help="Write a text report to this path")
    args = parser.parse_args()

    rows = fetch_all(db_path=args.db_path)
    companies = list(args.companies)
    if args.latest_patents or not companies:
        companies = load_latest_patent_companies(rows, limit=args.limit)

    if not companies:
        print("No companies provided and no latest patent companies found.")
        return 1

    report_lines: list[str] = []
    report_lines.append("BIOVENTURE COMPANY PATH ANALYSIS")
    report_lines.append("=" * 40)
    report_lines.append("")

    print("Loading historical model...", flush=True)
    model = SuccessPredictor()
    model.train(db_path=args.db_path)

    for company in companies:
        records = find_company_records(rows, company)
        if not records:
            print(f"{company}: no matching records found")
            report_lines.append(f"{company}: no matching records found")
            report_lines.append("")
            continue

        analysis = summarize_path(company, records, model)
        print(f"\n{analysis['company_clean']}")
        print(f"Path: {analysis['path']}")
        print(f"GO/NO-GO: {analysis['verdict']}  (model P(success)={analysis['p_success']:.1%}, model={analysis['model_verdict']})")
        print(f"Records matched: {analysis['n_records']}")
        print(f"Top indication: {analysis['top_indication']}")
        print(f"Top mechanism: {analysis['top_mechanism']}")
        print("Recent history:")
        for row in records[-5:]:
            print(f"  {render_record_line(row)}")

        report_lines.append(f"Company: {analysis['company_clean']}")
        report_lines.append(f"Path: {analysis['path']}")
        report_lines.append(f"GO/NO-GO: {analysis['verdict']}  (model P(success)={analysis['p_success']:.1%}, model={analysis['model_verdict']})")
        report_lines.append(f"Records matched: {analysis['n_records']}")
        report_lines.append(f"Top indication: {analysis['top_indication']}")
        report_lines.append(f"Top mechanism: {analysis['top_mechanism']}")
        report_lines.append("Recent history:")
        for row in records[-5:]:
            report_lines.append(f"  {render_record_line(row)}")
        report_lines.append("")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report_lines) + "\n")
    print(f"\nReport written → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())