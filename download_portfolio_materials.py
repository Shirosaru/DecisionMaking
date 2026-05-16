#!/usr/bin/env python3
"""
VC Portfolio Materials Downloader
──────────────────────────────────
For every portfolio company across the 5 VC firms, downloads:

  1. Company website newsroom / press-release / pipeline pages
     → saves press-release text and any linked PDFs / PPTXes
  2. SEC EDGAR 8-K filings + EX-99.1 exhibits  (public companies)
  3. GlobeNewsWire biotech press releases       (keyword search)
  4. PubMed clinical publication abstracts      (drug + company)
  5. ClinicalTrials.gov terminated-study records (company sponsor)

Output:
  data/slides/portfolio/{company_slug}/          ← downloaded files
  data/portfolio_materials_report.html            ← full browsable reading list
"""

from __future__ import annotations

import hashlib, html as htmllib, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
OUTDIR   = Path("data/slides/portfolio")
REPORT   = Path("data/portfolio_materials_report.html")
RATE     = 0.5          # seconds between HTTP requests
MAX_SIZE = 8_000_000    # skip files > 8 MB
MAX_GNW  = 12           # GlobeNewsWire articles per company
MAX_EDGAR= 15           # EDGAR 8-K filings per company
MAX_PM   = 8            # PubMed abstracts per company
MAX_CT   = 8            # ClinicalTrials studies per company
MAX_SITE = 12           # links to follow per website

EDGAR_EFTS    = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
GNW_SEARCH    = "https://www.globenewswire.com/en/search/keyword/{q}/industry/57"
PM_ESEARCH    = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PM_EFETCH     = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
CT_API        = "https://clinicaltrials.gov/api/v2/studies"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
        "PortfolioResearch/1.0 (+https://github.com/Shirosaru/DecisionMaking)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─────────────────────────────────────────────────────────────────────────────
# Portfolio (35 companies, 5 firms)
# ─────────────────────────────────────────────────────────────────────────────
FIRMS: list[dict] = [
    {
        "name": "3E BioVentures",
        "url": "https://www.3ebiovc.com/content.php?cat_id=2",
        "companies": [
            dict(name="Aravive",                ticker="ARAV",  drug="batiraxcept",   website="https://aravive.com",           indication="oncology",       outcome="failed"),
            dict(name="OncoImmune",             ticker=None,    drug="anti-CD24",     website="https://oncoimmune.com",        indication="oncology",       outcome="acquired"),
            dict(name="C4 Therapeutics",        ticker="CCCC",  drug="CFT7455",       website="https://c4therapeutics.com",    indication="oncology",       outcome="pipeline_setback"),
            dict(name="Cognition Therapeutics", ticker="CGTX",  drug="CT1812",        website="https://cognitx.com",           indication="neurology",      outcome="failed"),
            dict(name="OncoC4",                 ticker=None,    drug="ONC-392",       website="https://oncoc4.com",            indication="oncology",       outcome="ongoing"),
            dict(name="Dewpoint Therapeutics",  ticker=None,    drug="DPT-SH-01",     website="https://dewpointx.com",         indication="neurology",      outcome="pipeline_setback"),
            dict(name="Cullgen",                ticker=None,    drug="CG-806",        website="https://cullgen.com",           indication="oncology",       outcome="ongoing"),
            dict(name="Lipidio",                ticker=None,    drug="LP101",         website="https://lipidiopharma.com",     indication="metabolic",      outcome="ongoing"),
            dict(name="Arnatar Therapeutics",   ticker=None,    drug="siRNA-ANGPTL3", website="https://arnatar.com",           indication="cardiovascular", outcome="ongoing"),
        ],
    },
    {
        "name": "BioVentures Capital",
        "url": "https://bioventures-capital.com/#two",
        "companies": [
            dict(name="Oraliva",       ticker=None, drug="oral-mucosa-delivery", website="https://oraliva.com",       indication="drug_delivery", outcome="ongoing"),
            dict(name="Biopathogenix", ticker=None, drug="BPG-01",               website="https://biopathogenix.com", indication="infectious",    outcome="ongoing"),
        ],
    },
    {
        "name": "BioVentures MedTech Funds",
        "url": "https://www.bioventuresinvestors.com/investment-portfolio",
        "companies": [
            dict(name="Optivio",          ticker=None, drug="cardiac-output-monitor", website="https://www.optivio.com",          indication="cardiovascular", outcome="ongoing"),
            dict(name="Endotronix",       ticker=None, drug="Cordella",               website="https://endotronix.com",           indication="cardiovascular", outcome="failed"),
            dict(name="CoNextions",       ticker=None, drug="augmented-suture",        website="https://www.conextionsmed.com",   indication="orthopaedics",   outcome="ongoing"),
            dict(name="Deep Vein Medical",ticker=None, drug="DVT-prevention",          website="https://www.bioventuresinvestors.com", indication="cardiovascular", outcome="ongoing"),
            dict(name="Verax Biomedical", ticker=None, drug="PGD-sterility-test",      website="https://www.veraxbiomedical.com", indication="infectious",     outcome="failed"),
        ],
    },
    {
        "name": "Pivotal Life Sciences",
        "url": "https://pivotallifesciences.com/portfolio/",
        "companies": [
            dict(name="IO Biotech",              ticker=None,   drug="IO102-IO103",   website="https://io-biotech.com",           indication="oncology",    outcome="failed"),
            dict(name="Bolt Biotherapeutics",    ticker=None,   drug="BDC-1001",      website="https://boltbio.com",              indication="oncology",    outcome="failed"),
            dict(name="BioAge Labs",             ticker=None,   drug="azelaprag",     website="https://bioagelabs.com",           indication="metabolic",   outcome="failed"),
            dict(name="Aligos Therapeutics",     ticker="ALGS", drug="ALG-020572",    website="https://aligos.com",               indication="infectious",  outcome="pipeline_setback"),
            dict(name="Gossamer Bio",            ticker="GOSS", drug="seralutinib",   website="https://gossamerbio.com",          indication="cardiovascular", outcome="failed"),
            dict(name="Exscientia",              ticker="EXAI", drug="EXS-21546",     website="https://exscientia.ai",            indication="oncology",    outcome="pipeline_setback"),
            dict(name="Inozyme Pharma",          ticker="INZY", drug="INZ-701",       website="https://inozymepharma.com",        indication="rare_disease",outcome="pipeline_setback"),
            dict(name="Oculis",                  ticker="OCS",  drug="OCS-01",        website="https://oculis.com",               indication="rare_disease",outcome="failed"),
            dict(name="Vigil Neuroscience",      ticker="VIGL", drug="VGL101",        website="https://vigilneuro.com",           indication="neurology",   outcome="ongoing"),
            dict(name="Trevi Therapeutics",      ticker="TRVI", drug="nalbuphine-ER", website="https://trevitherapeutics.com",    indication="neurology",   outcome="mixed"),
            dict(name="Karuna Therapeutics",     ticker="KRTX", drug="KarXT",         website="https://karunatx.com",             indication="neurology",   outcome="approved"),
            dict(name="Harmony Biosciences",     ticker="HRMY", drug="pitolisant",    website="https://harmonybiosciences.com",   indication="neurology",   outcome="approved"),
            dict(name="Gracell Biotechnologies", ticker="GRCL", drug="FasTCAR-CD19",  website="https://gracellbio.com",           indication="oncology",    outcome="acquired"),
            dict(name="Fusion Pharmaceuticals",  ticker="FUSN", drug="FPI-2265",      website="https://fusionpharma.com",         indication="oncology",    outcome="acquired"),
        ],
    },
    {
        "name": "Capital BioVentures",
        "url": "https://capitalbioventures.ca/portfolio/",
        "companies": [
            dict(name="Apiary TX",         ticker=None, drug="bee-venom-peptide", website="https://www.linkedin.com/company/apiarytx", indication="immunology",   outcome="ongoing"),
            dict(name="CerebroTX",         ticker=None, drug="CRB-001",           website="https://www.cerebrotx.com",                 indication="neurology",    outcome="ongoing"),
            dict(name="Cura Therapeutics", ticker=None, drug="RNA-targeted-SM",   website="https://www.curatherapeutics.com",          indication="neurology",    outcome="ongoing"),
            dict(name="FibroDynamX",       ticker=None, drug="FDX-001",           website="https://www.fibrodynamx.com",               indication="rare_disease", outcome="ongoing"),
            dict(name="i-RNA Therapeutics",ticker=None, drug="imRNA-001",         website="https://www.i-rna.ca",                      indication="immunology",   outcome="ongoing"),
        ],
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# HTTP / File utilities
# ─────────────────────────────────────────────────────────────────────────────
_last_req = 0.0

def _rate_limit():
    global _last_req
    elapsed = time.time() - _last_req
    if elapsed < RATE:
        time.sleep(RATE - elapsed)
    _last_req = time.time()

def _get(url: str, binary: bool = False, timeout: int = 8):
    """Fetch URL, return (content, content_type) or (None, None) on error."""
    _rate_limit()
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = r.getheader("Content-Type", "")
            data = r.read(MAX_SIZE)
        return (data, ct)
    except Exception as e:
        return (None, str(e))

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

def _sha8(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:8]

def _clean_html(raw: bytes | str) -> str:
    """Strip HTML tags; return readable plain text."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [l.strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)

def _save(path: Path, content: bytes | str, label: str = "") -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    kb = len(content) // 1024
    if label:
        print(f"      saved {kb:,} KB  {path.name}")
    return len(content)

def _is_doc(url: str) -> bool:
    u = url.lower().split("?")[0]
    return u.endswith((".pdf", ".pptx", ".ppt", ".docx", ".xlsx"))

def _is_news_link(url: str, text: str) -> bool:
    kw = ("news", "press", "release", "announc", "update", "data", "result",
          "trial", "phase", "pipeline", "investor", "present", "slide",
          "readout", "milestone", "approval", "fda", "asco", "esmo", "aacr")
    combined = (url + " " + text).lower()
    return any(k in combined for k in kw)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Company website crawler
# ─────────────────────────────────────────────────────────────────────────────
def crawl_website(co: dict, out: Path) -> list[dict]:
    """Fetch homepage + any news/press links found there. Hard 30s budget."""
    import time as _time
    items: list[dict] = []
    base = co["website"].rstrip("/")
    deadline = _time.time() + 30  # hard 30-second wall-clock budget

    # --- Step 1: fetch homepage only ---
    data, ct = _get(base, timeout=6)
    if not data:
        print(f"      website unreachable, skipping", flush=True)
        return items

    if not ("html" in (ct or "").lower() or base.endswith("/")):
        # Binary file at root — unlikely but handle it
        return items

    text = _clean_html(data)
    hp = out / "site_homepage.txt"
    if not hp.exists():
        _save(hp, text, label=f"  homepage")
    items.append(dict(
        source="website", company=co["name"],
        title="Homepage", url=base,
        file=str(hp), size=len(text), date="",
        preview=text[:600],
    ))

    # --- Step 2: collect interesting links from homepage ---
    soup = BeautifulSoup(data.decode("utf-8", errors="replace"), "lxml")
    base_host = urllib.parse.urlparse(base).netloc
    candidate_links: list[tuple[str, str]] = []  # (url, link_text)
    seen: set[str] = {base}

    for a in soup.find_all("a", href=True):
        if _time.time() > deadline:
            break
        href = a["href"].strip()
        link_text = a.get_text(strip=True)
        abs_url = urllib.parse.urljoin(base, href).split("#")[0]
        parsed = urllib.parse.urlparse(abs_url)
        if abs_url in seen:
            continue
        seen.add(abs_url)

        if _is_doc(abs_url):
            candidate_links.append((abs_url, link_text))
        elif parsed.netloc == base_host and _is_news_link(abs_url, link_text):
            candidate_links.append((abs_url, link_text))

    # --- Step 3: fetch up to MAX_SITE candidates ---
    for url, link_text in candidate_links[:MAX_SITE]:
        if _time.time() > deadline:
            print(f"      website budget exhausted", flush=True)
            break

        if _is_doc(url):
            doc_data, _ = _get(url, timeout=6)
            if not doc_data:
                continue
            fname = slugify(url.split("/")[-1].split("?")[0]) or _sha8(url)
            ext   = url.lower().split("?")[0].rsplit(".", 1)[-1]
            fpath = out / f"site_{fname}.{ext}"
            if not fpath.exists():
                _save(fpath, doc_data, label=f"  doc: {fname[:40]}")
            items.append(dict(
                source="website", company=co["name"],
                title=fpath.name, url=url,
                file=str(fpath), size=len(doc_data), date="",
                preview="",
            ))
            continue

        page_data, page_ct = _get(url, timeout=6)
        if not page_data or "html" not in (page_ct or "").lower():
            continue
        page_text = _clean_html(page_data)
        if len(page_text) < 200:
            continue
        pg_soup = BeautifulSoup(page_data.decode("utf-8", errors="replace"), "lxml")
        title_tag = pg_soup.find("h1") or pg_soup.find("h2") or pg_soup.find("title")
        title = title_tag.get_text(strip=True)[:80] if title_tag else link_text[:80] or url.split("/")[-1]
        fname = slugify(title)[:60] or _sha8(url)
        fpath = out / f"site_{fname}.txt"
        if not fpath.exists():
            _save(fpath, page_text, label=f"  {title[:60]}")
        items.append(dict(
            source="website", company=co["name"],
            title=title, url=url,
            file=str(fpath), size=len(page_text), date="",
            preview=page_text[:800],
        ))

    return items

# ─────────────────────────────────────────────────────────────────────────────
# 2. SEC EDGAR  (public companies only)
# ─────────────────────────────────────────────────────────────────────────────
def _edgar_filings(co: dict, out: Path) -> list[dict]:
    if not co.get("ticker"):
        return []
    items: list[dict] = []
    ticker = co["ticker"]
    name   = co["name"]
    print(f"    EDGAR {ticker} …", flush=True)

    edgar_dir = out / "edgar"
    edgar_dir.mkdir(parents=True, exist_ok=True)

    # Use EDGAR RSS Atom feed for 8-K filings
    rss_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={urllib.parse.quote(ticker)}&type=8-K&dateb=&owner=include&count={MAX_EDGAR}&search_text=&output=atom"
    rss_data, _ = _get(rss_url)
    if not rss_data:
        return items

    # Parse Atom feed
    soup = BeautifulSoup(rss_data, "lxml-xml") if b"<feed" in rss_data else BeautifulSoup(rss_data, "lxml")
    entries = soup.find_all("entry")
    print(f"      {len(entries)} 8-K entries found", flush=True)

    for entry in entries[:MAX_EDGAR]:
        filing_url = None
        for link in entry.find_all("link"):
            href = link.get("href", "")
            if "/Archives/" in href or "browse-edgar" in href:
                filing_url = href
                break
        if not filing_url:
            continue

        # Fetch the filing index page
        idx_data, _ = _get(filing_url)
        if not idx_data:
            continue

        idx_soup = BeautifulSoup(idx_data, "lxml")
        date = ""
        dt = entry.find("updated") or entry.find("published")
        if dt:
            date = dt.get_text()[:10]

        # Find EX-99.1 / EX-99.2 / EX-99.3 or any HTM/PDF exhibits
        found_exhibit = False
        for row in idx_soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            doc_type = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            desc     = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            link_tag = cells[2].find("a") if len(cells) > 2 else None
            if not link_tag:
                link_tag = row.find("a")
            if not link_tag:
                continue
            href = link_tag.get("href", "")
            if not href:
                continue
            ext = href.lower().rsplit(".", 1)[-1]
            is_exhibit = ("99" in doc_type or "exhibit" in desc.lower() or
                          "press" in desc.lower() or "presentation" in desc.lower())
            if is_exhibit and ext in ("htm", "html", "pdf"):
                abs_url = urllib.parse.urljoin("https://www.sec.gov", href)
                fname   = slugify(f"{ticker}_{date}_{desc or doc_type}")[:70]
                fpath   = edgar_dir / f"{fname}.{ext}"
                if not fpath.exists():
                    doc_data, _ = _get(abs_url)
                    if doc_data:
                        if ext in ("htm", "html"):
                            txt = _clean_html(doc_data)
                            _save(fpath.with_suffix(".txt"), txt, label=f"    {ticker} {date}")
                            fpath = fpath.with_suffix(".txt")
                            items.append(dict(
                                source="edgar", company=co["name"],
                                title=f"{ticker} 8-K {date}: {desc or doc_type}",
                                url=abs_url, file=str(fpath),
                                size=len(txt), date=date,
                                preview=txt[:800],
                            ))
                        else:
                            _save(fpath, doc_data, label=f"    {ticker} {date}")
                            items.append(dict(
                                source="edgar", company=co["name"],
                                title=f"{ticker} 8-K {date}: {desc or doc_type}",
                                url=abs_url, file=str(fpath),
                                size=len(doc_data), date=date,
                                preview="[PDF — open file to read]",
                            ))
                        found_exhibit = True
                        break

        if not found_exhibit:
            # Fallback: grab first HTM document in the filing
            for a in idx_soup.find_all("a", href=True):
                href = a["href"]
                if "/Archives/" in href and href.lower().endswith((".htm", ".html")):
                    abs_url = urllib.parse.urljoin("https://www.sec.gov", href)
                    doc_data, _ = _get(abs_url)
                    if doc_data:
                        txt = _clean_html(doc_data)
                        fname = slugify(f"{ticker}_{date}_8K")[:70]
                        fpath = edgar_dir / f"{fname}.txt"
                        if not fpath.exists():
                            _save(fpath, txt, label=f"    {ticker} {date} (fallback)")
                        items.append(dict(
                            source="edgar", company=co["name"],
                            title=f"{ticker} 8-K {date}",
                            url=abs_url, file=str(fpath),
                            size=len(txt), date=date,
                            preview=txt[:800],
                        ))
                    break

    return items

# ─────────────────────────────────────────────────────────────────────────────
# 3. GlobeNewsWire
# ─────────────────────────────────────────────────────────────────────────────
def _gnw_search(co: dict, out: Path) -> list[dict]:
    items: list[dict] = []
    name = co["name"]
    drug = co.get("drug", "")
    gnw_dir = out / "gnw"
    gnw_dir.mkdir(parents=True, exist_ok=True)

    queries = [name]
    if drug and drug != co["name"]:
        queries.append(drug)

    collected_urls: set[str] = set()

    for q in queries:
        if len(items) >= MAX_GNW:
            break
        enc = urllib.parse.quote(q)
        search_url = GNW_SEARCH.format(q=enc)
        data, _ = _get(search_url)
        if not data:
            continue

        soup = BeautifulSoup(data.decode("utf-8", errors="replace"), "lxml")

        # Extract article links
        article_urls: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            abs_url = urllib.parse.urljoin("https://www.globenewswire.com", href)
            if ("/news-release/" in abs_url or "/article/" in abs_url) and abs_url not in collected_urls:
                article_urls.append(abs_url)
                collected_urls.add(abs_url)

        for art_url in article_urls[:6]:
            art_data, _ = _get(art_url)
            if not art_data:
                continue
            art_soup = BeautifulSoup(art_data.decode("utf-8", errors="replace"), "lxml")
            title_tag = art_soup.find("h1") or art_soup.find("title")
            title = title_tag.get_text(strip=True)[:100] if title_tag else art_url.split("/")[-2]
            # Extract date
            date = ""
            date_tag = art_soup.find("time") or art_soup.find(class_=re.compile("date|time", re.I))
            if date_tag:
                date = date_tag.get("datetime", date_tag.get_text(strip=True))[:10]

            txt = _clean_html(art_data)
            fname = slugify(title)[:70] or _sha8(art_url)
            fpath = gnw_dir / f"{fname}.txt"
            if not fpath.exists():
                _save(fpath, txt, label=f"  GNW: {title[:60]}")
            items.append(dict(
                source="globenewswire", company=co["name"],
                title=title, url=art_url,
                file=str(fpath), size=len(txt), date=date,
                preview=txt[:800],
            ))
            if len(items) >= MAX_GNW:
                break

    return items

# ─────────────────────────────────────────────────────────────────────────────
# 4. PubMed
# ─────────────────────────────────────────────────────────────────────────────
def _pubmed_search(co: dict, out: Path) -> list[dict]:
    items: list[dict] = []
    name  = co["name"]
    drug  = co.get("drug", "")
    ind   = co.get("indication", "")
    pm_dir = out / "pubmed"
    pm_dir.mkdir(parents=True, exist_ok=True)

    queries = [
        f'"{drug}" clinical trial' if drug else f'"{name}" clinical',
        f'"{name}" phase',
        f'"{drug}" phase' if drug else "",
    ]

    seen_ids: set[str] = set()

    for q in queries:
        if not q or len(items) >= MAX_PM:
            break
        params = urllib.parse.urlencode({
            "db": "pubmed", "term": q,
            "retmax": str(MAX_PM), "retmode": "json",
        })
        data, _ = _get(f"{PM_ESEARCH}?{params}")
        if not data:
            continue
        try:
            ids = json.loads(data)["esearchresult"]["idlist"]
        except Exception:
            continue

        new_ids = [i for i in ids if i not in seen_ids]
        seen_ids.update(new_ids)
        if not new_ids:
            continue

        # Fetch abstracts
        fetch_params = urllib.parse.urlencode({
            "db": "pubmed", "id": ",".join(new_ids[:MAX_PM]),
            "rettype": "abstract", "retmode": "text",
        })
        abs_data, _ = _get(f"{PM_EFETCH}?{fetch_params}")
        if not abs_data:
            continue

        # Split into individual abstracts
        text = abs_data.decode("utf-8", errors="replace")
        chunks = re.split(r"\n\n(?=\d+\.\s)", text)
        for chunk in chunks[:MAX_PM]:
            if len(chunk.strip()) < 50:
                continue
            # Extract PMID
            pmid_m = re.search(r"PMID:\s*(\d+)", chunk)
            pmid = pmid_m.group(1) if pmid_m else _sha8(chunk)
            # Extract title (first non-blank line usually)
            first_line = next((l.strip() for l in chunk.splitlines() if l.strip()), "")
            title = first_line[:100]

            fpath = pm_dir / f"pubmed_{pmid}.txt"
            if not fpath.exists():
                _save(fpath, chunk, label=f"  PubMed {pmid}: {title[:50]}")
            items.append(dict(
                source="pubmed", company=co["name"],
                title=f"PubMed {pmid}: {title}",
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                file=str(fpath), size=len(chunk),
                date="", preview=chunk[:800],
            ))

    return items

# ─────────────────────────────────────────────────────────────────────────────
# 5. ClinicalTrials.gov
# ─────────────────────────────────────────────────────────────────────────────
_CT_STAGE = {"EARLY_PHASE1":"Phase 1","PHASE1":"Phase 1","PHASE1_PHASE2":"Phase 1/2",
             "PHASE2":"Phase 2","PHASE2_PHASE3":"Phase 2/3","PHASE3":"Phase 3","PHASE4":"Phase 4","NA":"N/A"}

def _ct_studies(co: dict, out: Path) -> list[dict]:
    items: list[dict] = []
    name = co["name"]
    ct_dir = out / "ct_gov"
    ct_dir.mkdir(parents=True, exist_ok=True)

    params = urllib.parse.urlencode({
        "query.spons": name,
        "format": "json",
        "pageSize": str(MAX_CT),
        "fields": "NCTId,BriefTitle,OfficialTitle,Phase,OverallStatus,Condition,"
                  "InterventionName,InterventionType,StudyType,StartDate,"
                  "CompletionDate,WhyStopped,BriefSummary,DetailedDescription,"
                  "PrimaryOutcomeMeasure,SecondaryOutcomeMeasure",
    })
    data, _ = _get(f"{CT_API}?{params}")
    if not data:
        return items
    try:
        studies = json.loads(data).get("studies", [])
    except Exception:
        return items

    for s in studies:
        proto = s.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        stat  = proto.get("statusModule", {})
        desc  = proto.get("descriptionModule", {})
        arms  = proto.get("armsInterventionsModule", {})

        nct      = ident.get("nctId", "")
        title    = ident.get("briefTitle", ident.get("officialTitle", nct))[:100]
        status   = stat.get("overallStatus", "")
        why_stop = stat.get("whyStopped", "")
        phase    = proto.get("designModule", {}).get("phases", ["N/A"])
        phase    = ", ".join(phase) if isinstance(phase, list) else phase
        summary  = desc.get("briefSummary", "")
        detailed = desc.get("detailedDescription", "")

        conditions  = ", ".join(proto.get("conditionsModule", {}).get("conditions", [])[:4])
        interventions = "; ".join(
            f"{i.get('type','')}: {i.get('name','')}"
            for i in arms.get("interventions", [])[:4]
        )

        text = (
            f"NCT ID: {nct}\n"
            f"Title: {title}\n"
            f"Status: {status}\n"
            f"Why Stopped: {why_stop}\n"
            f"Phase: {phase}\n"
            f"Conditions: {conditions}\n"
            f"Interventions: {interventions}\n\n"
            f"Brief Summary:\n{summary}\n\n"
            f"Detailed Description:\n{detailed}\n"
        )
        fpath = ct_dir / f"ct_{nct}.txt"
        if not fpath.exists():
            _save(fpath, text, label=f"  CT {nct}: {title[:50]}")
        items.append(dict(
            source="clinicaltrials", company=co["name"],
            title=f"CT {nct} [{status}] {title}",
            url=f"https://clinicaltrials.gov/study/{nct}",
            file=str(fpath), size=len(text),
            date=stat.get("startDateStruct", {}).get("date", "")[:10],
            preview=text[:800],
            why_stopped=why_stop, status=status,
        ))
        print(f"      {nct} [{status}] {title[:55]}", flush=True)

    return items

# ─────────────────────────────────────────────────────────────────────────────
# HTML report
# ─────────────────────────────────────────────────────────────────────────────
_SOURCE_BADGE = {
    "website":       ('<span class="badge site">Website</span>',        "#2563eb"),
    "edgar":         ('<span class="badge edgar">SEC EDGAR</span>',     "#7c3aed"),
    "globenewswire": ('<span class="badge gnw">GlobeNewsWire</span>',   "#0891b2"),
    "pubmed":        ('<span class="badge pm">PubMed</span>',           "#059669"),
    "clinicaltrials":('<span class="badge ct">ClinicalTrials</span>',   "#dc2626"),
}
_OUTCOME_BADGE = {
    "failed":           ("FAILED",           "#dc2626"),
    "pipeline_setback": ("SETBACK",          "#ea580c"),
    "mixed":            ("MIXED",            "#d97706"),
    "ongoing":          ("ONGOING",          "#2563eb"),
    "approved":         ("APPROVED",         "#16a34a"),
    "acquired":         ("ACQUIRED",         "#7c3aed"),
}

def _outcome_badge(outcome: str) -> str:
    label, color = _OUTCOME_BADGE.get(outcome, ("UNKNOWN", "#6b7280"))
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:.75rem;font-weight:700">{label}</span>'

def generate_report(firms_items: dict[str, dict[str, list[dict]]]) -> None:
    """Generate HTML report. firms_items = {firm_name: {co_name: [item,...]}}"""
    ts    = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = sum(len(v) for firm in firms_items.values() for v in firm.values())

    # Build stats
    by_source: dict[str, int] = {}
    total_kb = 0
    for firm in firms_items.values():
        for items in firm.values():
            for it in items:
                by_source[it["source"]] = by_source.get(it["source"], 0) + 1
                total_kb += it.get("size", 0)

    total_kb //= 1024

    nav_html = "\n".join(
        f'<a href="#{slugify(firm["name"])}">{firm["name"]}</a>'
        for firm in FIRMS
    )

    firm_sections = []
    for firm in FIRMS:
        fname  = firm["name"]
        fslug  = slugify(fname)
        co_sections = []

        for co in firm["companies"]:
            cname = co["name"]
            items = firms_items.get(fname, {}).get(cname, [])
            cslug = slugify(cname)
            outc  = co.get("outcome", "ongoing")
            ob    = _outcome_badge(outc)

            ct_terminated = [i for i in items if i["source"] == "clinicaltrials"
                             and i.get("status") in ("TERMINATED","WITHDRAWN","SUSPENDED")]

            # Count by source
            by_src = {}
            for it in items:
                by_src[it["source"]] = by_src.get(it["source"], 0) + 1

            src_pills = " ".join(
                f'<span style="background:{_SOURCE_BADGE[s][1]};color:#fff;'
                f'padding:1px 7px;border-radius:3px;font-size:.7rem">{s.upper()} {n}</span>'
                for s, n in by_src.items()
            )

            # Rows for each item
            rows = []
            for it in items:
                badge, _ = _SOURCE_BADGE.get(it["source"], ("", ""))
                frel = Path(it["file"]).name if it.get("file") else ""
                flink = f'<a href="{htmllib.escape(it["file"])}" target="_blank">📂 {htmllib.escape(frel)}</a>' if frel else ""
                ulink = f'<a href="{htmllib.escape(it["url"])}" target="_blank">🔗 source</a>' if it.get("url") else ""
                prev  = htmllib.escape((it.get("preview") or "")[:400]).replace("\n", "<br>")
                rows.append(f"""
                <tr>
                  <td>{badge}</td>
                  <td><strong>{htmllib.escape(it.get("title","")[:80])}</strong><br>
                      <small style="color:#666">{it.get("date","")}</small></td>
                  <td>{ulink} {flink}</td>
                  <td><details><summary style="cursor:pointer;color:#2563eb">expand</summary>
                      <pre style="white-space:pre-wrap;font-size:.78rem;max-height:300px;overflow:auto">{prev}</pre>
                      </details></td>
                </tr>""")

            rows_html = "\n".join(rows) if rows else "<tr><td colspan=4><em>No materials found</em></td></tr>"

            why_stop_html = ""
            if ct_terminated:
                entries = "".join(
                    f"<li><strong>{htmllib.escape(i.get('title',''))}</strong><br>"
                    f"<em>{htmllib.escape(i.get('why_stopped',''))}</em></li>"
                    for i in ct_terminated
                )
                why_stop_html = f"""
                <div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:6px;
                            padding:10px 14px;margin:8px 0">
                  <strong>⚠ Terminated/Withdrawn Trials (CT.gov)</strong>
                  <ul style="margin:6px 0">{entries}</ul>
                </div>"""

            co_sections.append(f"""
            <div id="{cslug}" style="background:#f8fafc;border:1px solid #e2e8f0;
                 border-radius:8px;padding:16px;margin-bottom:12px">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap">
                <h3 style="margin:0">{htmllib.escape(cname)}</h3>
                {ob}
                <span style="font-size:.8rem;color:#64748b">{htmllib.escape(co.get('drug',''))}</span>
                {src_pills}
                <span style="font-size:.8rem;color:#64748b">{len(items)} docs</span>
              </div>
              {why_stop_html}
              <table style="width:100%;border-collapse:collapse;font-size:.85rem">
                <thead><tr style="background:#e2e8f0">
                  <th style="padding:6px;text-align:left">Source</th>
                  <th style="padding:6px;text-align:left">Title</th>
                  <th style="padding:6px;text-align:left">Links</th>
                  <th style="padding:6px;text-align:left">Preview</th>
                </tr></thead>
                <tbody>{rows_html}</tbody>
              </table>
            </div>""")

        all_co_items = sum((firms_items.get(fname, {}).get(c["name"], []) for c in firm["companies"]), [])
        firm_sections.append(f"""
        <section id="{fslug}" style="margin-bottom:40px">
          <h2 style="border-bottom:2px solid #2563eb;padding-bottom:6px">
            {htmllib.escape(fname)}
            <small style="font-weight:400;font-size:1rem;color:#64748b">
              — {len(all_co_items)} documents
            </small>
          </h2>
          {''.join(co_sections)}
        </section>""")

    report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>VC Portfolio Materials — Full Reading List</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       background:#f1f5f9;color:#1e293b;margin:0;padding:0}}
  .navbar{{position:sticky;top:0;background:#1e293b;padding:10px 20px;
           display:flex;gap:16px;flex-wrap:wrap;z-index:100}}
  .navbar a{{color:#94a3b8;text-decoration:none;font-size:.85rem;white-space:nowrap}}
  .navbar a:hover{{color:#fff}}
  .hero{{background:#0f172a;color:#fff;padding:28px 32px}}
  .hero h1{{margin:0 0 6px}}
  .stat-grid{{display:flex;gap:16px;flex-wrap:wrap;margin-top:14px}}
  .stat{{background:rgba(255,255,255,.1);padding:12px 18px;border-radius:8px;
         text-align:center}}
  .stat .n{{font-size:1.8rem;font-weight:700}}
  .stat .l{{font-size:.75rem;color:#94a3b8;margin-top:2px}}
  .content{{max-width:1300px;margin:0 auto;padding:24px}}
  table td{{padding:6px 8px;border-bottom:1px solid #e2e8f0;vertical-align:top}}
  details summary::marker{{color:#2563eb}}
  a{{color:#2563eb}}
</style>
</head>
<body>

<div class="navbar">
  <span style="color:#fff;font-weight:700;margin-right:8px">📚 Portfolio Docs</span>
  {nav_html}
</div>

<div class="hero">
  <h1>VC Portfolio — Full Materials Download</h1>
  <p style="color:#94a3b8;margin:4px 0">Generated {ts} &nbsp;·&nbsp;
     Sources: Company websites, SEC EDGAR, GlobeNewsWire, PubMed, ClinicalTrials.gov</p>
  <div class="stat-grid">
    <div class="stat"><div class="n">{total:,}</div><div class="l">Total Documents</div></div>
    <div class="stat"><div class="n">{total_kb:,} KB</div><div class="l">Total Text Size</div></div>
    {''.join(f'<div class="stat"><div class="n">{n}</div><div class="l">{s.upper()}</div></div>' for s,n in sorted(by_source.items(), key=lambda x:-x[1]))}
  </div>
</div>

<div class="content">
  {''.join(firm_sections)}
</div>

</body></html>"""

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(report_html, encoding="utf-8")
    print(f"\nReport written → {REPORT}")
    print(f"Total: {total} documents, {total_kb:,} KB across {len(FIRMS)} firms")

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print(f"Portfolio materials downloader — {len(sum([f['companies'] for f in FIRMS],[]))} companies")
    OUTDIR.mkdir(parents=True, exist_ok=True)

    firms_items: dict[str, dict[str, list[dict]]] = {}

    for firm in FIRMS:
        fname = firm["name"]
        firms_items[fname] = {}
        print(f"\n── {fname} ──", flush=True)

        for co in firm["companies"]:
            cname = co["name"]
            cslug = slugify(cname)
            out   = OUTDIR / cslug
            out.mkdir(parents=True, exist_ok=True)
            print(f"  {cname}", flush=True)

            items: list[dict] = []

            # 1. ClinicalTrials.gov
            print(f"    ClinicalTrials …", flush=True)
            items += _ct_studies(co, out)

            # 2. PubMed
            print(f"    PubMed …", flush=True)
            items += _pubmed_search(co, out)

            # 3. GlobeNewsWire
            print(f"    GlobeNewsWire …", flush=True)
            items += _gnw_search(co, out)

            # 4. SEC EDGAR (public companies)
            if co.get("ticker"):
                items += _edgar_filings(co, out)

            # 5. Company website
            print(f"    Website {co['website'][:50]} …", flush=True)
            items += crawl_website(co, out)

            firms_items[fname][cname] = items
            total_co = len(items)
            print(f"  → {total_co} docs for {cname}", flush=True)

    generate_report(firms_items)

if __name__ == "__main__":
    main()
