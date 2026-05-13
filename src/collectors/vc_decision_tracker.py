from __future__ import annotations

"""
VC Decision Tracker
===================
Tracks *every* decision made by each VC-backed biotech — whether it was a
success or failure.  For each portfolio company the collector emits:

  1. An **exit/status record** — IPO, acquisition, merger, bankruptcy,
     wind-down, or still-active.
  2. One record per **clinical trial** ever run by the company, drawn from
     ClinicalTrials.gov (all statuses: recruiting, completed, terminated,
     withdrawn, suspended).

This gives the ML model complete longitudinal data:
  • Which VC bets paid off (acquired at premium, blockbuster approval)
  • Which VC bets failed (Phase 2 miss, bankrupt, programme terminated)
  • At what clinical stage programmes were abandoned

Data sources
------------
  • _KNOWN_OUTCOMES  — curated ground truth for major exits / failures
  • ClinicalTrials.gov v2 API — full trial history per company
"""

import hashlib
import logging
import re
from typing import Any

import requests

from .base_collector import BaseCollector, RawRecord
from .vc_portfolio_collector import _HARDCODED_COMPANIES

logger = logging.getLogger(__name__)

# ── Curated known outcomes ─────────────────────────────────────────────────────
# Keys are canonical company names matching _HARDCODED_COMPANIES.
# outcome_type: "acquired" | "ipo" | "bankrupt" | "winddown" | "merged" | "active"
# result:       "success" | "failure" | "ongoing"
# deal_size_m:  deal value in USD millions (0 = unknown)

_KNOWN_OUTCOMES: dict[str, dict[str, Any]] = {

    # ── Flagship Pioneering portfolio ─────────────────────────────────────
    "Moderna": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2018,
        "deal_size_m": 0,
        "note": "IPO 2018; COVID-19 mRNA vaccine (Spikevax) approved 2021; ~$18B peak revenue",
        "acquirer": None,
        "approved_drugs": ["Spikevax (mRNA-1273)"],
    },
    "Translate Bio": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2021,
        "deal_size_m": 3200,
        "note": "Acquired by Sanofi $3.2B; mRNA platform for vaccines",
        "acquirer": "Sanofi",
        "approved_drugs": [],
    },
    "Rubius Therapeutics": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2023,
        "deal_size_m": 0,
        "note": "Phase 2 failure in AML/RTX-240; suspended all programmes, wind-down",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Evelo Biosciences": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2023,
        "deal_size_m": 0,
        "note": "Multiple Phase 2 failures (EDP1815 psoriasis, atopic dermatitis); suspended operations",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Kaleido Biosciences": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2021,
        "deal_size_m": 0,
        "note": "Phase 2 failure (KB195 ornithine transcarbamylase deficiency); wound down",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Axcella Health": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2024,
        "deal_size_m": 0,
        "note": "Phase 2 failure (AXA1125 NASH); company dissolved 2024",
        "acquirer": None,
        "approved_drugs": [],
    },
    "EQRx": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2023,
        "deal_size_m": 0,
        "note": "Generic/value oncology pricing model failed commercially; dissolved 2023",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Sienna Biopharmaceuticals": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2019,
        "deal_size_m": 0,
        "note": "All dermatology programmes failed; wound down 2019",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Neon Therapeutics": {
        "outcome_type": "merged",
        "result": "ongoing",
        "year": 2020,
        "deal_size_m": 0,
        "note": "Merged with BioNTech 2020; neoantigen vaccine platform absorbed",
        "acquirer": "BioNTech",
        "approved_drugs": [],
    },
    "Generate Biomedicines": {
        "outcome_type": "active",
        "result": "ongoing",
        "year": None,
        "deal_size_m": 0,
        "note": "Generative AI protein design platform; $370M Series B 2022",
        "acquirer": None,
        "approved_drugs": [],
    },

    # ── Atlas Venture portfolio ───────────────────────────────────────────
    "Blueprint Medicines": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2015,
        "deal_size_m": 0,
        "note": "IPO 2015; FDA approved Ayvakit (avapritinib) 2020 for GIST/SM; Gavreto (pralsetinib) 2020 RET+",
        "acquirer": None,
        "approved_drugs": ["Ayvakit (avapritinib)", "Gavreto (pralsetinib)"],
    },
    "Agios Pharmaceuticals": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2013,
        "deal_size_m": 0,
        "note": "IPO 2013; Tibsovo (ivosidenib IDH1) approved 2018; Voranigo (vorasidenib IDH1/2) approved 2023",
        "acquirer": None,
        "approved_drugs": ["Tibsovo (ivosidenib)", "Voranigo (vorasidenib)"],
    },
    "Editas Medicine": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2016,
        "deal_size_m": 0,
        "note": "IPO 2016; EDIT-101 (CEP290 LCA10) Phase 1/2 slow enrolment; pipeline refocus 2023",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Intellia Therapeutics": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2016,
        "deal_size_m": 0,
        "note": "IPO 2016; NTLA-2001 (ATTR) Phase 3 ongoing; NTLA-2002 (hereditary angioedema) Phase 2",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Kymera Therapeutics": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2020,
        "deal_size_m": 0,
        "note": "IPO 2020; targeted protein degradation PROTAC; KT-474 Phase 2 AHD/psoriasis",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Relay Therapeutics": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2020,
        "deal_size_m": 0,
        "note": "IPO 2020; RLY-4008 (FGFR2) pivotal trial ongoing; RLY-2608 (PI3Kα) Phase 1/2",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Imago BioSciences": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2022,
        "deal_size_m": 1350,
        "note": "Acquired by Merck $1.35B; bomedemstat (LSD1 inhibitor) myeloproliferative neoplasms",
        "acquirer": "Merck",
        "approved_drugs": [],
    },
    "Pandion Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2021,
        "deal_size_m": 1850,
        "note": "Acquired by Merck $1.85B; PT101 localised IL-2 for autoimmune Phase 1",
        "acquirer": "Merck",
        "approved_drugs": [],
    },
    "Vividion Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2021,
        "deal_size_m": 1500,
        "note": "Acquired by Bayer $1.5B; chemoproteomic platform for undruggable targets",
        "acquirer": "Bayer",
        "approved_drugs": [],
    },
    "iTeos Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2024,
        "deal_size_m": 1450,
        "note": "Acquired by GSK $1.45B; EOS-448 (TIGIT) and inupadenant (A2A/A2B) oncology",
        "acquirer": "GSK",
        "approved_drugs": [],
    },

    # ── Third Rock Ventures portfolio ─────────────────────────────────────
    "Karuna Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2024,
        "deal_size_m": 14000,
        "note": "Acquired by BMS $14B; Cobenfy (xanomeline-trospium) approved 2024 schizophrenia — largest psychiatry deal",
        "acquirer": "Bristol Myers Squibb",
        "approved_drugs": ["Cobenfy (xanomeline-trospium)"],
    },
    "Scholar Rock": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2018,
        "deal_size_m": 0,
        "note": "IPO 2018; apitegromab (SMA) Phase 3 positive 2024; potential approval 2025",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Protagonist Therapeutics": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2016,
        "deal_size_m": 0,
        "note": "IPO 2016; Ryckfio (imetelstat) approved FDA 2024 myelodysplastic syndromes",
        "acquirer": None,
        "approved_drugs": ["Ryckfio (imetelstat)"],
    },
    "BlackDiamond Therapeutics": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2023,
        "deal_size_m": 0,
        "note": "Phase 1/2 failure BDTX-189 (EGFR/HER2); restructured, pipeline abandoned 2023",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Disc Medicine": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2023,
        "deal_size_m": 0,
        "note": "IPO 2023 (merged from Imago+Imara); bitopertin (GlyT1) Phase 2/3 erythropoietic protoporphyria",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Karyopharm Therapeutics": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2013,
        "deal_size_m": 0,
        "note": "IPO 2013; Xpovio (selinexor XPO1) approved 2019 multiple myeloma; also approved 2020 DLBCL",
        "acquirer": None,
        "approved_drugs": ["Xpovio (selinexor)"],
    },
    "Foundation Medicine": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2018,
        "deal_size_m": 2400,
        "note": "Acquired by Roche $2.4B; leading comprehensive genomic profiling companion diagnostics",
        "acquirer": "Roche",
        "approved_drugs": [],
    },
    "Aileron Therapeutics": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2022,
        "deal_size_m": 0,
        "note": "Phase 2 failure ALRN-6924 (p53 stapled peptide); company folded 2022",
        "acquirer": None,
        "approved_drugs": [],
    },

    # ── ARCH Venture Partners portfolio ───────────────────────────────────
    "Alnylam Pharmaceuticals": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2004,
        "deal_size_m": 0,
        "note": "IPO 2004; 5 approved RNAi drugs: Onpattro, Givlaari, Oxlumo, Leqvio, Amvuttra",
        "acquirer": None,
        "approved_drugs": ["Onpattro (patisiran)", "Givlaari (givosiran)", "Oxlumo (lumasiran)", "Leqvio (inclisiran)", "Amvuttra (vutrisiran)"],
    },
    "Juno Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2018,
        "deal_size_m": 9000,
        "note": "Acquired by Celgene $9B; CAR-T pioneer; Breyanzi (lisocabtagene maraleucel) later approved",
        "acquirer": "Celgene/BMS",
        "approved_drugs": ["Breyanzi (lisocabtagene maraleucel)"],
    },
    "GRAIL": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2020,
        "deal_size_m": 0,
        "note": "IPO 2020; acquired by Illumina 2021 $8B then forced divestiture 2024 by FTC; Galleri multi-cancer liquid biopsy",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Recursion Pharmaceuticals": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2021,
        "deal_size_m": 0,
        "note": "IPO 2021; AI drug discovery; REC-994 Phase 2 CCM; Tempus acquisition 2024",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Sana Biotechnology": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2021,
        "deal_size_m": 0,
        "note": "IPO 2021; hypoimmune cell engineering; SAN-903 Phase 1; significant cash burn",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Prime Medicine": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2022,
        "deal_size_m": 0,
        "note": "IPO 2022; prime editing (David Liu); PM359 Phase 1 chronic granulomatous disease 2024",
        "acquirer": None,
        "approved_drugs": [],
    },
    "C4 Therapeutics": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2020,
        "deal_size_m": 0,
        "note": "IPO 2020; targeted protein degradation; CFT8919 (EGFR L858R) Phase 1; CFT1946 BRAF Phase 1",
        "acquirer": None,
        "approved_drugs": [],
    },

    # ── RA Capital portfolio ───────────────────────────────────────────────
    "Passage Bio": {
        "outcome_type": "ipo",
        "result": "failure",
        "year": 2020,
        "deal_size_m": 0,
        "note": "IPO 2020; PBGM01 (GM1 gangliosidosis) Phase 1/2 failed 2022; PASB01 ongoing",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Merus": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2016,
        "deal_size_m": 0,
        "note": "IPO 2016; Bizengri (zenocutuzumab NRG1+) approved 2024 NSCLC/pancreatic — first NRG1+ approval",
        "acquirer": None,
        "approved_drugs": ["Bizengri (zenocutuzumab)"],
    },
    "Arrowhead Pharmaceuticals": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 0,
        "deal_size_m": 0,
        "note": "Listed NASDAQ; plozasiran (TG) approved 2024; multiple RNAi programmes Phase 2/3",
        "acquirer": None,
        "approved_drugs": ["plozasiran"],
    },
    "SpringWorks Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2023,
        "deal_size_m": 6700,
        "note": "Acquired by Pfizer $6.7B; Ogsiveo (nirogacestat γ-secretase) approved 2023 desmoid tumors",
        "acquirer": "Pfizer",
        "approved_drugs": ["Ogsiveo (nirogacestat)"],
    },
    "Arcus Biosciences": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2018,
        "deal_size_m": 0,
        "note": "IPO 2018; domvanalimab (TIGIT) Phase 3 NSCLC; Gilead declined further options 2024",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Arvinas": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2018,
        "deal_size_m": 0,
        "note": "IPO 2018; ARV-471 (ERα PROTAC) Phase 3 breast cancer; ARV-766 Phase 2 prostate",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Alector": {
        "outcome_type": "ipo",
        "result": "failure",
        "year": 2019,
        "deal_size_m": 0,
        "note": "IPO 2019; AL002 (TREM2) Phase 2 AD failed 2024; AL044 discontinued; pipeline in doubt",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Dyne Therapeutics": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2020,
        "deal_size_m": 0,
        "note": "IPO 2020; DYNE-101 (DM1 myotonic dystrophy) Phase 1/2; FORCE platform muscle delivery",
        "acquirer": None,
        "approved_drugs": [],
    },

    # ── 5AM Ventures portfolio ─────────────────────────────────────────────
    "Turning Point Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2022,
        "deal_size_m": 4100,
        "note": "Acquired by BMS $4.1B; repotrectinib (ROS1/NTRK) approved FDA 2023 Augtyro",
        "acquirer": "Bristol Myers Squibb",
        "approved_drugs": ["Augtyro (repotrectinib)"],
    },
    "Chinook Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2023,
        "deal_size_m": 3500,
        "note": "Acquired by Novartis $3.5B; atrasentan Phase 3 IgA nephropathy",
        "acquirer": "Novartis",
        "approved_drugs": [],
    },
    "Janux Therapeutics": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2021,
        "deal_size_m": 0,
        "note": "IPO 2021; JANX007 (PSMA×CD3) Phase 1 prostate; JANX008 (HER2) Phase 1",
        "acquirer": None,
        "approved_drugs": [],
    },
    "CG Oncology": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2024,
        "deal_size_m": 0,
        "note": "IPO 2024; cretostimogene oncolytic virus bladder cancer Phase 3",
        "acquirer": None,
        "approved_drugs": [],
    },

    # ── Bain Capital Life Sciences ──────────────────────────────────────────
    "Fusion Pharmaceuticals": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2024,
        "deal_size_m": 2000,
        "note": "Acquired by AstraZeneca $2B; FPI-2265 (PSMA) Phase 2/3 prostate cancer",
        "acquirer": "AstraZeneca",
        "approved_drugs": [],
    },
    "Morphic Therapeutic": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2024,
        "deal_size_m": 3200,
        "note": "Acquired by Eli Lilly $3.2B; MORF-057 (αvβ6 integrin) Phase 2 IBD",
        "acquirer": "Eli Lilly",
        "approved_drugs": [],
    },

    # ── OrbiMed portfolio ──────────────────────────────────────────────────
    "Ra Pharmaceuticals": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2021,
        "deal_size_m": 2300,
        "note": "Acquired by UCB $2.3B; zilucoplan (C5 inhibitor) approved 2023 gMG (Zilbrysq)",
        "acquirer": "UCB",
        "approved_drugs": ["Zilbrysq (zilucoplan)"],
    },
    "Achaogen": {
        "outcome_type": "bankrupt",
        "result": "failure",
        "year": 2019,
        "deal_size_m": 0,
        "note": "Bankruptcy 2019; plazomicin (Zemdri) approved 2018 for cUTI but commercial failure — antibiotic market access failure",
        "acquirer": None,
        "approved_drugs": ["Zemdri (plazomicin)"],
    },
    "Arena Pharmaceuticals": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2022,
        "deal_size_m": 6700,
        "note": "Acquired by Pfizer $6.7B; etrasimod (S1P1) approved 2023 UC (Velsipity); ralinepag Phase 3",
        "acquirer": "Pfizer",
        "approved_drugs": ["Velsipity (etrasimod)"],
    },
    "Harmony Biosciences": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2021,
        "deal_size_m": 0,
        "note": "IPO 2021; Wakix (pitolisant) approved 2019 narcolepsy (licensed from Bioprojet)",
        "acquirer": None,
        "approved_drugs": ["Wakix (pitolisant)"],
    },
    "Alector": {
        "outcome_type": "ipo",
        "result": "failure",
        "year": 2019,
        "deal_size_m": 0,
        "note": "IPO 2019; TREM2/progranulin programmes failed Phase 2; pipeline significantly restructured",
        "acquirer": None,
        "approved_drugs": [],
    },

    # ── Novo Holdings portfolio ─────────────────────────────────────────────
    "Inversago Pharma": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2023,
        "deal_size_m": 1075,
        "note": "Acquired by Novo Nordisk $1.075B; INV-202 (CB1 inverse agonist) Phase 2 kidney disease/obesity",
        "acquirer": "Novo Nordisk",
        "approved_drugs": [],
    },
    "Zealand Pharma": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2010,
        "deal_size_m": 0,
        "note": "Listed Copenhagen; petrelintide (amylin) Phase 2b obesity; dasiglucagon approved",
        "acquirer": None,
        "approved_drugs": ["Zegalogue (dasiglucagon)"],
    },

    # ── Deerfield Management portfolio ─────────────────────────────────────
    "Principia Biopharma": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2020,
        "deal_size_m": 3700,
        "note": "Acquired by Sanofi $3.7B; tolebrutinib (BTKi) Phase 3 MS/SLE — NDA submitted 2024",
        "acquirer": "Sanofi",
        "approved_drugs": [],
    },
    "Esperion Therapeutics": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2013,
        "deal_size_m": 0,
        "note": "IPO 2013; Nexletol (bempedoic acid) approved 2020 LDL; Nexlizet (bempedoic acid+ezetimibe) approved 2020",
        "acquirer": None,
        "approved_drugs": ["Nexletol (bempedoic acid)", "Nexlizet (bempedoic acid/ezetimibe)"],
    },
    "Corcept Therapeutics": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2004,
        "deal_size_m": 0,
        "note": "IPO 2004; Korlym (mifepristone) approved 2012 Cushing's syndrome; profitable commercial-stage",
        "acquirer": None,
        "approved_drugs": ["Korlym (mifepristone)"],
    },

    # ── Polaris Partners portfolio ──────────────────────────────────────────
    "Forma Therapeutics": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2022,
        "deal_size_m": 1100,
        "note": "Acquired by Novo Nordisk $1.1B; olutasidenib (IDH1) approved 2022 AML (Rezlidhia)",
        "acquirer": "Novo Nordisk",
        "approved_drugs": ["Rezlidhia (olutasidenib)"],
    },

    # ── RA Capital / multi-VC ──────────────────────────────────────────────
    "Boundless Bio": {
        "outcome_type": "acquired",
        "result": "success",
        "year": 2024,
        "deal_size_m": 600,
        "note": "Acquired by Jazz Pharmaceuticals $600M; BBO-8520 (extrachromosomal DNA) Phase 1",
        "acquirer": "Jazz Pharmaceuticals",
        "approved_drugs": [],
    },
    "Day One Pharmaceuticals": {
        "outcome_type": "ipo",
        "result": "success",
        "year": 2021,
        "deal_size_m": 0,
        "note": "IPO 2021; Ojemda (tovorafenib BRAF) approved 2024 pediatric low-grade glioma",
        "acquirer": None,
        "approved_drugs": ["Ojemda (tovorafenib)"],
    },

    # ── ARCH Venture / multi-VC ────────────────────────────────────────────
    "Neumora Therapeutics": {
        "outcome_type": "ipo",
        "result": "failure",
        "year": 2023,
        "deal_size_m": 0,
        "note": "IPO 2023; navacaprant (OPRK1) Phase 3 MDD failed 2024; stock collapsed >70%",
        "acquirer": None,
        "approved_drugs": [],
    },

    # ── Canaan Partners portfolio ───────────────────────────────────────────
    "Genocea Biosciences": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2022,
        "deal_size_m": 0,
        "note": "Wind-down 2022; GEN-009 neoantigen vaccine failed; GEN-011 discontinued after Phase 2 miss",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Kronos Bio": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2023,
        "deal_size_m": 0,
        "note": "Wind-down 2023; entospletinib (SYK) Phase 2 AML failed; all programmes discontinued",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Monte Rosa Therapeutics": {
        "outcome_type": "ipo",
        "result": "ongoing",
        "year": 2021,
        "deal_size_m": 0,
        "note": "IPO 2021; MRT-2359 (GSPT1 molecular glue) Phase 1/2 NSCLC; QuEEN platform",
        "acquirer": None,
        "approved_drugs": [],
    },

    # ── Multi-VC ────────────────────────────────────────────────────────────
    "Eliem Therapeutics": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2023,
        "deal_size_m": 0,
        "note": "ETX-810 (Nav1.7/Nav1.8) Phase 2 trigeminal neuralgia failed 2023; wound down",
        "acquirer": None,
        "approved_drugs": [],
    },
    "Aikar Pharmaceuticals": {
        "outcome_type": "winddown",
        "result": "failure",
        "year": 2021,
        "deal_size_m": 0,
        "note": "Phase 2 failure; wound down",
        "acquirer": None,
        "approved_drugs": [],
    },
}

# ── ClinicalTrials.gov v2 ───────────────────────────────────────────────────

_CT_API = "https://clinicaltrials.gov/api/v2/studies"

_PHASE_MAP = {
    "PHASE1":       "phase1",
    "PHASE2":       "phase2",
    "PHASE3":       "phase3",
    "PHASE4":       "approved",
    "EARLY_PHASE1": "phase1",
    "NA":           "preclinical",
}

_STATUS_DECISION = {
    "TERMINATED":            "no-go",
    "WITHDRAWN":             "no-go",
    "SUSPENDED":             "no-go",
    "COMPLETED":             "go",
    "ACTIVE_NOT_RECRUITING": "go",
    "RECRUITING":            "go",
    "NOT_YET_RECRUITING":    "go",
    "UNKNOWN_STATUS":        "undecided",
}

_STATUS_OUTCOME = {
    "TERMINATED":            "discontinued",
    "WITHDRAWN":             "discontinued",
    "SUSPENDED":             "discontinued",
    "COMPLETED":             "completed",
    "ACTIVE_NOT_RECRUITING": "ongoing",
    "RECRUITING":            "ongoing",
    "NOT_YET_RECRUITING":    "ongoing",
    "UNKNOWN_STATUS":        "unknown",
}

_IND_RE = re.compile(
    r"\b(cancer|carcinoma|oncology|leukemia|lymphoma|melanoma|glioblastoma|glioma|"
    r"rare disease|autoimmune|rheumatoid|lupus|crohn|colitis|IBD|"
    r"neurology|alzheimer|parkinson|ALS|multiple sclerosis|CNS|depression|schizophrenia|"
    r"cardiovascular|heart failure|hypertension|"
    r"metabolic|diabetes|obesity|NASH|fatty liver|"
    r"infectious|HIV|hepatitis|influenza|COVID|"
    r"inflammation|psoriasis|atopic dermatitis|eczema|"
    r"hematology|sickle cell|hemophilia|MDS|AML|myeloma|"
    r"renal|kidney|pulmonary|fibrosis|"
    r"solid tumor|tumor|neoplasm|sarcoma|NSCLC|lung cancer|breast cancer|prostate|"
    r"angioedema|narcolepsy|hypersomnia|Cushing|"
    r"gene therapy|cell therapy)\b",
    re.IGNORECASE,
)

_MECH_RE = re.compile(
    r"\b(antibody|monoclonal|bispecific|ADC|conjugate|"
    r"small molecule|inhibitor|kinase|checkpoint|PD.?1|PD.?L1|CTLA.?4|TIGIT|"
    r"cell therapy|CAR.?T|T.?cell|NK cell|"
    r"gene therapy|CRISPR|prime editing|AAV|lentiviral|"
    r"RNA|siRNA|mRNA|antisense|oligonucleotide|RNAi|"
    r"PROTAC|degrader|molecular glue|"
    r"enzyme|protein|peptide|vaccine|immunotherapy|oncolytic)\b",
    re.IGNORECASE,
)


def _ext_ind(text: str) -> str:
    m = _IND_RE.search(text)
    return m.group(0).lower() if m else "unknown"


def _ext_mech(text: str) -> str:
    m = _MECH_RE.search(text)
    return m.group(0).lower() if m else "unknown"


# ── Investment thesis — why each company got funded + what the killer tech was ─
# Fields:
#   killer_tech:       the core platform/mechanism that drove investor excitement
#   thesis:            one-sentence investment rationale
#   differentiation:   why this was better than existing treatments
#   platform:          True if the value is a reusable platform (not single drug)
#   lead_investor:     VC firm that led the round or was founding investor

_INVESTMENT_THESIS: dict[str, dict] = {

    # ── Platform / technology bets ────────────────────────────────────────
    "Moderna": {
        "killer_tech": "mRNA lipid nanoparticle delivery — programmable protein expression without DNA integration",
        "thesis": "A single manufacturing platform could generate any protein-based vaccine or therapeutic rapidly",
        "differentiation": "No cold-chain DNA; no viral vector immunogenicity; rapid sequence-to-IND in weeks",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Alnylam Pharmaceuticals": {
        "killer_tech": "RNA interference (RNAi) — GalNAc-siRNA hepatic delivery with quarterly dosing",
        "thesis": "Silencing disease-causing genes directly, not just blocking their protein products",
        "differentiation": "Addresses targets completely undruggable by small molecules or antibodies; durable effect from rare dosing",
        "platform": True,
        "lead_investor": "Arch Venture Partners",
    },
    "Intellia Therapeutics": {
        "killer_tech": "In vivo CRISPR-Cas9 gene editing — permanent one-cut cure via LNP delivery to liver",
        "thesis": "One treatment permanently knocks out disease gene — no chronic dosing",
        "differentiation": "First company to demonstrate in vivo CRISPR editing in humans (ATTR 2021)",
        "platform": True,
        "lead_investor": "Atlas Venture",
    },
    "Editas Medicine": {
        "killer_tech": "CRISPR-Cas9 ocular gene editing — subretinal injection to correct LCA10",
        "thesis": "CRISPR correction of CEP290 mutation in photoreceptors to restore vision",
        "differentiation": "First CRISPR medicine to enter human trials (EDIT-101)",
        "platform": False,
        "lead_investor": "Atlas Venture",
    },
    "Prime Medicine": {
        "killer_tech": "Prime editing — 'search-and-replace' DNA editing without double-strand breaks",
        "thesis": "Correct >90% of disease-causing mutations vs ~10% addressable by base editing",
        "differentiation": "No DSBs eliminates translocations; installs precise corrections not possible with Cas9",
        "platform": True,
        "lead_investor": "Arch Venture Partners",
    },
    "Translate Bio": {
        "killer_tech": "mRNA delivery optimised for pulmonary and liver targets",
        "thesis": "mRNA for CF (CFTR) and rare liver diseases without the manufacturing cost of gene therapy",
        "differentiation": "Non-integrating, re-dosable, no immune memory against delivery vehicle",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Generate Biomedicines": {
        "killer_tech": "Generative AI protein design — diffusion models trained on 700M protein sequences",
        "thesis": "Design novel proteins with optimal function from scratch; eliminate years of hit-to-lead",
        "differentiation": "First generative AI-native drug company with compute-designed biologics in the clinic",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Recursion Pharmaceuticals": {
        "killer_tech": "Morphological cell imaging + AI — phenotypic screening at scale using ML on microscopy",
        "thesis": "Map biology at scale and discover drug-target relationships missed by hypothesis-driven research",
        "differentiation": "High-throughput imaging across 2M+ compound-cell combinations; AI predicts MoA",
        "platform": True,
        "lead_investor": "Arch Venture Partners",
    },

    # ── Targeted oncology / precision medicine ────────────────────────────
    "Blueprint Medicines": {
        "killer_tech": "Kinase structure-based design targeting PDGFRA D842V, KIT, RET — historically undruggable mutants",
        "thesis": "Exact structural reasons prior KIT inhibitors failed could be solved by rational design",
        "differentiation": "Avapritinib 400x more potent on D842V GIST than imatinib; first drug to work",
        "platform": False,
        "lead_investor": "Atlas Venture",
    },
    "Turning Point Therapeutics": {
        "killer_tech": "Macrocyclic kinase inhibitors — lock the drug into inactive DFG-out conformation of ROS1/NTRK",
        "thesis": "Overcome resistance to first-generation ROS1/NTRK inhibitors via distinct binding mode",
        "differentiation": "Repotrectinib 10–100x more potent than crizotinib; active against solvent-front mutations",
        "platform": False,
        "lead_investor": "5AM Ventures",
    },
    "Agios Pharmaceuticals": {
        "killer_tech": "IDH1/IDH2 mutant inhibitors — cancer metabolism targeting oncometabolite 2-HG",
        "thesis": "IDH mutations create a druggable neomorphic enzyme activity absent in normal cells",
        "differentiation": "First drugs targeting oncometabolite; differentiation therapy rather than cytotoxicity",
        "platform": False,
        "lead_investor": "Atlas Venture",
    },
    "Relay Therapeutics": {
        "killer_tech": "Dynamo platform — computational conformational sampling of protein motion to find cryptic pockets",
        "thesis": "Proteins are dynamic; binding sites exist in transient states invisible to X-ray crystallography",
        "differentiation": "RLY-4008 first selective FGFR2 inhibitor without FGFR1-driven toxicity; RLY-2608 mutant-selective PI3Kα",
        "platform": True,
        "lead_investor": "Atlas Venture",
    },
    "Kymera Therapeutics": {
        "killer_tech": "PROTAC protein degradation — bifunctional molecules recruit E3 ligase to destroy disease proteins",
        "thesis": "Degrade, don't just inhibit; works on transcription factors and other 'undruggable' proteins",
        "differentiation": "Catalytic mechanism means sub-stoichiometric drug concentration; overcomes resistance by mutation",
        "platform": True,
        "lead_investor": "Atlas Venture",
    },
    "C4 Therapeutics": {
        "killer_tech": "Degronimids (molecular glues + PROTACs) — cereblon-based targeted protein degradation",
        "thesis": "Protein degradation as a new pharmacological modality for oncology targets",
        "differentiation": "Access non-enzymatic oncoproteins (IKZF1, GSPT1) impossible to block with inhibitors",
        "platform": True,
        "lead_investor": "Arch Venture Partners",
    },
    "Monte Rosa Therapeutics": {
        "killer_tech": "QuEEN platform — molecular glue discovery using cryo-EM + DEL to find neo-substrates",
        "thesis": "Systematic discovery of molecular glues (not just accidental as with lenalidomide)",
        "differentiation": "MRT-2359 first designed GSPT1 glue; targets n-MYC driven SCLC/NSCLC",
        "platform": True,
        "lead_investor": "Canaan Partners",
    },
    "Day One Pharmaceuticals": {
        "killer_tech": "Tovorafenib — type II pan-RAF inhibitor designed for weekly oral dosing in pediatric glioma",
        "thesis": "BRAF-altered low-grade glioma is the most common pediatric brain tumour; no approved oral targeted therapy existed",
        "differentiation": "CNS-penetrant; weekly dosing aligned with chemotherapy schedules; no cutaneous toxicity of vemurafenib",
        "platform": False,
        "lead_investor": "RA Capital",
    },
    "Janux Therapeutics": {
        "killer_tech": "TRACTr (Tumor-activated T-cell Engager) — prodrug bispecific that only activates in tumour microenvironment",
        "thesis": "CD3 bispecifics fail in solid tumours due to on-target/off-tumour T-cell activation (cytokines); activation only in tumour solves this",
        "differentiation": "PSMA×CD3 with protease-cleavable mask; 10-100x higher therapeutic index vs blinatumomab-class",
        "platform": True,
        "lead_investor": "5AM Ventures",
    },
    "Merus": {
        "killer_tech": "Biclonics bispecific antibody — common light chain technology enabling stable bispecific manufacturing",
        "thesis": "NRG1 fusion cancers (2–5% of NSCLC/pancreatic) have no targeted therapy; ErbB2/ErbB3 bispecific blocks NRG1 signalling",
        "differentiation": "Zenocutuzumab approved 2024 — first drug ever for NRG1+ cancers; IHC companion diagnostic",
        "platform": True,
        "lead_investor": "Canaan Partners",
    },
    "Arvinas": {
        "killer_tech": "PROTAC — first company to advance ERα and AR protein degraders into Phase 3",
        "thesis": "Degrade the entire ER protein including the ligand-binding domain mutants that cause endocrine resistance",
        "differentiation": "ARV-471 degrades ERα regardless of ESR1 mutation; works where fulvestrant/CDK4/6 fail",
        "platform": True,
        "lead_investor": "RA Capital",
    },
    "Morphic Therapeutic": {
        "killer_tech": "Oral integrin inhibitor — small molecule αvβ6 blocker with organ selectivity for IBD fibrosis",
        "thesis": "αvβ6 integrin activates TGF-β in gut epithelium; blocking it reverses fibrosis without systemic immunosuppression",
        "differentiation": "Oral vs IV vedolizumab; organ-selective avoids αvβ6 pulmonary toxicity (seen with peptide inhibitors)",
        "platform": False,
        "lead_investor": "Polaris Partners",
    },

    # ── Gene/cell therapy ─────────────────────────────────────────────────
    "Juno Therapeutics": {
        "killer_tech": "CAR-T cell therapy — autologous CD19-targeting T cells for B-cell malignancies",
        "thesis": "Retraining the patient's own immune system to recognise and destroy cancer cells",
        "differentiation": "Second-generation CARs with 4-1BB costimulatory domain for superior persistence vs CD28",
        "platform": True,
        "lead_investor": "Arch Venture Partners",
    },
    "Sana Biotechnology": {
        "killer_tech": "Hypoimmune (HIP) cell engineering — universal donor cells that evade immune rejection without immunosuppression",
        "thesis": "Off-the-shelf allogeneic cell therapy without rejection; enables scale and cost reduction vs autologous",
        "differentiation": "CD47 overexpression + B2M/CIITA KO to dodge both NK and T-cell killing",
        "platform": True,
        "lead_investor": "Arch Venture Partners",
    },
    "Rubius Therapeutics": {
        "killer_tech": "Red cell therapeutics (RCT) — engineer enucleated red blood cells to carry enzymes or immune cargos",
        "thesis": "RBCs circulate 120 days, lack nuclei (no gene expression risk), scalable manufacturing from HSCs",
        "differentiation": "RTX-240 armed RBCs deliver 4-1BBL + IL-15 to tumour-infiltrating T cells",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Editas Medicine": {
        "killer_tech": "Subretinal CRISPR editing — in vivo AAV5 delivery of SaCas9 to photoreceptors",
        "thesis": "CEP290 IVS26 mutation in LCA10 is a 1-nucleotide change correctable by a single guide RNA",
        "differentiation": "Permanent correction vs voretigene neparvovec (replaces, not corrects; different disease)",
        "platform": False,
        "lead_investor": "Atlas Venture",
    },
    "Passage Bio": {
        "killer_tech": "One-time gene therapy for lysosomal storage diseases via cisterna magna AAV delivery",
        "thesis": "Intracisternal AAV9 achieves broad CNS distribution unreachable by IV at safe doses",
        "differentiation": "IC delivery reduces required dose 30x vs IV; lower manufacturing cost and immunogenicity",
        "platform": False,
        "lead_investor": "Bain Capital Life Sciences",
    },

    # ── Autoimmune / inflammation ─────────────────────────────────────────
    "Principia Biopharma": {
        "killer_tech": "Non-covalent BTK inhibitor — overcomes C481S resistance mutation that breaks ibrutinib/acalabrutinib",
        "thesis": "BTK inhibition durably effective in CLL/MS/lupus; resistance is an inevitable clinical problem to solve",
        "differentiation": "Tolebrutinib CNS-penetrant for MS; tolebrutinib reversible binding avoids off-target covalent effects",
        "platform": False,
        "lead_investor": "Deerfield Management",
    },
    "Alector": {
        "killer_tech": "Neuroinflammation — TREM2 agonist and progranulin replacement for Alzheimer's microglia restoration",
        "thesis": "Alzheimer's has a neuroinflammation component driven by dysfunctional microglia; restoring TREM2 signalling could arrest progression",
        "differentiation": "First TREM2-targeting approach; disease-stage agnostic (not amyloid-dependent)",
        "platform": False,
        "lead_investor": "OrbiMed",
    },
    "Ra Pharmaceuticals": {
        "killer_tech": "Pepducin + complement inhibitor — zilucoplan cyclic peptide blocks C5 cleavage with subcutaneous dosing",
        "thesis": "Complement C5 is proven in PNH (eculizumab); self-administered subQ zilucoplan could dominate gMG market",
        "differentiation": "Weekly self-injection vs eculizumab 2-weekly IV infusion; phase 3 faster than ravulizumab",
        "platform": False,
        "lead_investor": "OrbiMed",
    },
    "SpringWorks Therapeutics": {
        "killer_tech": "γ-secretase inhibitor — nirogacestat blocks Notch3 processing in desmoid tumor stroma",
        "thesis": "Desmoid tumors are Notch-driven and have no approved systemic therapy",
        "differentiation": "First and only FDA-approved systemic therapy for desmoid tumors (orphan, no competition)",
        "platform": False,
        "lead_investor": "RA Capital",
    },
    "Arena Pharmaceuticals": {
        "killer_tech": "S1P1 receptor modulation — etrasimod selective vs fingolimod (no bradycardia, rapid washout)",
        "thesis": "S1P1 modulators sequester lymphocytes in lymph nodes; UC lacks approved oral lymphocyte-trapping agent",
        "differentiation": "Etrasimod has 10h half-life (vs fingolimod 6–9 days); safer cardiac profile; no ophthalmic monitoring",
        "platform": False,
        "lead_investor": "OrbiMed",
    },
    "Protagonist Therapeutics": {
        "killer_tech": "Peptide chemistry — oral miniaturised imetelstat (telomerase inhibitor) for MDS/myelofibrosis",
        "thesis": "Imetelstat suppresses aberrant HSC clones in MDS; IV formulation had risk; oral dosing could expand use",
        "differentiation": "Only drug approved for transfusion-dependent low-risk MDS since azacitidine era",
        "platform": False,
        "lead_investor": "Third Rock Ventures",
    },

    # ── Failures ──────────────────────────────────────────────────────────
    "Karuna Therapeutics": {
        "killer_tech": "Muscarinic agonist (xanomeline) + peripheral antagonist (trospium) — activates brain M1/M4 without cholinergic side effects",
        "thesis": "Schizophrenia has a cholinergic deficit; M1/M4 agonism improves cognition and psychosis without D2 blockade",
        "differentiation": "First non-dopaminergic mechanism approved for schizophrenia in 70 years",
        "platform": False,
        "lead_investor": "Third Rock Ventures",
    },
    "Rubius Therapeutics": {
        "killer_tech": "RTX-240 armed RBC — 4-1BBL + IL-15 displayed on red cell surface for in vivo T-cell costimulation",
        "thesis": "Systemic T-cell agonism is too toxic; displaying agonist on circulating RBCs creates depot restricted to vasculature",
        "differentiation": "Avoids IL-15 systemic toxicity by keeping the costimulatory signal in the blood compartment",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Evelo Biosciences": {
        "killer_tech": "Extracellular vesicles from commensal bacteria (EDP1815 Prevotella histicola) as oral immunomodulators",
        "thesis": "Gut microbiome modulates systemic immune homeostasis via dendritic cell signalling in Peyer's patches",
        "differentiation": "Oral, non-living, non-antibiotic bacterial product with manufacturing scale-up potential",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Kaleido Biosciences": {
        "killer_tech": "Glycan-based microbiome modulators — defined prebiotic structures to select specific bacterial populations",
        "thesis": "Ammonia detoxification by gut bacteria can supplement or replace nitrogen scavenging drugs in UCDs",
        "differentiation": "Food-grade glycans, no live organisms, stable manufacturing; safer than FMT",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Axcella Health": {
        "killer_tech": "Multi-targeted amino acid therapeutics — amino acid combinations targeting mitochondrial metabolism in NASH",
        "thesis": "NASH has complex aetiology; multi-hit amino acid combinations address multiple metabolic pathways simultaneously",
        "differentiation": "Endogenous metabolites with no toxicology concern; could be combined with any other therapy",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "BlackDiamond Therapeutics": {
        "killer_tech": "Master Tumor Driver (MTD) inhibitors — EGFR/HER2/HER4 allosteric inhibitors targeting exon20 insertions",
        "thesis": "HER2 exon20 insertions are the second-most common NSCLC mutation with no approved targeted therapy at founding",
        "differentiation": "Allosteric binding avoids the paradoxical activation seen with ATP-competitive drugs on exon20",
        "platform": True,
        "lead_investor": "Third Rock Ventures",
    },
    "Achaogen": {
        "killer_tech": "Aminoglycoside next-gen — plazomicin overcomes all AME-mediated aminoglycoside resistance via chemical modification",
        "thesis": "CRE/KPC carbapenem-resistant Enterobacteriaceae is an unmet need with no approved drug",
        "differentiation": "Plazomicin active against isolates resistant to gentamicin, tobramycin, amikacin",
        "platform": False,
        "lead_investor": "OrbiMed",
    },
    "Aileron Therapeutics": {
        "killer_tech": "Stapled peptides — hydrocarbon-stitched alpha-helical mimetics that penetrate cells and hit MDM2/MDMX",
        "thesis": "p53 is mutated or MDM2-amplified in >50% of cancers; ALRN-6924 reactivates wild-type p53 in MDM2hi tumours",
        "differentiation": "First cell-permeable peptide against intracellular PPI; addresses liposarcoma/haematological malignancies",
        "platform": True,
        "lead_investor": "Third Rock Ventures",
    },
    "EQRx": {
        "killer_tech": "Value-priced oncology biologics — biosimilar-like manufacturing of branded oncology drugs at 50–80% lower price",
        "thesis": "Drug prices are the dominant barrier to access; high-quality biologics can be made for a fraction of the list price",
        "differentiation": "End-to-end global manufacturing; commercial-stage partnerships in China (CStone) for fast access",
        "platform": False,
        "lead_investor": "Flagship Pioneering",
    },
    "Neumora Therapeutics": {
        "killer_tech": "Precision psychiatry — biomarker-stratified selection of MDD patients with elevated dynorphin/KOR activity",
        "thesis": "MDD is biologically heterogeneous; kappa-opioid receptor antagonism (navacaprant) works only in the dynorphin-high subtype",
        "differentiation": "First biomarker-driven psychiatry trial; plasma dynorphin as companion diagnostic",
        "platform": True,
        "lead_investor": "Arch Venture Partners",
    },
    "Genocea Biosciences": {
        "killer_tech": "ATLAS neoantigen identification — PBMC-based assay to find immunogenic neoantigens for personalised vaccines",
        "thesis": "T-cell responses to neoantigens can eliminate tumours; personalised vaccines will outperform shared antigen vaccines",
        "differentiation": "ATLAS directly measures antigen immunogenicity in patient T cells vs in silico prediction",
        "platform": True,
        "lead_investor": "Canaan Partners",
    },
    "Neon Therapeutics": {
        "killer_tech": "Neoantigen long peptide vaccine + next-gen sequencing — personalised tumour vaccines synthesised within 3 months",
        "thesis": "Tumour-specific neoantigens are recognised by T cells; personalised vaccine + checkpoint inhibitor eliminates residual disease",
        "differentiation": "GMP peptide synthesis at scale; HLA-agnostic; combination with pembrolizumab in Phase 1b",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Sienna Biopharmaceuticals": {
        "killer_tech": "Dermatology topicals with restricted systemic exposure — calcineurin inhibitors and JAK inhibitors formulated for skin",
        "thesis": "Topical JAK/calcineurin inhibitors can match systemic efficacy without systemic immunosuppression",
        "differentiation": "Local action, proprietary microparticle formulation limiting absorption",
        "platform": True,
        "lead_investor": "Flagship Pioneering",
    },
    "Imago BioSciences": {
        "killer_tech": "LSD1 inhibitor — bomedemstat blocks lysine demethylase 1A to differentiate malignant stem cells in MPNs",
        "thesis": "MPN progenitor cells express LSD1; inhibition forces differentiation and reduces mutant clone burden",
        "differentiation": "Oral, not thrombocytopenic at therapeutic doses unlike prior LSD1 inhibitors",
        "platform": False,
        "lead_investor": "Atlas Venture",
    },
    "Scholar Rock": {
        "killer_tech": "Myostatin/GDF11 selective inhibition — apitegromab binds latent myostatin without cross-reacting with activin A",
        "thesis": "Muscle atrophy in SMA is partially myostatin-driven; blocking it on top of nusinersen/risdiplam amplifies motor benefit",
        "differentiation": "First antibody that blocks latent myostatin selectively; no activin A cross-reactivity (avoids the follistatin toxicity problem)",
        "platform": True,
        "lead_investor": "Third Rock Ventures",
    },
    "Pandion Therapeutics": {
        "killer_tech": "Tissue-localised IL-2 fusion — PT101 anchors IL-2 to inflamed tissue to expand Tregs locally without systemic toxicity",
        "thesis": "IL-2 is the most potent Treg expander but systemic VLS/eosinophilia limits dose; tissue anchor solves this",
        "differentiation": "10–50x therapeutic index improvement vs systemic IL-2; autoimmune not oncology (Treg not Teff expansion)",
        "platform": True,
        "lead_investor": "Atlas Venture",
    },
    "Vividion Therapeutics": {
        "killer_tech": "Chemoproteomic fragment screening — activity-based protein profiling to find cryptic cysteines on >10,000 proteins",
        "thesis": "75% of the proteome lacks known ligandable sites; chemoproteomics maps all covalent binding opportunities simultaneously",
        "differentiation": "Platform identifies covalent pockets invisible to structural biology; first systematic approach to undruggable proteome",
        "platform": True,
        "lead_investor": "Atlas Venture",
    },
    "Inversago Pharma": {
        "killer_tech": "Peripheral-restricted CB1 inverse agonist — INV-202 blocks endocannabinoid receptor in kidney/adipose without CNS effects",
        "thesis": "CB1 is activated in diabetic nephropathy and drives glomerular injury; rimonabant proved the biology but failed due to CNS access",
        "differentiation": "Restricted CNS exposure (<1% brain penetration) eliminates the psychiatric side effects that killed rimonabant",
        "platform": False,
        "lead_investor": "Novo Holdings",
    },
    "Fusion Pharmaceuticals": {
        "killer_tech": "Targeted alpha therapy (TAT) — actinium-225 linked to PSMA antibody for prostate cancer",
        "thesis": "Alpha particles have sub-cell range; PSMA-targeted alpha kills tumour cells while sparing surrounding tissue far more than beta (lutetium)",
        "differentiation": "3–7x higher linear energy transfer than lutetium-PSMA; single or two cycles may suffice",
        "platform": True,
        "lead_investor": "Bain Capital Life Sciences",
    },
    "iTeos Therapeutics": {
        "killer_tech": "Dual adenosine pathway blockade — inupadenant (A2A/A2B antagonist) + EOS-448 (TIGIT antagonist)",
        "thesis": "Tumour microenvironment immunosuppression is redundant; adenosine pathway is a major PD-1-independent axis",
        "differentiation": "A2A/A2B dual blockade avoids A2B escape; EOS-448 Fc-engineered for ADCC against Treg TIGIT",
        "platform": True,
        "lead_investor": "Atlas Venture",
    },
    "Boundless Bio": {
        "killer_tech": "Extrachromosomal DNA (ecDNA) targeting — BBO-8520 (ATR inhibitor) selectively kills ecDNA-amplified tumours",
        "thesis": "ecDNA amplifies oncogenes (EGFR, MYC) in 15% of solid tumours and drives drug resistance; ecDNA replication requires ATR",
        "differentiation": "ecDNA biology is ATR-dependent; BBO-8520 selectively toxic to ecDNA+ cells vs chromosomal amplification",
        "platform": True,
        "lead_investor": "RA Capital",
    },
    "Arcus Biosciences": {
        "killer_tech": "Multi-pronged adenosine/TIGIT/PD-1 combination — zimberelimab + domvanalimab designed as combo",
        "thesis": "NSCLC is PD-1 refractory in 60%; adenosine + TIGIT axes explain non-responders",
        "differentiation": "Company designs the combination from scratch rather than combining post-hoc biologics",
        "platform": True,
        "lead_investor": "OrbiMed",
    },
    "CG Oncology": {
        "killer_tech": "CG0070 oncolytic adenovirus — replicates only in Rb-deficient cancer cells; E2F-driven viral replication",
        "thesis": "NMIBC (bladder cancer) is a local disease accessible by intravesical instillation; oncolytic virus kills cancer + stimulates immune response",
        "differentiation": "Replication-competent unlike killed-virus approaches; BCG-unresponsive bladder cancer has no approved alternative",
        "platform": False,
        "lead_investor": "5AM Ventures",
    },
    "Chinook Therapeutics": {
        "killer_tech": "Atrasentan endothelin A antagonist — reduces proteinuria and inflammation in IgA nephropathy",
        "thesis": "IgA nephropathy has no approved targeted therapy; mesangial immune complex deposition is ET-A driven",
        "differentiation": "Atrasentan selectively blocks ET-A vs ET-B; prior ET-A/B drugs failed due to fluid retention from ET-B blockade",
        "platform": False,
        "lead_investor": "5AM Ventures",
    },
    "Esperion Therapeutics": {
        "killer_tech": "ATP citrate lyase inhibitor (bempedoic acid) — blocks cholesterol synthesis upstream of statins without myopathy",
        "thesis": "Statin-intolerant patients (~10M in US) have no oral alternative; same pathway, different enzyme",
        "differentiation": "Pro-drug activated only in liver, not muscle — eliminates the myalgia mechanism of statins",
        "platform": False,
        "lead_investor": "Polaris Partners",
    },
    "Harmony Biosciences": {
        "killer_tech": "Pitolisant histamine H3 antagonist/inverse agonist — promotes wakefulness without abuse potential (no amphetamine mechanism)",
        "thesis": "Narcolepsy with/without cataplexy requires wakefulness-promoting agents; pitolisant non-scheduled drug usable in all markets",
        "differentiation": "Non-controlled substance; works in patients unresponsive to modafinil; active on cataplexy unlike armodafinil",
        "platform": False,
        "lead_investor": "Deerfield Management",
    },
    "Zealand Pharma": {
        "killer_tech": "Amylin analog (petrelintide) + glucagon receptor agonism for obesity and metabolic disease",
        "thesis": "GLP-1 alone doesn't address satiety signals from amylin; combination should outperform semaglutide monotherapy",
        "differentiation": "Amylin acts on area postrema independent of GLP-1R; additive weight loss in Phase 2b",
        "platform": False,
        "lead_investor": "Novo Holdings",
    },
    "Forma Therapeutics": {
        "killer_tech": "IDH1 inhibitor (olutasidenib) — highly selective R132H/C/G mutant IDH1 blockade in AML",
        "thesis": "IDH1 mutation drives 2-HG oncometabolite; mIDH1 inhibition forces differentiation of leukemic blasts",
        "differentiation": "More selective than ivosidenib (fewer off-target effects); strong IDH differentiation syndrome management protocol",
        "platform": False,
        "lead_investor": "Polaris Partners",
    },
    "Corcept Therapeutics": {
        "killer_tech": "Mifepristone (GR antagonist) — blocks glucocorticoid receptor in Cushing's syndrome causing cortisol excess",
        "thesis": "Cushing's syndrome has no approved medical treatment targeting cortisol action; GR blockade is agnostic to ACTH source",
        "differentiation": "No adrenal suppression (cortisol levels rise but are blocked at receptor); only oral option for inoperable Cushing's",
        "platform": False,
        "lead_investor": "Deerfield Management",
    },
    "Dyne Therapeutics": {
        "killer_tech": "FORCE platform — antibody-oligonucleotide conjugate targeting transferrin receptor 1 (TfR1) for muscle delivery",
        "thesis": "Muscle diseases need oligonucleotide correction but ASOs don't penetrate muscle; TfR1 expressed on all muscle enables targeted delivery",
        "differentiation": "10–30x higher muscle uptake vs unconjugated ASO; systemic IV dosing reaches all skeletal muscle simultaneously",
        "platform": True,
        "lead_investor": "RA Capital",
    },
    "Passage Bio": {
        "killer_tech": "Intracisternal magna AAV9 delivery — cisterna magna injection deposits viral vector throughout CSF and brain",
        "thesis": "GM1 gangliosidosis and other lysosomal storage disorders need widespread CNS transduction, not achievable by IV at safe doses",
        "differentiation": "IC delivery achieves >100x more CNS transduction than IV with same dose",
        "platform": True,
        "lead_investor": "Bain Capital Life Sciences",
    },
    "Achaogen": {
        "killer_tech": "Next-generation aminoglycoside — chemical modifications block aminoglycoside-modifying enzymes (AMEs) that cause resistance",
        "thesis": "CRE/carbapenem-resistant Gram-negatives are running out of options; plazomicin works where all other aminoglycosides fail",
        "differentiation": "Active against KPC, NDM, OXA-48 producing isolates; IV once-daily vs colistin twice-daily with worse renal toxicity",
        "platform": False,
        "lead_investor": "OrbiMed",
    },
    "GRAIL": {
        "killer_tech": "Multi-cancer early detection — cfDNA methylation sequencing (Galleri) detects 50+ cancer types from a blood draw",
        "thesis": "Stage I cancer survival is 80–90%; Stage IV is 10–20%; early detection saves lives even without new treatments",
        "differentiation": "Methylation patterns are tissue-of-origin specific; Galleri signals tissue of origin with 89% accuracy",
        "platform": True,
        "lead_investor": "Arch Venture Partners",
    },
}


# ── Failure reasons — why it failed (tox, lack of activity, patient selection, commercial) ─
# failure_mode categories:
#   "lack_of_efficacy"       — drug did not work in the target population
#   "toxicity"               — drug worked but safety/tolerability unacceptable
#   "patient_selection"      — right drug, wrong population; biomarker missed
#   "mechanism_wrong"        — target hypothesis was incorrect
#   "commercial_failure"     — drug approved but could not achieve commercial viability
#   "regulatory"             — FDA/EMA rejected; insufficient data
#   "manufacturing"          — couldn't scale or maintain quality
#   "competition"            — beaten to market by a better drug
#   "combination_failure"    — failed as combo partner
#   None                     — no failure (success / ongoing)

_FAILURE_REASONS: dict[str, dict] = {

    # ── Flagship failures ─────────────────────────────────────────────────
    "Rubius Therapeutics": {
        "failure_mode": "lack_of_efficacy",
        "detail": "RTX-240 (4-1BBL+IL-15 RBC) showed no objective responses in Phase 2 AML/melanoma despite preclinical promise. T-cell costimulation from circulating RBCs did not translate to tumour-infiltrating T-cell activation. Hypothesis that systemic costimulatory RBCs would reach and activate intratumoral T cells was disproven.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "Tumour T-cell infiltration not selected for; cold tumours unlikely to respond to systemic costimulation",
    },
    "Evelo Biosciences": {
        "failure_mode": "lack_of_efficacy",
        "detail": "EDP1815 (Prevotella histicola extracellular vesicles) failed Phase 2 psoriasis and Phase 2 atopic dermatitis. No significant improvement over placebo in PASI or IGA scores. Mechanistic hypothesis that oral bacteria-derived EVs modulate systemic Th17 immunity was not validated in humans. Likely species/strain specificity and variable gut colonisation undermined effect.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "No biomarker to identify patients with relevant gut immune dysregulation",
    },
    "Kaleido Biosciences": {
        "failure_mode": "lack_of_efficacy",
        "detail": "KB195 (glycan prebiotic) failed Phase 2 VITORA trial in ornithine transcarbamylase deficiency (OTC). Did not reduce ammonia or protein restriction. Microbiome response to glycan structurally defined but pharmacodynamic readout (ammonia-consuming bacteria) varied enormously between patients. No proof that KB195 expanded the right species in each patient.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "Baseline microbiome composition not measured or stratified; microbiome heterogeneity was the hidden confounder",
    },
    "Axcella Health": {
        "failure_mode": "lack_of_efficacy",
        "detail": "AXA1125 (amino acid combination) failed Phase 2 NASH trial. No reduction in liver fat by MRI-PDFF vs placebo at 16 weeks. Multi-target hypothesis: each amino acid component had low effect size individually; combination showed no synergy measurable by clinical endpoints. NASH trials notoriously hard — high placebo response and patient heterogeneity contributed.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "NASH patient subtype (fibrosis stage, insulin resistance, PNPLA3 genotype) not used for stratification",
    },
    "EQRx": {
        "failure_mode": "commercial_failure",
        "detail": "Value-pricing model for oncology drugs failed commercially. US PBM formulary placement requires rebates and exclusivity deals with branded manufacturers; EQRx's lower-priced drugs were blocked from formularies. Payer incentive structure rewards high list price + rebate, not low list price. No generic pathway for biologics at the time.",
        "stage_failed": None,
        "tox_signal": False,
        "efficacy_signal": True,
        "biomarker_missed": None,
    },
    "Sienna Biopharmaceuticals": {
        "failure_mode": "lack_of_efficacy",
        "detail": "Topical formulations of calcineurin inhibitors and JAK inhibitors failed to demonstrate meaningful clinical improvement. Skin absorption was insufficient for systemic inflammatory diseases; proprietary microparticle formulation did not deliver meaningful drug concentrations to dermis.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": None,
    },
    "Neon Therapeutics": {
        "failure_mode": "lack_of_efficacy",
        "detail": "Neoantigen long peptide vaccines elicited T-cell responses but did not achieve tumour regression alone. Merged into BioNTech — neoantigen approach still being pursued. Phase 1b combination with pembrolizumab showed immune responses but insufficient durable responses in checkpoint-refractory patients.",
        "stage_failed": "phase1",
        "tox_signal": False,
        "efficacy_signal": True,
        "biomarker_missed": "Responders vs non-responders defined by HLA-neoantigen fit; patient selection needs better HLA prediction models",
    },

    # ── Atlas failures ────────────────────────────────────────────────────
    "Aileron Therapeutics": {
        "failure_mode": "toxicity",
        "detail": "ALRN-6924 (stapled p53-activating peptide) caused dose-limiting thrombocytopaenia in Phase 1, limiting dose to sub-therapeutic levels. p53 wild-type cells in bone marrow are also activated by MDM2 inhibition — same mechanism that kills tumour cells kills normal haematopoietic progenitors. Therapeutic window between MDM2hi tumour cells and normal progenitors was insufficient.",
        "stage_failed": "phase1",
        "tox_signal": True,
        "efficacy_signal": True,
        "biomarker_missed": "MDM2 amplification status could have enriched patient selection; thrombocytopaenia is on-target not off-target toxicity",
    },
    "BlackDiamond Therapeutics": {
        "failure_mode": "lack_of_efficacy",
        "detail": "BDTX-189 (EGFR/HER2 exon20 allosteric inhibitor) showed low response rates in NSCLC exon20 insertions (~10% ORR) vs 28–40% for poziotinib/amivantamab. Allosteric binding site hypothesis was not confirmed; exon20 insertions are structurally heterogeneous (near loop vs far loop) and BDTX-189 did not cover all subtypes.",
        "stage_failed": "phase1",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "Exon20 insertion subtype (near-loop vs far-loop) not stratified; allosteric site is subtype-specific",
    },

    # ── Third Rock failures ───────────────────────────────────────────────
    "Aileron Therapeutics": {
        "failure_mode": "toxicity",
        "detail": "Thrombocytopenia dose-limiting toxicity from on-target MDM2 inhibition in platelet progenitors. Phase 2 expansion in liposarcoma and AML terminated early due to inability to escalate dose sufficiently. The stapled peptide p53-activation is biologically correct but the therapeutic window is too narrow at achievable doses.",
        "stage_failed": "phase2",
        "tox_signal": True,
        "efficacy_signal": True,
        "biomarker_missed": "Need MDM2 amplification + low baseline platelets; broader biomarker to identify wide therapeutic window patients",
    },

    # ── ARCH Venture failures ─────────────────────────────────────────────
    "Neumora Therapeutics": {
        "failure_mode": "patient_selection",
        "detail": "Navacaprant (OPRK1/kappa opioid antagonist) failed Phase 3 KOASTAL-1 trial (MDD). Primary endpoint MADRS score not met (LS mean -12.7 drug vs -11.5 placebo, p=0.26). The biomarker hypothesis — that plasma dynorphin identifies KOR-active patients — was not validated as a stratification tool. All-comers MDD trials have 30–40% placebo response which buries modest drug effects. Should have been a biomarker-selected Phase 2.",
        "stage_failed": "phase3",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "Plasma dynorphin not used as enrichment biomarker in Phase 3; all-comers MDD swamps real responders",
    },
    "Sana Biotechnology": {
        "failure_mode": None,
        "detail": "Ongoing — SAN-903 (hypoimmune CD19 CAR-T) Phase 1; significant cash burn with no approved product. HIP platform (hypoimmune universal cells) not yet clinically validated for immune evasion in humans.",
        "stage_failed": None,
        "tox_signal": False,
        "efficacy_signal": None,
        "biomarker_missed": None,
    },

    # ── RA Capital failures ───────────────────────────────────────────────
    "Alector": {
        "failure_mode": "mechanism_wrong",
        "detail": "AL002 (TREM2 agonist) failed Phase 2 INVOKE-2 (Alzheimer's disease). The TREM2 microglial activation hypothesis — that boosting phagocytic clearance would slow AD — was not validated. AL002 increased TREM2 sFlt levels (PD marker) but did not slow CDR-SB decline. TREM2-high microglia may already be maximally activated in late-stage AD.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "Disease stage selection critical — early AD (preclinical) may be the window; late AD has irreversible neuronal loss",
    },
    "Passage Bio": {
        "failure_mode": "lack_of_efficacy",
        "detail": "PBGM01 (AAV9 GLB1 for GM1 gangliosidosis) failed Phase 1/2. Patients showed severe neurological decline despite treatment. Gene therapy delivered functional enzyme but lysosomal substrate accumulation from years of disease caused irreversible neuronal damage pre-treatment. Treatment timing (infantile form, start before symptom onset) appears critical.",
        "stage_failed": "phase1",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "Disease stage at treatment — neuronal loss before treatment initiation renders gene therapy too late",
    },

    # ── Multi-VC failures ─────────────────────────────────────────────────
    "Achaogen": {
        "failure_mode": "commercial_failure",
        "detail": "Plazomicin (Zemdri) was approved 2018 for cUTI but commercial launch failed. Antibiotic stewardship policies restrict use of last-resort antibiotics to documented CRE infections (<5% of target UTI population). Sales ~$3.5M in first year vs $300M+ needed to break even. Hospital formulary placement requires infectious disease consultation — adds friction. Antibiotics are given for 5–10 days vs chronic drugs; no recurring revenue. Filed bankruptcy April 2019, 9 months post-approval. Drug was effective and needed; economic model was broken.",
        "stage_failed": None,
        "tox_signal": False,
        "efficacy_signal": True,
        "biomarker_missed": None,
    },
    "Genocea Biosciences": {
        "failure_mode": "lack_of_efficacy",
        "detail": "GEN-009 personalised neoantigen vaccine failed Phase 2 in solid tumours; combined with pembrolizumab showed no durable responses beyond checkpoint alone. GEN-011 (autologous neoantigen-specific T cells) discontinued after Phase 2 miss. ATLAS immunogenicity assay correctly identified T-cell responses but T-cell responses did not translate to tumour regression. Trafficking to tumour and exhaustion in TME remained unsolved.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "T-cell functionality (exhaustion markers TIM3/LAG3) not measured; immunogenicity ≠ anti-tumour activity",
    },
    "Kronos Bio": {
        "failure_mode": "lack_of_efficacy",
        "detail": "Entospletinib (SYK inhibitor) failed Phase 2 in AML. Response rate ~20% in a heavily pre-treated population, not competitive with venetoclax/azacitidine standard of care. SYK pathway is active in AML blasts but is not a dominant driver in most subtypes. Patient stratification by SYK pathway activation was insufficient to identify the responder population.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "SYK expression/activation not used as patient stratification biomarker; FLT3/NPM1 subtypes not explored separately",
    },
    "Eliem Therapeutics": {
        "failure_mode": "lack_of_efficacy",
        "detail": "ETX-810 (Nav1.7+Nav1.8 dual inhibitor) failed Phase 2 trigeminal neuralgia. Pain endpoint not met. Genetic validation of Nav1.7 (SCN9A loss-of-function = congenital insensitivity to pain) was strong, but trigeminal neuralgia mechanistically driven by demyelination/ion channel remodelling pattern distinct from the LoF genotype. Patient population was not genetically stratified.",
        "stage_failed": "phase2",
        "tox_signal": False,
        "efficacy_signal": False,
        "biomarker_missed": "SCN9A gain-of-function mutations not required for trial entry; genetic validation of sodium channel hypothesis in this specific pain subtype was incomplete",
    },
}


# ── Failure regex patterns for CT.gov WhyStopped text ─────────────────────────
_TOX_WORDS = re.compile(
    r"\b(toxic|toxicity|adverse|safety|tolerabilit|side effect|SAE|serious adverse|"
    r"hepatotoxic|cardiotoxic|myelosuppress|thrombocytopeni|neutropeni|"
    r"DLT|dose.limiting|QTc|hepatic|renal|nephrotoxic|hypersensitiv|"
    r"anaphyla|immunogenic|cytokine|CRS|ICANS|neurotoxic)\b",
    re.IGNORECASE,
)
_EFFICACY_WORDS = re.compile(
    r"\b(efficacy|response rate|no benefit|futility|interim analysis|"
    r"lack of effect|no significant|did not meet|primary endpoint|"
    r"negative|did not demonstrate|failed to show|ORR|PFS|OS|p=0\.[1-9])\b",
    re.IGNORECASE,
)
_SELECTION_WORDS = re.compile(
    r"\b(enroll|recruitment|enrolment|accrual|eligible|screen fail|"
    r"feasibility|no patients|insufficient patients|competing trial|"
    r"sponsor decision|business reason|strategic)\b",
    re.IGNORECASE,
)


def _classify_failure(why_stopped: str) -> str:
    """Classify WhyStopped text into a failure category."""
    if not why_stopped:
        return "unknown"
    if _TOX_WORDS.search(why_stopped):
        return "toxicity"
    if _EFFICACY_WORDS.search(why_stopped):
        return "lack_of_efficacy"
    if _SELECTION_WORDS.search(why_stopped):
        return "patient_selection"
    return "other"


# ── Collector class ────────────────────────────────────────────────────────────

class VCDecisionTracker(BaseCollector):
    """
    Emits one record per VC decision:
      - company exit/status (IPO, acquisition, wind-down, ongoing)
      - every clinical trial (all statuses) from ClinicalTrials.gov
    """

    name = "vc_decision_tracker"
    rate_limit_seconds = 0.4

    def collect(self, max_records: int = 1000) -> list[RawRecord]:
        records: list[RawRecord] = []
        seen_companies: set[str] = set()  # tracks which companies have been CT-searched

        # Group hardcoded companies by canonical name, collecting all VCs that backed them
        company_vcs: dict[str, list[str]] = {}
        for entry in _HARDCODED_COMPANIES:
            cname = entry["company"]
            company_vcs.setdefault(cname, [])
            if entry["vc"] not in company_vcs[cname]:
                company_vcs[cname].append(entry["vc"])

        for company, vcs in company_vcs.items():
            if len(records) >= max_records:
                break

            vc_tag = vcs[0]  # primary VC for labelling
            vc_all = " | ".join(vcs)

            # 1. Emit known outcome record
            if company in _KNOWN_OUTCOMES:
                rec = self._make_exit_record(company, vc_tag, vc_all, _KNOWN_OUTCOMES[company])
                if rec:
                    records.append(rec)

            # 2. Emit per-trial records from ClinicalTrials.gov
            if company not in seen_companies:
                seen_companies.add(company)
                trials = self._fetch_all_trials(company)
                for trial in trials:
                    if len(records) >= max_records:
                        break
                    rec = self._make_trial_record(company, vc_tag, vc_all, trial)
                    if rec:
                        records.append(rec)

        logger.info("[vc_decision_tracker] Total: %d records from %d companies",
                    len(records), len(seen_companies))
        return records[:max_records]

    # ── Exit/outcome record ────────────────────────────────────────────────

    def _make_exit_record(
        self,
        company: str,
        vc: str,
        vc_all: str,
        outcome: dict,
    ) -> RawRecord | None:
        otype   = outcome["outcome_type"]   # "acquired","ipo","bankrupt","winddown","merged","active"
        result  = outcome["result"]         # "success","failure","ongoing"
        note    = outcome.get("note", "")
        year    = outcome.get("year") or 0
        deal_m  = outcome.get("deal_size_m", 0) or 0
        acquirer = outcome.get("acquirer") or ""
        drugs   = outcome.get("approved_drugs", [])

        # Map to RawRecord fields
        if otype == "acquired":
            decision = "go"
            ct_outcome = "approved" if drugs else "acquired"
            clinical_stage = "phase3" if drugs else "unknown"
        elif otype == "ipo":
            decision = "go"
            ct_outcome = "approved" if drugs else "ongoing"
            clinical_stage = "approved" if drugs else "unknown"
        elif otype in ("bankrupt", "winddown"):
            decision = "no-go"
            ct_outcome = "discontinued_p3" if "Phase 3" in note else "discontinued_p2"
            clinical_stage = "phase3" if "Phase 3" in note else "phase2"
        elif otype == "merged":
            decision = "go"
            ct_outcome = "ongoing"
            clinical_stage = "unknown"
        else:  # active
            decision = "undecided"
            ct_outcome = "ongoing"
            clinical_stage = "unknown"

        indication = _ext_ind(note)
        mechanism  = _ext_mech(note)

        title = (
            f"[EXIT] {company} → {otype.upper()}"
            + (f" by {acquirer}" if acquirer else "")
            + (f" ${deal_m}M" if deal_m else "")
            + (f" ({year})" if year else "")
        )

        source_id = hashlib.md5(f"exit:{company}".encode()).hexdigest()[:16]

        # Enrich with investment thesis
        thesis_data = _INVESTMENT_THESIS.get(company, {})
        killer_tech    = thesis_data.get("killer_tech", "")
        thesis_text    = thesis_data.get("thesis", "")
        differentiation = thesis_data.get("differentiation", "")
        is_platform    = thesis_data.get("platform", False)

        # Enrich with failure reasons (for failed companies)
        fail_data    = _FAILURE_REASONS.get(company, {})
        fail_mode    = fail_data.get("failure_mode", "")
        fail_detail  = fail_data.get("detail", "")
        tox_signal   = fail_data.get("tox_signal", False)
        efficacy_ok  = fail_data.get("efficacy_signal", None)
        biomarker_miss = fail_data.get("biomarker_missed", "")

        raw = (
            f"{company}\n{vc_all}\n{note}\n"
            + "\n".join(drugs)
            + (f"\nKILLER TECH: {killer_tech}" if killer_tech else "")
            + (f"\nINVESTMENT THESIS: {thesis_text}" if thesis_text else "")
            + (f"\nDIFFERENTIATION: {differentiation}" if differentiation else "")
            + (f"\nFAILURE MODE: {fail_mode}" if fail_mode else "")
            + (f"\nWHY FAILED: {fail_detail}" if fail_detail else "")
            + (f"\nBIOMARKER MISSED: {biomarker_miss}" if biomarker_miss else "")
        )

        return RawRecord(
            source="vc_decision_tracker",
            source_id=source_id,
            url=f"https://en.wikipedia.org/wiki/{company.replace(' ', '_')}",
            title=title,
            indication=indication,
            mechanism=mechanism,
            clinical_stage=clinical_stage,
            decision=decision,
            outcome=ct_outcome,
            investment_usd=float(deal_m * 1_000_000),
            raw_text=raw,
            extra={
                "vc": vc,
                "vc_all": vc_all,
                "company": company,
                "outcome_type": otype,
                "result": result,
                "acquirer": acquirer,
                "approved_drugs": drugs,
                "year": year,
                "deal_size_m": deal_m,
                # Investment thesis
                "killer_tech": killer_tech,
                "thesis": thesis_text,
                "differentiation": differentiation,
                "is_platform": is_platform,
                # Failure analysis
                "failure_mode": fail_mode,
                "failure_detail": fail_detail,
                "tox_signal": tox_signal,
                "efficacy_signal": efficacy_ok,
                "biomarker_missed": biomarker_miss,
            },
        )

    # ── ClinicalTrials.gov full trial history ──────────────────────────────

    def _fetch_all_trials(self, company: str) -> list[dict]:
        """Return all CT.gov studies for a company (all statuses)."""
        params = {
            "query.lead": company,
            "fields": ",".join([
                "NCTId", "BriefTitle", "Phase", "OverallStatus",
                "Condition", "InterventionType", "InterventionName",
                "BriefSummary", "WhyStopped", "StartDate", "CompletionDate",
            ]),
            "pageSize": 40,
        }
        try:
            resp = self._get(_CT_API, params=params, accept_json=True)
            data = resp.json()
        except Exception as exc:
            logger.warning("  CT.gov lookup failed for %s: %s", company, exc)
            return []

        studies = data.get("studies", [])
        logger.debug("  CT.gov %s → %d trials", company, len(studies))
        return studies

    def _make_trial_record(
        self,
        company: str,
        vc: str,
        vc_all: str,
        study: dict,
    ) -> RawRecord | None:
        proto  = study.get("protocolSection", {})
        id_mod = proto.get("identificationModule", {})
        stat_mod = proto.get("statusModule", {})
        desc_mod = proto.get("descriptionModule", {})
        cond_mod = proto.get("conditionsModule", {})
        int_mod  = proto.get("armsInterventionsModule", {})
        design_mod = proto.get("designModule", {})

        nct_id  = id_mod.get("nctId", "")
        title   = id_mod.get("briefTitle", "")
        status  = stat_mod.get("overallStatus", "UNKNOWN_STATUS")
        why_stopped = stat_mod.get("whyStopped", "")
        summary = desc_mod.get("briefSummary", "")
        conditions = ", ".join(cond_mod.get("conditions", []))

        # Phase — CT.gov v2 uses designModule.phases (list of enum strings)
        phases = design_mod.get("phases", [])          # ["PHASE1"], ["PHASE2"], etc.
        if not phases:
            phases = design_mod.get("phaseList", {}).get("phase", [])  # v1 fallback
        phase_raw = phases[0] if phases else "NA"
        phase_clean = phase_raw.replace(" ", "").replace("_", "").upper()
        clinical_stage = _PHASE_MAP.get(phase_raw, _PHASE_MAP.get(phase_clean, "unknown"))

        # Interventions
        interventions = int_mod.get("interventions", [])
        mech_text = " ".join(
            iv.get("name", "") + " " + iv.get("type", "")
            for iv in interventions
        )

        decision = _STATUS_DECISION.get(status, "undecided")
        outcome_raw = _STATUS_OUTCOME.get(status, "unknown")

        # Map outcome to our schema
        if outcome_raw == "discontinued":
            outcome = f"discontinued_{clinical_stage}" if clinical_stage not in ("unknown", "preclinical") else "discontinued_p2"
        elif outcome_raw == "completed":
            outcome = "completed"
        else:
            outcome = outcome_raw

        indication = _ext_ind(conditions + " " + summary)
        mechanism  = _ext_mech(mech_text + " " + summary)

        full_text = (
            f"{company} | {title}\n"
            f"Status: {status} | Phase: {clinical_stage}\n"
            f"Condition: {conditions}\n"
            f"Why stopped: {why_stopped}\n"
            f"{summary[:1000]}"
        )

        # Classify why a trial was stopped
        stop_class = _classify_failure(why_stopped) if status in (
            "TERMINATED", "WITHDRAWN", "SUSPENDED"
        ) else ""

        # Pull company-level failure/thesis context into the trial raw_text
        fail_data  = _FAILURE_REASONS.get(company, {})
        thesis_data = _INVESTMENT_THESIS.get(company, {})
        if fail_data:
            full_text += f"\nCOMPANY FAILURE MODE: {fail_data.get('failure_mode', '')}"
            full_text += f"\n{fail_data.get('detail', '')}"
        if thesis_data:
            full_text += f"\nKILLER TECH: {thesis_data.get('killer_tech', '')}"
            full_text += f"\nTHESIS: {thesis_data.get('thesis', '')}"

        if not nct_id:
            return None

        record_title = (
            f"[TRIAL:{clinical_stage.upper()}:{status}] {company} — {title[:60]}"
        )

        source_id = hashlib.md5(f"trial:{nct_id}:{company}".encode()).hexdigest()[:16]

        return RawRecord(
            source="vc_decision_tracker",
            source_id=source_id,
            url=f"https://clinicaltrials.gov/study/{nct_id}",
            title=record_title,
            indication=indication,
            mechanism=mechanism,
            clinical_stage=clinical_stage,
            decision=decision,
            outcome=outcome,
            investment_usd=0.0,
            raw_text=full_text,
            extra={
                "vc": vc,
                "vc_all": vc_all,
                "company": company,
                "nct_id": nct_id,
                "status": status,
                "why_stopped": why_stopped,
                "stop_classification": stop_class,
                "conditions": conditions,
                # Forward-fill company-level thesis/failure for ML features
                "failure_mode": fail_data.get("failure_mode", ""),
                "tox_signal": fail_data.get("tox_signal", False),
                "biomarker_missed": fail_data.get("biomarker_missed", ""),
                "killer_tech": thesis_data.get("killer_tech", ""),
                "is_platform": thesis_data.get("platform", False),
            },
        )
