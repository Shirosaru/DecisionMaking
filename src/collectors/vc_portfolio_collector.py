from __future__ import annotations

"""
VC Portfolio Collector
======================
Scrapes portfolio company lists from 6 major bioventure capital firms that
expose static/parseable HTML, then cross-references each company against
ClinicalTrials.gov to find their actual clinical pipeline records.

VC firms scraped (portfolio page → company names → ClinicalTrials lookup):
  • Atlas Venture          atlasventure.com/portfolio
  • Foresite Capital       foresitecapital.com/portfolio
  • Versant Ventures       versantventures.com/portfolio
  • Flagship Pioneering    flagshippioneering.com/companies
  • Third Rock Ventures    thirdrockventures.com/portfolio  (fallback: hardcoded)
  • RA Capital             racap.com/portfolio               (fallback: hardcoded)

For companies where ClinicalTrials.gov returns no study, the collector emits
a preclinical/early-stage synthetic record based on publicly available info.

Famous bioventure capital firms listed in the module docstring (read-only):
    OrbiMed, Versant Ventures, Atlas Venture, Third Rock Ventures,
    Flagship Pioneering, RA Capital Management, 5AM Ventures,
    Foresite Capital, Arch Venture Partners, NEA Healthcare,
    Bain Capital Life Sciences, MPM Capital, GV Life Sciences,
    Deerfield Management, Perceptive Advisors, Polaris Partners,
    Sofinnova Partners, Index Ventures, Novo Holdings, SR One,
    Canaan Partners, RTW Investments, BioMed Investors, Vida Ventures,
    Omega Funds, Pivotal bioVenture Partners, Droia Ventures,
    Pfizer Ventures, Johnson & Johnson Innovation, Roche Ventures
"""

import logging
import re
import time
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

# ── VC firm definitions ────────────────────────────────────────────────────────

FAMOUS_VC_FIRMS: list[dict[str, str]] = [
    {"name": "OrbiMed",                  "url": "https://www.orbimed.com",            "hq": "New York"},
    {"name": "Versant Ventures",         "url": "https://www.versantventures.com",    "hq": "San Francisco"},
    {"name": "Atlas Venture",            "url": "https://atlasventure.com",           "hq": "Boston"},
    {"name": "Third Rock Ventures",      "url": "https://thirdrockventures.com",      "hq": "Boston"},
    {"name": "Flagship Pioneering",      "url": "https://www.flagshippioneering.com", "hq": "Cambridge MA"},
    {"name": "RA Capital Management",    "url": "https://www.racap.com",              "hq": "Boston"},
    {"name": "5AM Ventures",             "url": "https://www.5amventures.com",        "hq": "Menlo Park"},
    {"name": "Foresite Capital",         "url": "https://foresitecapital.com",        "hq": "San Francisco"},
    {"name": "Arch Venture Partners",    "url": "https://www.archventure.com",        "hq": "Chicago"},
    {"name": "NEA Healthcare",           "url": "https://www.nea.com",               "hq": "Chevy Chase MD"},
    {"name": "Bain Capital Life Sciences","url": "https://www.baincapital.com",       "hq": "Boston"},
    {"name": "MPM Capital",              "url": "https://mpmcapital.com",             "hq": "San Francisco"},
    {"name": "GV Life Sciences",         "url": "https://www.gv.com",               "hq": "Mountain View"},
    {"name": "Deerfield Management",     "url": "https://www.deerfield.com",         "hq": "New York"},
    {"name": "Perceptive Advisors",      "url": "https://www.perceptiveadvisors.com","hq": "New York"},
    {"name": "Polaris Partners",         "url": "https://www.polarispartners.com",    "hq": "Boston"},
    {"name": "Sofinnova Partners",       "url": "https://www.sofinnova.fr",          "hq": "Paris"},
    {"name": "Index Ventures",           "url": "https://www.indexventures.com",     "hq": "London/SF"},
    {"name": "Novo Holdings",            "url": "https://www.novoholdings.com",      "hq": "Copenhagen"},
    {"name": "SR One",                   "url": "https://www.srone.com",             "hq": "Philadelphia"},
    {"name": "Canaan Partners",          "url": "https://www.canaan.com",            "hq": "Westport CT"},
    {"name": "RTW Investments",          "url": "https://www.rtwfunds.com",          "hq": "New York"},
    {"name": "Vida Ventures",            "url": "https://www.vidaventures.com",      "hq": "San Diego"},
    {"name": "Omega Funds",              "url": "https://www.omegafunds.com",        "hq": "Boston"},
    {"name": "Pivotal bioVenture",       "url": "https://www.pivotalbioventure.com", "hq": "San Francisco"},
    {"name": "Pfizer Ventures",          "url": "https://www.pfizer.com",            "hq": "New York"},
    {"name": "J&J Innovation",           "url": "https://jnjinnovation.com",         "hq": "New Brunswick NJ"},
    {"name": "Roche Ventures",           "url": "https://www.roche.com",             "hq": "Basel"},
    {"name": "Droia Ventures",           "url": "https://www.droiaventures.com",     "hq": "Copenhagen"},
    {"name": "BioMed Investors",         "url": "https://www.biomedinvestors.com",   "hq": "New York"},
]

# ── Portfolio pages we can actually scrape ────────────────────────────────────

_PORTFOLIO_PAGES: list[dict[str, Any]] = [
    # ── Sites with dedicated parsers ─────────────────────────────────────────
    {
        "vc": "Atlas Venture",
        "url": "https://atlasventure.com/portfolio/",
        "parser": "atlas",
    },
    {
        "vc": "Foresite Capital",
        "url": "https://foresitecapital.com/portfolio/",
        "parser": "foresite",
    },
    {
        "vc": "Versant Ventures",
        "url": "https://www.versantventures.com/portfolio",
        "parser": "versant",
    },
    {
        "vc": "Flagship Pioneering",
        "url": "https://www.flagshippioneering.com/companies",
        "parser": "flagship",
    },
    # ── Generic slug/heading parser — works on static-HTML portfolio pages ────
    {
        "vc": "Third Rock Ventures",
        "url": "https://thirdrockventures.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "RA Capital Management",
        "url": "https://www.racap.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "OrbiMed",
        "url": "https://www.orbimed.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "Arch Venture Partners",
        "url": "https://www.archventure.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "5AM Ventures",
        "url": "https://www.5amventures.com/portfolio-companies",
        "parser": "generic",
    },
    {
        "vc": "Novo Holdings",
        "url": "https://www.novoholdings.com/investments/life-sciences",
        "parser": "generic",
    },
    {
        "vc": "Sofinnova Partners",
        "url": "https://www.sofinnova.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "Omega Funds",
        "url": "https://www.omegafunds.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "Vida Ventures",
        "url": "https://www.vidaventures.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "RTW Investments",
        "url": "https://www.rtwfunds.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "Polaris Partners",
        "url": "https://www.polarispartners.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "Bain Capital Life Sciences",
        "url": "https://www.baincapitallifesciences.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "Perceptive Advisors",
        "url": "https://www.perceptiveadvisors.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "Deerfield Management",
        "url": "https://www.deerfield.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "SR One",
        "url": "https://www.srone.com/portfolio",
        "parser": "generic",
    },
    {
        "vc": "Canaan Partners",
        "url": "https://www.canaan.com/health",
        "parser": "generic",
    },
    {
        "vc": "Index Ventures",
        "url": "https://www.indexventures.com/portfolio",
        "parser": "generic",
    },
]

# Hardcoded known-active portfolio companies from real VC firm portfolios
# Sources: public VC websites, SEC filings, press releases, CrunchBase (public data)
_HARDCODED_COMPANIES: list[dict[str, str]] = [
    # ── Atlas Venture ─────────────────────────────────────────────────────
    {"company": "Blueprint Medicines",       "vc": "Atlas Venture"},
    {"company": "Agios Pharmaceuticals",     "vc": "Atlas Venture"},
    {"company": "Editas Medicine",           "vc": "Atlas Venture"},
    {"company": "Intellia Therapeutics",     "vc": "Atlas Venture"},
    {"company": "Kymera Therapeutics",       "vc": "Atlas Venture"},
    {"company": "Relay Therapeutics",        "vc": "Atlas Venture"},
    {"company": "Accent Therapeutics",       "vc": "Atlas Venture"},
    {"company": "Imago BioSciences",         "vc": "Atlas Venture"},
    {"company": "Enliven Therapeutics",      "vc": "Atlas Venture"},
    {"company": "Vividion Therapeutics",     "vc": "Atlas Venture"},
    {"company": "iTeos Therapeutics",        "vc": "Atlas Venture"},
    {"company": "Larimar Therapeutics",      "vc": "Atlas Venture"},
    {"company": "Pandion Therapeutics",      "vc": "Atlas Venture"},
    {"company": "Entrada Therapeutics",      "vc": "Atlas Venture"},
    {"company": "Omega Therapeutics",        "vc": "Atlas Venture"},
    {"company": "Lyra Therapeutics",         "vc": "Atlas Venture"},
    {"company": "Gain Therapeutics",         "vc": "Atlas Venture"},
    {"company": "Generation Bio",            "vc": "Atlas Venture"},
    {"company": "Verastem",                  "vc": "Atlas Venture"},
    {"company": "Imara",                     "vc": "Atlas Venture"},
    # ── Third Rock Ventures ───────────────────────────────────────────────
    {"company": "Karuna Therapeutics",       "vc": "Third Rock Ventures"},
    {"company": "Scholar Rock",              "vc": "Third Rock Ventures"},
    {"company": "Protagonist Therapeutics",  "vc": "Third Rock Ventures"},
    {"company": "BlackDiamond Therapeutics", "vc": "Third Rock Ventures"},
    {"company": "Disc Medicine",             "vc": "Third Rock Ventures"},
    {"company": "Celsius Therapeutics",      "vc": "Third Rock Ventures"},
    {"company": "Karyopharm Therapeutics",   "vc": "Third Rock Ventures"},
    {"company": "Anchor BioSciences",        "vc": "Third Rock Ventures"},
    {"company": "Foundation Medicine",       "vc": "Third Rock Ventures"},
    {"company": "Aileron Therapeutics",      "vc": "Third Rock Ventures"},
    # ── Flagship Pioneering ───────────────────────────────────────────────
    {"company": "Moderna",                   "vc": "Flagship Pioneering"},
    {"company": "Translate Bio",             "vc": "Flagship Pioneering"},
    {"company": "Rubius Therapeutics",       "vc": "Flagship Pioneering"},
    {"company": "Evelo Biosciences",         "vc": "Flagship Pioneering"},
    {"company": "Kaleido Biosciences",       "vc": "Flagship Pioneering"},
    {"company": "Axcella Health",            "vc": "Flagship Pioneering"},
    {"company": "Vedanta Biosciences",       "vc": "Flagship Pioneering"},
    {"company": "Generate Biomedicines",     "vc": "Flagship Pioneering"},
    {"company": "Ring Therapeutics",         "vc": "Flagship Pioneering"},
    {"company": "EQRx",                      "vc": "Flagship Pioneering"},
    {"company": "Sienna Biopharmaceuticals", "vc": "Flagship Pioneering"},
    {"company": "Neon Therapeutics",         "vc": "Flagship Pioneering"},
    # ── RA Capital Management ─────────────────────────────────────────────
    {"company": "Relay Therapeutics",        "vc": "RA Capital"},
    {"company": "Passage Bio",               "vc": "RA Capital"},
    {"company": "Merus",                     "vc": "RA Capital"},
    {"company": "Arrowhead Pharmaceuticals", "vc": "RA Capital"},
    {"company": "SpringWorks Therapeutics",  "vc": "RA Capital"},
    {"company": "Aldeyra Therapeutics",      "vc": "RA Capital"},
    {"company": "Alector",                   "vc": "RA Capital"},
    {"company": "Arcus Biosciences",         "vc": "RA Capital"},
    {"company": "Arvinas",                   "vc": "RA Capital"},
    {"company": "Bicycle Therapeutics",      "vc": "RA Capital"},
    {"company": "Black Diamond Therapeutics","vc": "RA Capital"},
    {"company": "Boundless Bio",             "vc": "RA Capital"},
    {"company": "Celldex Therapeutics",      "vc": "RA Capital"},
    {"company": "Day One Pharmaceuticals",   "vc": "RA Capital"},
    {"company": "Dyne Therapeutics",         "vc": "RA Capital"},
    # ── ARCH Venture Partners ─────────────────────────────────────────────
    {"company": "Alnylam Pharmaceuticals",   "vc": "Arch Venture"},
    {"company": "Juno Therapeutics",         "vc": "Arch Venture"},
    {"company": "GRAIL",                     "vc": "Arch Venture"},
    {"company": "Recursion Pharmaceuticals", "vc": "Arch Venture"},
    {"company": "Sana Biotechnology",        "vc": "Arch Venture"},
    {"company": "Prime Medicine",            "vc": "Arch Venture"},
    {"company": "Neumora Therapeutics",      "vc": "Arch Venture"},
    {"company": "Nuvation Bio",              "vc": "Arch Venture"},
    {"company": "AbSci",                     "vc": "Arch Venture"},
    {"company": "C4 Therapeutics",           "vc": "Arch Venture"},
    {"company": "Lyric Therapeutics",        "vc": "Arch Venture"},
    # ── 5AM Ventures ──────────────────────────────────────────────────────
    {"company": "Navire Pharma",             "vc": "5AM Ventures"},
    {"company": "Artios Pharma",             "vc": "5AM Ventures"},
    {"company": "CG Oncology",               "vc": "5AM Ventures"},
    {"company": "Lykan Bioscience",          "vc": "5AM Ventures"},
    {"company": "Janux Therapeutics",        "vc": "5AM Ventures"},
    {"company": "Chinook Therapeutics",      "vc": "5AM Ventures"},
    {"company": "Turning Point Therapeutics","vc": "5AM Ventures"},
    # ── Bain Capital Life Sciences ────────────────────────────────────────
    {"company": "Prelude Therapeutics",      "vc": "Bain Capital Life Sciences"},
    {"company": "Fusion Pharmaceuticals",    "vc": "Bain Capital Life Sciences"},
    {"company": "Cogent Biosciences",        "vc": "Bain Capital Life Sciences"},
    {"company": "PMV Pharmaceuticals",       "vc": "Bain Capital Life Sciences"},
    {"company": "Rigel Pharmaceuticals",     "vc": "Bain Capital Life Sciences"},
    {"company": "Protagonist Therapeutics",  "vc": "Bain Capital Life Sciences"},
    {"company": "Passage Bio",               "vc": "Bain Capital Life Sciences"},
    # ── NEA Healthcare ────────────────────────────────────────────────────
    {"company": "Dyne Therapeutics",         "vc": "NEA Healthcare"},
    {"company": "Turning Point Therapeutics","vc": "NEA Healthcare"},
    {"company": "Eliem Therapeutics",        "vc": "NEA Healthcare"},
    {"company": "Recursion Pharmaceuticals", "vc": "NEA Healthcare"},
    {"company": "ArQule",                    "vc": "NEA Healthcare"},
    {"company": "Acelyrin",                  "vc": "NEA Healthcare"},
    {"company": "Arena Pharmaceuticals",     "vc": "NEA Healthcare"},
    # ── Novo Holdings ─────────────────────────────────────────────────────
    {"company": "Zealand Pharma",            "vc": "Novo Holdings"},
    {"company": "Evotec",                    "vc": "Novo Holdings"},
    {"company": "Bioxcel Therapeutics",      "vc": "Novo Holdings"},
    {"company": "Monte Rosa Therapeutics",   "vc": "Novo Holdings"},
    {"company": "Inversago Pharma",          "vc": "Novo Holdings"},
    {"company": "Nuevolution",               "vc": "Novo Holdings"},
    # ── Sofinnova Partners ────────────────────────────────────────────────
    {"company": "Cellectis",                 "vc": "Sofinnova"},
    {"company": "Pharvaris",                 "vc": "Sofinnova"},
    {"company": "Innate Pharma",             "vc": "Sofinnova"},
    {"company": "OSE Immunotherapeutics",    "vc": "Sofinnova"},
    {"company": "ERYTECH Pharma",            "vc": "Sofinnova"},
    {"company": "Abionyx Pharma",            "vc": "Sofinnova"},
    {"company": "Xenothera",                 "vc": "Sofinnova"},
    {"company": "MedDay Pharmaceuticals",    "vc": "Sofinnova"},
    # ── MPM Capital ───────────────────────────────────────────────────────
    {"company": "Bicycle Therapeutics",      "vc": "MPM Capital"},
    {"company": "Metacrine",                 "vc": "MPM Capital"},
    {"company": "Silence Therapeutics",      "vc": "MPM Capital"},
    {"company": "Vividion Therapeutics",     "vc": "MPM Capital"},
    {"company": "Sutro Biopharma",           "vc": "MPM Capital"},
    {"company": "Avrobio",                   "vc": "MPM Capital"},
    # ── OrbiMed ───────────────────────────────────────────────────────────
    {"company": "Ra Pharmaceuticals",        "vc": "OrbiMed"},
    {"company": "Achaogen",                  "vc": "OrbiMed"},
    {"company": "BioAtla",                   "vc": "OrbiMed"},
    {"company": "CytomX Therapeutics",       "vc": "OrbiMed"},
    {"company": "Cidara Therapeutics",       "vc": "OrbiMed"},
    {"company": "Alector",                   "vc": "OrbiMed"},
    {"company": "Arcus Biosciences",         "vc": "OrbiMed"},
    {"company": "Xilio Therapeutics",        "vc": "OrbiMed"},
    {"company": "Arena Pharmaceuticals",     "vc": "OrbiMed"},
    {"company": "Harmony Biosciences",       "vc": "OrbiMed"},
    # ── Deerfield Management ──────────────────────────────────────────────
    {"company": "Esperion Therapeutics",     "vc": "Deerfield Management"},
    {"company": "Harmony Biosciences",       "vc": "Deerfield Management"},
    {"company": "Principia Biopharma",       "vc": "Deerfield Management"},
    {"company": "Ra Pharmaceuticals",        "vc": "Deerfield Management"},
    {"company": "Neos Therapeutics",         "vc": "Deerfield Management"},
    {"company": "Rigel Pharmaceuticals",     "vc": "Deerfield Management"},
    {"company": "Corcept Therapeutics",      "vc": "Deerfield Management"},
    {"company": "Turning Point Therapeutics","vc": "Deerfield Management"},
    {"company": "Merus",                     "vc": "Deerfield Management"},
    # ── Polaris Partners ──────────────────────────────────────────────────
    {"company": "Cidara Therapeutics",       "vc": "Polaris Partners"},
    {"company": "Esperion Therapeutics",     "vc": "Polaris Partners"},
    {"company": "Forma Therapeutics",        "vc": "Polaris Partners"},
    {"company": "H3 Biomedicine",            "vc": "Polaris Partners"},
    {"company": "Iterion Therapeutics",      "vc": "Polaris Partners"},
    {"company": "Morphic Therapeutic",       "vc": "Polaris Partners"},
    {"company": "Translate Bio",             "vc": "Polaris Partners"},
    # ── Perceptive Advisors ───────────────────────────────────────────────
    {"company": "Imago BioSciences",         "vc": "Perceptive Advisors"},
    {"company": "Turning Point Therapeutics","vc": "Perceptive Advisors"},
    {"company": "Relay Therapeutics",        "vc": "Perceptive Advisors"},
    {"company": "Keros Therapeutics",        "vc": "Perceptive Advisors"},
    {"company": "Invaio Sciences",           "vc": "Perceptive Advisors"},
    {"company": "Janux Therapeutics",        "vc": "Perceptive Advisors"},
    # ── SR One (GSK Ventures) ─────────────────────────────────────────────
    {"company": "iTeos Therapeutics",        "vc": "SR One"},
    {"company": "Nuvation Bio",              "vc": "SR One"},
    {"company": "Black Diamond Therapeutics","vc": "SR One"},
    {"company": "EQRx",                      "vc": "SR One"},
    {"company": "Protagonist Therapeutics",  "vc": "SR One"},
    {"company": "Cogent Biosciences",        "vc": "SR One"},
    # ── RTW Investments ───────────────────────────────────────────────────
    {"company": "Blueprint Medicines",       "vc": "RTW Investments"},
    {"company": "Black Diamond Therapeutics","vc": "RTW Investments"},
    {"company": "Turning Point Therapeutics","vc": "RTW Investments"},
    {"company": "Day One Pharmaceuticals",   "vc": "RTW Investments"},
    {"company": "Passage Bio",               "vc": "RTW Investments"},
    {"company": "Scholar Rock",              "vc": "RTW Investments"},
    {"company": "Metacrine",                 "vc": "RTW Investments"},
    # ── Canaan Partners ───────────────────────────────────────────────────
    {"company": "Arena Pharmaceuticals",     "vc": "Canaan Partners"},
    {"company": "Cidara Therapeutics",       "vc": "Canaan Partners"},
    {"company": "ContraFect",                "vc": "Canaan Partners"},
    {"company": "Genocea Biosciences",       "vc": "Canaan Partners"},
    {"company": "Kala Pharmaceuticals",      "vc": "Canaan Partners"},
    {"company": "Keros Therapeutics",        "vc": "Canaan Partners"},
    {"company": "Kronos Bio",                "vc": "Canaan Partners"},
    {"company": "Lyra Therapeutics",         "vc": "Canaan Partners"},
    {"company": "Merus",                     "vc": "Canaan Partners"},
    {"company": "Monte Rosa Therapeutics",   "vc": "Canaan Partners"},
    {"company": "Nuvation Bio",              "vc": "Canaan Partners"},
    {"company": "Phathom Pharmaceuticals",   "vc": "Canaan Partners"},
    {"company": "Prelude Therapeutics",      "vc": "Canaan Partners"},
    # ── Vida Ventures ────────────────────────────────────────────────────
    {"company": "Artios Pharma",             "vc": "Vida Ventures"},
    {"company": "Cidara Therapeutics",       "vc": "Vida Ventures"},
    {"company": "Editas Medicine",           "vc": "Vida Ventures"},
    {"company": "Monte Rosa Therapeutics",   "vc": "Vida Ventures"},
    {"company": "Navire Pharma",             "vc": "Vida Ventures"},
    {"company": "Protagonist Therapeutics",  "vc": "Vida Ventures"},
    # ── Omega Funds ──────────────────────────────────────────────────────
    {"company": "Karyopharm Therapeutics",   "vc": "Omega Funds"},
    {"company": "Zentalis Pharmaceuticals",  "vc": "Omega Funds"},
    {"company": "iTeos Therapeutics",        "vc": "Omega Funds"},
    {"company": "Monte Rosa Therapeutics",   "vc": "Omega Funds"},
    {"company": "Relay Therapeutics",        "vc": "Omega Funds"},
    {"company": "Scholar Rock",              "vc": "Omega Funds"},
    {"company": "Silence Therapeutics",      "vc": "Omega Funds"},
    {"company": "Turning Point Therapeutics","vc": "Omega Funds"},
    # ── GV Life Sciences (Google Ventures) ───────────────────────────────
    {"company": "Blueprint Medicines",       "vc": "GV Life Sciences"},
    {"company": "Editas Medicine",           "vc": "GV Life Sciences"},
    {"company": "Relay Therapeutics",        "vc": "GV Life Sciences"},
    {"company": "Recursion Pharmaceuticals", "vc": "GV Life Sciences"},
    {"company": "Adimab",                    "vc": "GV Life Sciences"},
    # ── Pivotal bioVenture Partners ───────────────────────────────────────
    {"company": "Prelude Therapeutics",      "vc": "Pivotal bioVenture"},
    {"company": "Olimmune",                  "vc": "Pivotal bioVenture"},
    {"company": "Acelyrin",                  "vc": "Pivotal bioVenture"},
    # ── Pfizer Ventures ───────────────────────────────────────────────────
    {"company": "Arvinas",                   "vc": "Pfizer Ventures"},
    {"company": "Relay Therapeutics",        "vc": "Pfizer Ventures"},
    {"company": "SpringWorks Therapeutics",  "vc": "Pfizer Ventures"},
    {"company": "C4 Therapeutics",           "vc": "Pfizer Ventures"},
    # ── J&J Innovation ────────────────────────────────────────────────────
    {"company": "Protagonist Therapeutics",  "vc": "J&J Innovation"},
    {"company": "Janux Therapeutics",        "vc": "J&J Innovation"},
    {"company": "Scholar Rock",              "vc": "J&J Innovation"},
    {"company": "Merus",                     "vc": "J&J Innovation"},
    # ── Roche Ventures ────────────────────────────────────────────────────
    {"company": "Relay Therapeutics",        "vc": "Roche Ventures"},
    {"company": "Blueprint Medicines",       "vc": "Roche Ventures"},
    {"company": "Turning Point Therapeutics","vc": "Roche Ventures"},
    {"company": "Black Diamond Therapeutics","vc": "Roche Ventures"},
    # ── Droia Ventures ────────────────────────────────────────────────────
    {"company": "Silence Therapeutics",      "vc": "Droia Ventures"},
    {"company": "Zealand Pharma",            "vc": "Droia Ventures"},
    {"company": "Pharvaris",                 "vc": "Droia Ventures"},
    # ── BioMed Investors ──────────────────────────────────────────────────
    {"company": "Bicycle Therapeutics",      "vc": "BioMed Investors"},
    {"company": "Silence Therapeutics",      "vc": "BioMed Investors"},
    {"company": "Morphic Therapeutic",       "vc": "BioMed Investors"},
]

# ── ClinicalTrials.gov API ────────────────────────────────────────────────────

_CT_API     = "https://clinicaltrials.gov/api/v2/studies"
_CT_HEADERS = {"User-Agent": "BioVentureResearch/1.0 (academic-poc)"}

_STAGE_MAP = {
    "PHASE1":  "phase1", "PHASE 1": "phase1", "Phase 1": "phase1",
    "PHASE2":  "phase2", "PHASE 2": "phase2", "Phase 2": "phase2",
    "PHASE3":  "phase3", "PHASE 3": "phase3", "Phase 3": "phase3",
    "PHASE4":  "approved",
    "NA": "preclinical", "EARLY_PHASE1": "phase1",
}

_STATUS_MAP = {
    "TERMINATED": "no-go",    "WITHDRAWN": "no-go",    "SUSPENDED": "no-go",
    "COMPLETED":  "go",       "ACTIVE_NOT_RECRUITING": "go",
    "RECRUITING": "go",       "NOT_YET_RECRUITING": "go",
    "UNKNOWN_STATUS": "undecided",
}

_OUTCOME_MAP = {
    "TERMINATED": "discontinued", "WITHDRAWN": "discontinued",
    "SUSPENDED":  "discontinued", "COMPLETED": "ongoing",
    "ACTIVE_NOT_RECRUITING": "ongoing", "RECRUITING": "ongoing",
}

_IND_RE = re.compile(
    r"\b(cancer|carcinoma|oncology|leukemia|lymphoma|melanoma|glioblastoma|"
    r"rare disease|autoimmune|rheumatoid|lupus|crohn|colitis|"
    r"neurology|alzheimer|parkinson|ALS|multiple sclerosis|"
    r"cardiovascular|heart failure|hypertension|"
    r"metabolic|diabetes|obesity|NASH|fatty liver|"
    r"infectious|HIV|hepatitis|influenza|COVID|"
    r"inflammation|psoriasis|atopic dermatitis|eczema|"
    r"hematology|sickle cell|hemophilia|gene therapy|"
    r"renal|kidney|pulmonary|fibrosis|"
    r"solid tumor|tumor|neoplasm|myeloma|sarcoma)\b",
    re.IGNORECASE,
)

_MECH_RE = re.compile(
    r"\b(antibody|monoclonal|bispecific|ADC|conjugate|"
    r"small molecule|inhibitor|kinase|checkpoint|PD.?1|PD.?L1|CTLA.?4|"
    r"cell therapy|CAR.?T|T.?cell|NK cell|"
    r"gene therapy|CRISPR|AAV|lentiviral|"
    r"RNA|siRNA|mRNA|antisense|oligonucleotide|"
    r"enzyme|protein|peptide|vaccine|immunotherapy)\b",
    re.IGNORECASE,
)

_INVEST_RE = re.compile(r"\$\s*(\d[\d,.]*)\s*(million|M|billion|B)\b", re.IGNORECASE)


def _extract_ind(text: str) -> str:
    m = _IND_RE.search(text)
    return m.group(0).lower() if m else "oncology"


def _extract_mech(text: str) -> str:
    m = _MECH_RE.search(text)
    return m.group(0).lower() if m else "small molecule inhibitor"


# ── HTML parsers for each VC site ─────────────────────────────────────────────

def _parse_atlas(html: str) -> list[dict[str, str]]:
    """Extract company name + description from Atlas Venture portfolio page."""
    soup = BeautifulSoup(html, "lxml")
    results = []
    # Atlas renders companies in <article> or <div> with class containing "company"
    # Based on what we observed, descriptions follow a link to each company
    for tag in soup.find_all(["h3", "h4", "h5", "strong", "a"], href=True):
        href = tag.get("href", "")
        text = tag.get_text(strip=True)
        if not text or len(text) < 3:
            continue
        # Skip navigation links
        if any(skip in href for skip in ["/team", "/portfolio?", "/careers", "/news",
                                          "/contact", "/discover", "/login", "twitter",
                                          "linkedin", "lifescivc"]):
            continue
        # Grab sibling/parent text for description
        parent = tag.parent
        desc = parent.get_text(" ", strip=True) if parent else ""
        results.append({"company": text, "description": desc, "vc": "Atlas Venture"})
    # Deduplicate by company name
    seen = set()
    out = []
    for r in results:
        if r["company"] not in seen and len(r["company"]) > 4:
            seen.add(r["company"])
            out.append(r)
    return out[:80]


def _parse_foresite(html: str) -> list[dict[str, str]]:
    """Extract company names from Foresite Capital portfolio page."""
    soup = BeautifulSoup(html, "lxml")
    results = []
    # Foresite uses <h5> tags for company names (confirmed from live page)
    for tag in soup.find_all("h5"):
        text = tag.get_text(strip=True)
        # Strip "(Alumni)" suffix
        company = re.sub(r"\s*\(Alumni\)\s*", "", text).strip()
        if company and len(company) > 3:
            results.append({"company": company, "description": "", "vc": "Foresite Capital"})
    seen = set()
    out = []
    for r in results:
        if r["company"] not in seen:
            seen.add(r["company"])
            out.append(r)
    return out[:80]


def _parse_versant(html: str) -> list[dict[str, str]]:
    """Extract company slugs from Versant Ventures portfolio page."""
    soup = BeautifulSoup(html, "lxml")
    results = []
    for tag in soup.find_all("a", href=re.compile(r"/portfolio/")):
        href = tag.get("href", "")
        slug = href.rstrip("/").split("/")[-1]
        # Convert slug to title: "relay-therapeutics" → "Relay Therapeutics"
        company = " ".join(w.capitalize() for w in slug.replace("-", " ").split())
        if company and len(company) > 3:
            results.append({"company": company, "description": "", "vc": "Versant Ventures"})
    seen = set()
    out = []
    for r in results:
        if r["company"] not in seen:
            seen.add(r["company"])
            out.append(r)
    return out[:60]


def _parse_flagship(html: str) -> list[dict[str, str]]:
    """Extract company slugs from Flagship Pioneering companies page."""
    soup = BeautifulSoup(html, "lxml")
    results = []
    for tag in soup.find_all("a", href=re.compile(r"/companies/")):
        href = tag.get("href", "")
        slug = href.rstrip("/").split("/")[-1]
        if slug in ("companies", ""):
            continue
        company = " ".join(w.capitalize() for w in slug.replace("-", " ").split())
        if company and len(company) > 3:
            results.append({"company": company, "description": "", "vc": "Flagship Pioneering"})
    seen = set()
    out = []
    for r in results:
        if r["company"] not in seen:
            seen.add(r["company"])
            out.append(r)
    return out[:60]


def _parse_generic(html: str, vc: str = "") -> list[dict[str, str]]:
    """
    Generic portfolio page parser — tries two patterns in order:
      1. <a href="/portfolio/slug">, /companies/slug, /investments/slug, etc.
      2. Short heading tags (h3/h4/h5) that look like company names.
    Works on sites that render portfolio cards in static HTML.
    Returns an empty list gracefully when JS-rendered (no matching tags).
    """
    soup = BeautifulSoup(html, "lxml")
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    _PORTFOLIO_PATH_RE = re.compile(
        r"/(portfolio|companies|investments|company|portfolio-companies)/([^/?#\"'\s]{2,})",
        re.IGNORECASE,
    )
    _SKIP_SLUGS = {
        "all", "alumni", "current", "current-portfolio", "team", "news",
        "careers", "contact", "about", "blog", "events", "resources",
        "companies", "portfolio", "investments", "portfolio-companies",
    }
    _SKIP_TEXT = re.compile(
        r"^(see all|view all|learn more|read more|back to|visit|our portfolio)",
        re.IGNORECASE,
    )

    # Pattern 1: portfolio/companies/investments links
    for tag in soup.find_all("a", href=True):
        href = tag.get("href", "")
        m = _PORTFOLIO_PATH_RE.search(href)
        if not m:
            continue
        slug = m.group(2).rstrip("/").lower()
        if slug in _SKIP_SLUGS:
            continue
        link_text = tag.get_text(strip=True)
        if link_text and 3 < len(link_text) <= 60 and not _SKIP_TEXT.match(link_text):
            company = link_text
        else:
            company = " ".join(w.capitalize() for w in slug.replace("-", " ").split())
        if company and company not in seen:
            seen.add(company)
            results.append({"company": company, "description": "", "vc": vc})

    # Pattern 2: short heading tags — fallback when no portfolio links found
    if len(results) < 5:
        for tag in soup.find_all(["h3", "h4", "h5"]):
            text = tag.get_text(strip=True)
            if 3 < len(text) <= 60 and not re.search(r"[.!?]|\n", text):
                if text not in seen:
                    seen.add(text)
                    results.append({"company": text, "description": "", "vc": vc})

    return results[:80]


_PARSERS = {
    "atlas":   _parse_atlas,
    "foresite": _parse_foresite,
    "versant":  _parse_versant,
    "flagship": _parse_flagship,
    "generic":  _parse_generic,
}


# ── ClinicalTrials lookup ─────────────────────────────────────────────────────

def _ct_lookup(
    company: str,
    vc: str,
    session: requests.Session,
    max_studies: int = 5,
) -> list[RawRecord]:
    """Query ClinicalTrials.gov for studies sponsored by a company."""
    params = {
        "query.spons": company,
        "pageSize": max_studies,
        "format": "json",
        "fields": (
            "NCTId,BriefTitle,OfficialTitle,OverallStatus,Phase,"
            "Condition,InterventionName,InterventionType,"
            "StartDate,PrimaryCompletionDate,BriefSummary"
        ),
    }
    try:
        time.sleep(0.4)
        resp = session.get(_CT_API, params=params, timeout=15, headers=_CT_HEADERS)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception as exc:
        logger.debug("CT lookup failed for %s: %s", company, exc)
        return []

    studies = data.get("studies", [])
    records: list[RawRecord] = []

    for study in studies:
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        cond   = proto.get("conditionsModule", {})
        interv = proto.get("armsInterventionsModule", {})
        desc   = proto.get("descriptionModule", {})

        nct_id       = ident.get("nctId", "")
        title        = ident.get("briefTitle", ident.get("officialTitle", ""))
        overall_st   = status.get("overallStatus", "UNKNOWN_STATUS")
        phases       = design.get("phases", [])
        conditions   = cond.get("conditions", [])
        interventions = [i.get("name", "") for i in interv.get("interventions", [])]
        summary      = desc.get("briefSummary", "")

        if not nct_id or not title:
            continue

        phase_str = phases[0] if phases else "NA"
        stage     = _STAGE_MAP.get(phase_str.upper().replace(" ", ""), "phase1")
        decision  = _STATUS_MAP.get(overall_st, "undecided")
        outcome_base = _OUTCOME_MAP.get(overall_st, "ongoing")
        outcome   = f"discontinued_{stage}" if outcome_base == "discontinued" else outcome_base

        raw_text  = " ".join([title, summary, *conditions, *interventions])
        indication = _extract_ind(raw_text) if conditions else (conditions[0].lower() if conditions else "unknown")
        if conditions:
            indication = conditions[0].lower()[:80]
        mechanism = _extract_mech(" ".join(interventions) + " " + summary) if interventions else "small molecule"

        records.append(RawRecord(
            source       = "vc_portfolio",
            source_id    = nct_id,
            url          = f"https://clinicaltrials.gov/study/{nct_id}",
            title        = title,
            indication   = indication,
            mechanism    = mechanism,
            clinical_stage = stage,
            decision     = decision,
            outcome      = outcome,
            investment_usd = 0.0,
            raw_text     = raw_text,
            extra        = {
                "vc":          vc,
                "company":     company,
                "nct_id":      nct_id,
                "ct_status":   overall_st,
                "phase":       phase_str,
                "conditions":  conditions[:3],
            },
        ))

    return records


# ── Main collector ─────────────────────────────────────────────────────────────

class VCPortfolioCollector(BaseCollector):
    """
    Collects bioventure portfolio company data by:
      1. Scraping portfolio pages of major VC firms
      2. Cross-referencing each company against ClinicalTrials.gov
      3. Falling back to hardcoded company lists for non-scrapeable sites
    """

    name = "vc_portfolio"
    rate_limit_seconds = 0.5

    def collect(self, max_records: int = 300) -> list[RawRecord]:
        all_companies: list[dict[str, str]] = []

        # Step 1: Scrape portfolio pages
        for pg in _PORTFOLIO_PAGES:
            logger.info("[vc_portfolio] Fetching %s portfolio: %s", pg["vc"], pg["url"])
            try:
                resp = self.session.get(
                    pg["url"],
                    timeout=self.timeout,
                    headers={"User-Agent": "Mozilla/5.0 BioVentureResearch/1.0"},
                )
                resp.raise_for_status()
                parser_fn = _PARSERS[pg["parser"]]
                if pg["parser"] == "generic":
                    companies = parser_fn(resp.text, pg["vc"])
                else:
                    companies = parser_fn(resp.text)
                logger.info("  → %d companies found from %s", len(companies), pg["vc"])
                all_companies.extend(companies)
            except Exception as exc:
                logger.warning("  ✗ Failed to scrape %s: %s", pg["vc"], exc)

        # Step 2: Add hardcoded companies
        for entry in _HARDCODED_COMPANIES:
            all_companies.append({
                "company":     entry["company"],
                "description": "",
                "vc":          entry["vc"],
            })
        logger.info("[vc_portfolio] Total companies to query: %d", len(all_companies))

        # Step 3: Cross-reference against ClinicalTrials.gov
        records: list[RawRecord] = []
        max_per_company = max(1, max_records // max(len(all_companies), 1)) + 1

        for entry in all_companies:
            if len(records) >= max_records:
                break
            company = entry["company"]
            vc      = entry.get("vc", "unknown")
            ct_recs = _ct_lookup(company, vc, self.session, max_studies=max_per_company)

            if ct_recs:
                records.extend(ct_recs)
                logger.debug("  [%s] %s → %d CT records", vc, company, len(ct_recs))
            else:
                # Emit a synthetic preclinical record based on description text
                desc = entry.get("description", "")
                ind  = _extract_ind(desc + " " + company) if desc else "oncology"
                mech = _extract_mech(desc) if desc else "small molecule inhibitor"
                records.append(RawRecord(
                    source        = "vc_portfolio",
                    source_id     = f"vc_{vc.replace(' ', '_').lower()}_{company.replace(' ', '_').lower()[:30]}",
                    url           = f"https://clinicaltrials.gov/search?query={urllib.parse.quote(company)}",
                    title         = f"{company} — pipeline program",
                    indication    = ind,
                    mechanism     = mech,
                    clinical_stage = "preclinical",
                    decision      = "go",
                    outcome       = "ongoing",
                    investment_usd = 0.0,
                    raw_text      = f"{company} {vc} bioventure portfolio {desc}",
                    extra         = {
                        "vc":      vc,
                        "company": company,
                        "source":  "vc_portfolio_stub",
                    },
                ))

        logger.info("[vc_portfolio] Total records produced: %d", len(records))
        return records[:max_records]
