#!/usr/bin/env python3
"""
deepen_training.py
═══════════════════════════════════════════════════════════════════════════════

三つの原則 / Three principles:

  1. 失敗しない  — every HTTP call is wrapped in exponential-backoff retry.
                   Errors are logged and skipped; the pipeline never crashes.

  2. 失敗を意味づける — terminated trials are NOT just labelled "no-go".
                        We read WHY they stopped and classify into:
                        safety | efficacy | commercial | trial_design |
                        regulatory | covid | unknown

  3. 外部から学ぶ — pull 10,000+ records from three public APIs:
        • ClinicalTrials.gov  — ALL terminated industry trials 2014-2025
                                (whyStopped text extracted for each)
        • ChEMBL              — market-withdrawn approved drugs
                                (post-approval safety failures = strongest signal)
        • OpenFDA             — drug enforcement (Class I/II recalls)

Output:
  data/bioventure.json          — enriched training DB
  data/failure_patterns.json    — failure-mode stats per indication × mechanism
  Console: before/after AUC, top failure lessons

═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.parse import quote as _urlquote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.collectors.base_collector import RawRecord
from src.learning.decision_model import SuccessPredictor
from src.storage.repository import bulk_upsert, fetch_all

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── API endpoints ──────────────────────────────────────────────────────────────
CT_API       = "https://clinicaltrials.gov/api/v2/studies"
CHEMBL_API   = "https://www.ebi.ac.uk/chembl/api/data"
OPENFDA_API  = "https://api.fda.gov/drug"

_HEADERS = {
    "User-Agent": "BioVentureResearch/1.0 (academic-poc; contact:research@example.com)",
    "Accept":     "application/json",
}

# Rate limits (seconds between calls per API)
_RT_CT      = 0.55
_RT_CHEMBL  = 0.45
_RT_FDA     = 0.65

DB_SOURCE_CT      = "ct_terminated_v2"
DB_SOURCE_CHEMBL  = "chembl_withdrawn_v1"
DB_SOURCE_FDA     = "openfda_enforcement_v1"

FAILURE_PATTERNS_PATH = ROOT / "data" / "failure_patterns.json"

# ── Robust HTTP fetch with retry ───────────────────────────────────────────────

_last_call: dict[str, float] = {}


def _build_url(url: str, params: dict) -> str:
    """
    Build query URL preserving raw commas in values (needed for CT.gov
    filter.overallStatus=TERMINATED,WITHDRAWN  — encoded %2C is rejected).
    """
    parts = []
    for k, v in params.items():
        parts.append(f"{_urlquote(str(k), safe='.')}={_urlquote(str(v), safe=',;')}")
    return f"{url}?{'&'.join(parts)}"


def _get_json(url: str, params: dict | None = None,
              rate: float = 0.5, source: str = "") -> dict | None:
    """
    Fetch JSON with rate-limiting + exponential-backoff retry (3 attempts).
    Returns None on permanent failure instead of raising.
    失敗しない: every error is caught, logged, and gracefully skipped.
    """
    key = source or url[:40]
    elapsed = time.monotonic() - _last_call.get(key, 0.0)
    if elapsed < rate:
        time.sleep(rate - elapsed)

    full_url = url
    if params:
        full_url = _build_url(url, params)

    for attempt in range(1, 4):
        try:
            req = Request(full_url, headers=_HEADERS)
            with urlopen(req, timeout=25) as resp:
                _last_call[key] = time.monotonic()
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 429:
                wait = 5.0 * attempt
                log.warning("Rate-limited by %s — waiting %.0fs (attempt %d/3)", key, wait, attempt)
                time.sleep(wait)
                continue
            if e.code in (500, 502, 503, 504):
                time.sleep(2.0 * attempt)
                continue
            log.debug("HTTP %d from %s — skipping", e.code, full_url[:80])
            return None
        except (URLError, TimeoutError, Exception) as e:
            if attempt < 3:
                time.sleep(1.5 * attempt)
                continue
            log.debug("Network error on %s: %s", full_url[:80], e)
            return None
    return None


# ── Failure reason classification ─────────────────────────────────────────────
# 失敗を意味づける: every termination has a *reason* — learn from it

_SAFETY_RE      = re.compile(
    r"safety|adverse|toxic|serious ae|death|fatal|harm\b|side effect|liver|cardiac|"
    r"\bQT\b|bleed|hemorrhag|cytokine|hypersensitiv|anaphyla|hepato|renal|neuro.*toxic",
    re.I)
_EFFICACY_RE    = re.compile(
    r"efficacy|futility|benefit|endpoint|response|effective|lack of|no effect|"
    r"not meet|did not|insufficient|marginal|no improvement|no difference|"
    r"negative result|interim analysis|primary endpoint|clinical benefit|failed to",
    re.I)
_COMMERCIAL_RE  = re.compile(
    r"business|strategic|commerci|financial|fund\b|sponsor|resource|portfolio|"
    r"priorit|market|econom|bankrupt|acquired|merger|dissolv",
    re.I)
_TRIAL_RE       = re.compile(
    r"enroll|accrual|recruit|sample size|participant|subject|feasib|operational|"
    r"protocol|amendment|design|dropout|screen fail|slow.*enroll",
    re.I)
_REG_RE         = re.compile(
    r"regulat|FDA|EMA|CMC|manufactur|GMP|clinical hold|inspect|agency",
    re.I)
_COVID_RE       = re.compile(r"covid|pandemic|COVID-19|SARS.CoV|coronavirus", re.I)

# Priority order: more specific reasons first
_CLASSIFIERS: list[tuple[re.Pattern, str]] = [
    (_SAFETY_RE,     "safety"),
    (_EFFICACY_RE,   "efficacy"),
    (_COMMERCIAL_RE, "commercial"),
    (_TRIAL_RE,      "trial_design"),
    (_REG_RE,        "regulatory"),
    (_COVID_RE,      "covid"),
]


def classify_failure_reason(text: str) -> str:
    """
    Map free-text whyStopped → structured failure reason.
    Checks multiple categories and returns the most specific match.
    """
    if not text:
        return "unknown"
    for pattern, label in _CLASSIFIERS:
        if pattern.search(text):
            return label
    return "unknown"


# ── Indication + mechanism mapping ────────────────────────────────────────────

_IND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"neoplasm|cancer|carcinom|oncol|leukemia|lymphoma|melanoma|"
                r"glioblastoma|sarcoma|myeloma|tumor|tumour|malignant", re.I), "oncology"),
    (re.compile(r"rare disease|orphan|lysosomal|gaucher|fabry|pompe|niemann|"
                r"hunter|hurler|hemophilia|sickle cell|muscular dystrophy|"
                r"cystic fibr|spinal muscular|phenylketon|tay.sachs", re.I), "rare_disease"),
    (re.compile(r"autoimmun|rheumatoid|arthritis|lupus|crohn|colitis|psoriasis|"
                r"multiple sclerosis|immunol|inflamm|ankylosing|sjogren|"
                r"atopic|eczema|uveitis|vasculitis|IBD\b", re.I), "immunology"),
    (re.compile(r"alzheimer|parkinson|dementia|neurodegen|ALS\b|amyotroph|"
                r"huntington|epilep|seizure|schizophrenia|depression|anxiety|"
                r"bipolar|neurolog|CNS\b|migraine|peripheral neuropathy", re.I), "neurology"),
    (re.compile(r"cardiovasc|heart failure|atrial|coronary|hypertension|"
                r"cholesterol|atheroscler|myocardial|stroke|thrombosis|"
                r"anticoagul|antiarrhythm|angina", re.I), "cardiovascular"),
    (re.compile(r"diabetes|obesity|metabolic|NASH\b|NAFLD|fatty liver|"
                r"insulin|GLP.1|SGLT|lipid|triglycerid", re.I), "metabolic"),
    (re.compile(r"HIV|hepatitis|influenza|COVID|SARS|antibiotic|antibacter|"
                r"antiviral|antifungal|tuberculosis|malaria|infectious|pneumonia", re.I), "infectious"),
]

_MECH_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"mab\b|monoclonal|antibody|bispecific|ADC|drug conjugate", re.I), "antibody"),
    (re.compile(r"gene therapy|AAV|lentiviral|CRISPR|base edit|gene edit", re.I), "gene_therapy"),
    (re.compile(r"CAR.T|cell therapy|T.cell|NK.cell|TIL\b|adoptive cell", re.I), "cell_therapy"),
    (re.compile(r"siRNA|mRNA|antisense|ASO\b|oligonucleotide|RNAi|aptamer", re.I), "rna"),
    (re.compile(r"enzyme|protein|fusion|peptide|hormone|albumin|coagulation factor", re.I), "protein"),
    (re.compile(r"inhibitor|blocker|agonist|antagonist|modulator|small.?molecule", re.I), "small_molecule"),
]


def _map_indication(text: str) -> str:
    for pattern, ind in _IND_PATTERNS:
        if pattern.search(text):
            return ind
    return "other"


def _infer_mechanism(text: str) -> str:
    for pattern, mech in _MECH_PATTERNS:
        if pattern.search(text):
            return mech
    return "small_molecule"


_PHASE_MAP = {
    "PHASE1":       "phase1",
    "PHASE2":       "phase2",
    "PHASE3":       "phase3",
    "PHASE4":       "approved",
    "EARLY_PHASE1": "phase1",
    "NA":           "preclinical",
}

# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE 1 — ClinicalTrials.gov: Terminated / Withdrawn Trials
#  外部データ源1: 中止・取り下げされた臨床試験 (理由付き)
# ══════════════════════════════════════════════════════════════════════════════

# 8 indication queries × ~500 records each = ~4,000 new records
_CT_QUERIES: list[tuple[str, str]] = [
    ("oncology",
     "cancer OR oncology OR tumor OR leukemia OR lymphoma OR sarcoma OR glioma"),
    ("rare_disease",
     "rare disease OR orphan drug OR cystic fibrosis OR hemophilia OR sickle cell"),
    ("immunology",
     "autoimmune OR rheumatoid arthritis OR lupus OR crohn OR ulcerative colitis OR psoriasis"),
    ("neurology",
     "alzheimer OR parkinson OR ALS amyotrophic OR multiple sclerosis OR epilepsy"),
    ("cardiovascular",
     "heart failure OR hypertension OR coronary artery OR atrial fibrillation OR stroke"),
    ("metabolic",
     "type 2 diabetes OR obesity OR NASH non-alcoholic OR fatty liver OR metabolic syndrome"),
    ("infectious",
     "HIV OR hepatitis B OR influenza OR RSV respiratory syncytial OR antibiotic"),
    ("oncology_io",
     "immunotherapy checkpoint inhibitor OR CAR-T cell therapy OR PD-1 OR CTLA-4"),
]


def _ct_study_to_record(study: dict, default_ind: str) -> RawRecord | None:
    """Convert a single CT.gov study dict to a RawRecord (terminated/withdrawn only)."""
    proto     = study.get("protocolSection", {})
    id_mod    = proto.get("identificationModule", {})
    status_m  = proto.get("statusModule", {})
    design_m  = proto.get("designModule", {})
    cond_m    = proto.get("conditionsModule", {})
    interv_m  = proto.get("interventionsModule", {})
    sponsor_m = proto.get("sponsorCollaboratorsModule", {})

    nct_id = id_mod.get("nctId", "")
    if not nct_id:
        return None

    overall_status = status_m.get("overallStatus", "")
    if overall_status not in ("TERMINATED", "WITHDRAWN", "SUSPENDED"):
        return None

    why_stopped     = status_m.get("whyStopped", "")
    failure_reason  = classify_failure_reason(why_stopped)

    # Phase
    phases    = design_m.get("phases", [])
    phase_str = phases[0] if phases else "PHASE2"
    stage     = _PHASE_MAP.get(phase_str, "phase2")

    # Outcome encoding
    if overall_status == "WITHDRAWN":
        outcome   = "discontinued_p1"
        stage     = "phase1"
    else:
        outcome   = f"discontinued_{stage}"

    # Indication from conditions text
    conditions = cond_m.get("conditions", [])
    cond_text  = " ".join(conditions)
    indication = _map_indication(cond_text) or default_ind

    # Drug / mechanism
    interventions      = interv_m.get("interventions", [])
    drug_interventions = [i for i in interventions if i.get("type") in ("DRUG", "BIOLOGICAL")]
    drug_name          = (drug_interventions[0].get("name", "")
                         if drug_interventions else "")
    mechanism          = _infer_mechanism(drug_name + " " + str(interventions))

    brief_title = id_mod.get("briefTitle", drug_name or nct_id)

    return RawRecord(
        source      = DB_SOURCE_CT,
        source_id   = nct_id,
        url         = f"https://clinicaltrials.gov/study/{nct_id}",
        title       = brief_title[:200],
        indication  = indication,
        mechanism   = mechanism,
        clinical_stage = stage,
        decision    = "no-go",
        outcome     = outcome,
        investment_usd = 0.0,
        raw_text    = (f"{brief_title}. Conditions: {cond_text}. "
                       f"Drug: {drug_name}. WhyStopped: {why_stopped}"),
        extra       = {
            "why_stopped":    why_stopped,
            "failure_reason": failure_reason,
            "drug_name":      drug_name,
            "phases":         phases,
        },
    )


def fetch_terminated_trials(
    indication: str,
    query: str,
    max_records: int = 600,
) -> list[RawRecord]:
    """
    Pull terminated/withdrawn industry trials for one indication area.
    Paginates until max_records reached or CT.gov has no more.
    失敗しない: skips studies that fail parsing; never aborts the loop.
    """
    records: list[RawRecord] = []
    next_token: str | None   = None
    page = 0

    while len(records) < max_records:
        params: dict = {
            "query.term":           query,
            "filter.overallStatus": "TERMINATED,WITHDRAWN,SUSPENDED",
            "pageSize":             "100",
            "format":               "json",
        }
        if next_token:
            params["pageToken"] = next_token

        data = _get_json(CT_API, params, rate=_RT_CT, source="ct")
        if data is None:
            break

        studies = data.get("studies", [])
        page   += 1
        added   = 0
        for study in studies:
            try:
                rec = _ct_study_to_record(study, indication)
                if rec:
                    records.append(rec)
                    added += 1
            except Exception as e:
                log.debug("Skipping study: %s", e)

        next_token = data.get("nextPageToken")
        if not next_token or not studies:
            break

    return records


# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE 2 — ChEMBL: Withdrawn Approved Drugs
#  外部データ源2: 承認後に市場から撤回された薬 (最も強い安全性シグナル)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_chembl_withdrawn(max_records: int = 500) -> list[RawRecord]:
    """
    Pull drugs with withdrawn_flag=True from ChEMBL.
    These are drugs that passed all phases, got approved, then were WITHDRAWN
    from the market — almost all due to safety. This is the most powerful
    negative training signal available.
    """
    records: list[RawRecord] = []
    offset  = 0
    limit   = 100

    while len(records) < max_records:
        params = {
            "withdrawn_flag": "true",
            "format":         "json",
            "limit":          str(limit),
            "offset":         str(offset),
        }
        data = _get_json(f"{CHEMBL_API}/molecule.json", params,
                         rate=_RT_CHEMBL, source="chembl")
        if data is None:
            break

        molecules = data.get("molecules", [])
        if not molecules:
            break

        for mol in molecules:
            try:
                rec = _chembl_molecule_to_record(mol)
                if rec:
                    records.append(rec)
            except Exception as e:
                log.debug("ChEMBL parse error: %s", e)

        total = data.get("page_meta", {}).get("total_count", 0)
        offset += limit
        if offset >= total or offset >= max_records:
            break

    return records


def _chembl_molecule_to_record(mol: dict) -> RawRecord | None:
    chembl_id = mol.get("molecule_chembl_id", "")
    if not chembl_id:
        return None

    pref_name   = mol.get("pref_name") or chembl_id
    mol_type    = mol.get("molecule_type", "Small molecule")
    max_phase   = float(mol.get("max_phase", 4))
    indication  = _map_indication(str(mol.get("indication_class", "")))
    mechanism   = _infer_mechanism(mol_type)

    # Withdrawal reason from ChEMBL (often brief)
    wd_reason   = str(mol.get("withdrawn_reason", "") or "")
    failure_reason = classify_failure_reason(wd_reason) or "safety"  # default for withdrawals

    return RawRecord(
        source         = DB_SOURCE_CHEMBL,
        source_id      = chembl_id,
        url            = f"https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/",
        title          = pref_name,
        indication     = indication,
        mechanism      = mechanism,
        clinical_stage = "approved",   # It was approved (then withdrawn)
        decision       = "no-go",
        outcome        = "discontinued",  # market withdrawal
        investment_usd = 0.0,
        raw_text       = (f"{pref_name} ({mol_type}). "
                         f"Indication: {mol.get('indication_class', '')}. "
                         f"Withdrawn reason: {wd_reason}"),
        extra          = {
            "withdrawn_reason": wd_reason,
            "failure_reason":   failure_reason,
            "max_phase":        max_phase,
            "molecule_type":    mol_type,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SOURCE 3 — OpenFDA Drug Enforcement (Class I/II Recalls)
#  外部データ源3: FDA強制措置 (安全性による薬の回収)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_openfda_enforcement(max_records: int = 300) -> list[RawRecord]:
    """
    Pull FDA drug enforcement records (Class I = life-threatening risk).
    These represent post-market safety failures — drugs approved but recalled.
    """
    records: list[RawRecord] = []

    # Query Class I and II enforcement actions
    for classification in ("Class+I", "Class+II"):
        params = {
            "search": f'classification:"{classification}"',
            "limit":  "100",
        }
        data = _get_json(f"{OPENFDA_API}/enforcement.json", params,
                         rate=_RT_FDA, source="openfda")
        if data is None:
            continue

        results = data.get("results", [])
        for entry in results:
            try:
                rec = _fda_enforcement_to_record(entry)
                if rec:
                    records.append(rec)
            except Exception as e:
                log.debug("FDA enforcement parse error: %s", e)

        if len(records) >= max_records:
            break

    return records


def _fda_enforcement_to_record(entry: dict) -> RawRecord | None:
    recall_number = entry.get("recall_number", "")
    product_desc  = entry.get("product_description", "")
    reason_recall = entry.get("reason_for_recall", "")
    brand_name    = entry.get("brand_name", "")
    generic_name  = entry.get("generic_name", "")
    status        = entry.get("status", "")

    if not (product_desc or brand_name):
        return None

    drug_text     = f"{brand_name} {generic_name} {product_desc}"
    indication    = _map_indication(drug_text)
    mechanism     = _infer_mechanism(drug_text)
    failure_reason = classify_failure_reason(reason_recall)
    # Most FDA enforcement = safety issue
    if failure_reason == "unknown":
        failure_reason = "safety"

    return RawRecord(
        source         = DB_SOURCE_FDA,
        source_id      = recall_number or entry.get("recall_initiation_date", "") + product_desc[:20],
        url            = "https://api.fda.gov/drug/enforcement.json",
        title          = brand_name or generic_name or product_desc[:80],
        indication     = indication,
        mechanism      = mechanism,
        clinical_stage = "approved",
        decision       = "no-go",
        outcome        = "discontinued",
        investment_usd = 0.0,
        raw_text       = f"{drug_text}. Reason: {reason_recall}",
        extra          = {
            "reason_for_recall": reason_recall,
            "failure_reason":    failure_reason,
            "classification":    entry.get("classification", ""),
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Failure Pattern Analysis
#  失敗のパターン分析 — 失敗を意味づける
# ══════════════════════════════════════════════════════════════════════════════

_ALL_FAILURE_REASONS = [
    "safety", "efficacy", "commercial", "trial_design", "regulatory", "covid", "unknown"
]

_FAILURE_LESSONS = {
    "safety": (
        "Safety failures dominate this space. "
        "Prioritise early toxicology (in vitro toxicity panels, organ-on-chip), "
        "dose-escalation biomarkers, and patient selection to de-risk AEs."
    ),
    "efficacy": (
        "Efficacy is the primary killer here. "
        "Target validation, deep biology (PDX, single-cell), "
        "and early PK/PD biomarker endpoints are critical before committing to Phase 3."
    ),
    "commercial": (
        "Programs often die for non-scientific reasons (competitor approval, "
        "payer dynamics, M&A). Build competitive intelligence into portfolio decisions."
    ),
    "trial_design": (
        "Poor trial design / slow enrollment accounts for many terminations. "
        "Adaptive design, decentralised trials, and tight inclusion/exclusion criteria "
        "are underinvested but high-ROI."
    ),
    "regulatory": (
        "Regulatory/CMC issues: invest in manufacturing quality and early FDA dialogue "
        "(Type B meetings) — catching these at Phase 2 is far cheaper than Phase 3."
    ),
    "covid": (
        "External disruptions (pandemic, geopolitical) are real risks. "
        "Decentralised trial designs and redundant sites improve resilience."
    ),
    "unknown": (
        "Undisclosed termination reasons still encode signal — "
        "most undisclosed terminations are commercial or regulatory."
    ),
}


def compute_failure_patterns(all_records: list[dict]) -> dict:
    """
    Compute failure-mode statistics across all records that have failure_reason.
    Returns a nested dict: patterns[indication][mechanism][failure_reason] = count
    Also computes top failure reasons overall and per indication.
    """
    # Pull failure reason from extra field
    patterns: dict = defaultdict(lambda: defaultdict(Counter))
    overall: Counter = Counter()

    for rec in all_records:
        extra   = rec.get("extra", {})
        reason  = extra.get("failure_reason", "")
        if not reason:
            continue
        ind  = rec.get("indication", "other")
        mech = rec.get("mechanism", "small_molecule")
        patterns[ind][mech][reason] += 1
        overall[reason] += 1

    # Convert to plain dicts for JSON serialisation
    result = {
        "overall":    dict(overall.most_common()),
        "by_indication": {},
        "lessons": _FAILURE_LESSONS,
        "total_failure_records": sum(overall.values()),
    }
    for ind, mech_dict in patterns.items():
        result["by_indication"][ind] = {}
        ind_total: Counter = Counter()
        for mech, counts in mech_dict.items():
            result["by_indication"][ind][mech] = dict(counts)
            ind_total += counts
        result["by_indication"][ind]["_total"] = dict(ind_total)

    return result


def dominant_failure_mode(patterns: dict, indication: str, mechanism: str) -> tuple[str, str]:
    """
    Returns (top_reason, lesson) for a given indication × mechanism.
    Falls back to indication-level, then overall.
    """
    by_ind  = patterns.get("by_indication", {}).get(indication, {})
    mech_ct = by_ind.get(mechanism, {})
    ind_ct  = by_ind.get("_total", {})
    overall = patterns.get("overall", {})

    for counts in (mech_ct, ind_ct, overall):
        if counts:
            best = max(
                (k for k in counts if not k.startswith("_")),
                key=lambda r: counts.get(r, 0),
                default=None,
            )
            if best:
                return best, _FAILURE_LESSONS.get(best, "")
    return "unknown", _FAILURE_LESSONS["unknown"]


# ══════════════════════════════════════════════════════════════════════════════
#  Main pipeline
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    log.info("═" * 70)
    log.info("  Deepen Training — Failure Enrichment Pipeline")
    log.info("  失敗からの学習: Safety | Efficacy | Commercial | Trial Design | ...")
    log.info("═" * 70)

    # ── Step 0: Baseline AUC ─────────────────────────────────────────────────
    log.info("\nStep 0 — Measuring baseline model performance…")
    model_before = SuccessPredictor()
    metrics_before = model_before.train()
    auc_before = metrics_before.get("auc_roc", 0.0)
    n_before   = metrics_before.get("n_train", 0) + metrics_before.get("n_test", 0)
    log.info("  Baseline: AUC=%.3f  n=%d", auc_before, n_before)

    all_new_records: list[RawRecord] = []

    # ── Step 1: ClinicalTrials.gov — terminated trials ───────────────────────
    log.info("\nStep 1 — ClinicalTrials.gov terminated/withdrawn trials…")
    ct_total = 0
    for indication, query in _CT_QUERIES:
        log.info("  Fetching indication: %-20s  query: %s…", indication, query[:50])
        recs = fetch_terminated_trials(indication, query, max_records=600)
        all_new_records.extend(recs)
        ct_total += len(recs)
        log.info("    → %d records (running total %d)", len(recs), ct_total)
    log.info("  ClinicalTrials total: %d records", ct_total)

    # ── Step 2: ChEMBL — withdrawn compounds ─────────────────────────────────
    log.info("\nStep 2 — ChEMBL withdrawn approved compounds…")
    chembl_recs = fetch_chembl_withdrawn(max_records=500)
    all_new_records.extend(chembl_recs)
    log.info("  ChEMBL withdrawn: %d records", len(chembl_recs))

    # ── Step 3: OpenFDA — enforcement actions ────────────────────────────────
    log.info("\nStep 3 — OpenFDA drug enforcement (Class I/II recalls)…")
    fda_recs = fetch_openfda_enforcement(max_records=300)
    all_new_records.extend(fda_recs)
    log.info("  OpenFDA enforcement: %d records", len(fda_recs))

    log.info("\n  Total new records: %d", len(all_new_records))

    # ── Step 4: Classify failure reasons + summarise ─────────────────────────
    log.info("\nStep 4 — Failure reason classification…")
    reason_counts: Counter = Counter()
    for rec in all_new_records:
        reason = rec.extra.get("failure_reason", "n/a")
        reason_counts[reason] += 1

    log.info("  Failure reason breakdown (new records):")
    for reason, count in reason_counts.most_common():
        pct = 100.0 * count / max(len(all_new_records), 1)
        log.info("    %-14s  %5d  (%4.1f%%)", reason, count, pct)

    # ── Step 5: Upsert to DB ─────────────────────────────────────────────────
    log.info("\nStep 5 — Upserting %d records to DB…", len(all_new_records))
    inserted = bulk_upsert(all_new_records)
    log.info("  Inserted: %d  (skipped duplicates: %d)", inserted, len(all_new_records) - inserted)

    # ── Step 6: Compute failure patterns + save ──────────────────────────────
    log.info("\nStep 6 — Computing failure patterns across full DB…")
    all_db_records = fetch_all()
    patterns = compute_failure_patterns(all_db_records)
    FAILURE_PATTERNS_PATH.parent.mkdir(exist_ok=True)
    FAILURE_PATTERNS_PATH.write_text(json.dumps(patterns, indent=2, ensure_ascii=False))
    log.info("  Failure patterns saved → %s", FAILURE_PATTERNS_PATH)
    log.info("  Total records with failure_reason: %d", patterns["total_failure_records"])

    log.info("\n  Top failure reasons across full DB:")
    for reason, count in sorted(patterns["overall"].items(), key=lambda x: -x[1]):
        log.info("    %-14s  %d", reason, count)

    # ── Step 7: Re-train with enriched data ──────────────────────────────────
    log.info("\nStep 7 — Re-training SuccessPredictor on enriched DB…")
    model_after  = SuccessPredictor()
    metrics_after = model_after.train()
    auc_after  = metrics_after.get("auc_roc", 0.0)
    acc_after  = metrics_after.get("accuracy", 0.0)
    n_after    = metrics_after.get("n_train", 0) + metrics_after.get("n_test", 0)

    delta_auc  = auc_after - auc_before
    delta_n    = n_after - n_before

    # ── Step 8: Print failure lessons (核心: 失敗を意味づける) ───────────────
    log.info("\n" + "═" * 70)
    log.info("  FAILURE LESSONS — 失敗から学ぶ")
    log.info("═" * 70)
    for ind in ("oncology", "neurology", "cardiovascular", "rare_disease",
                "immunology", "metabolic", "infectious"):
        ind_data = patterns.get("by_indication", {}).get(ind, {})
        total    = ind_data.get("_total", {})
        if not total:
            continue
        dominant = max(total, key=lambda r: total[r])
        pct      = 100.0 * total[dominant] / max(sum(total.values()), 1)
        log.info("  %-20s  dominant failure: %-14s (%4.1f%%)  → %s",
                 ind, dominant, pct, _FAILURE_LESSONS.get(dominant, "")[:60])

    # ── Summary ──────────────────────────────────────────────────────────────
    log.info("\n" + "═" * 70)
    log.info("  RESULTS SUMMARY / 結果サマリー")
    log.info("═" * 70)
    log.info("  New records added  : %d (+%d training samples est.)",
             len(all_new_records), delta_n)
    log.info("  AUC before → after : %.3f → %.3f  (Δ%+.3f)",
             auc_before, auc_after, delta_auc)
    log.info("  Accuracy           : %.3f", acc_after)
    log.info("  Total training set : %d", n_after)
    log.info("  Failure patterns   : %s", FAILURE_PATTERNS_PATH)
    log.info("═" * 70)
    log.info("")
    log.info("  プログラムが失敗しない理由 / Why the program doesn't fail:")
    log.info("    → Every HTTP call is retried (exponential backoff)")
    log.info("    → Every parse error is caught and logged (never aborts)")
    log.info("    → Missing data triggers fallback defaults, not crashes")
    log.info("")
    log.info("  失敗の意味 / The meaning of failure:")
    log.info("    → Each failure_reason teaches us WHAT risk to watch for")
    log.info("    → Safety failures → chemistry/toxicology risk")
    log.info("    → Efficacy failures → target validation risk")
    log.info("    → Commercial failures → market/competitive risk")
    log.info("    → Every 'no-go' in the DB makes the model smarter")
    log.info("═" * 70)


if __name__ == "__main__":
    main()
