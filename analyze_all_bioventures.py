#!/usr/bin/env python3
"""
analyze_all_bioventures.py
==========================
Full-portfolio analysis across ALL 5 BioVentures VC firms (~97 companies).

Steps:
  1. Query ClinicalTrials.gov v2 API for every company
  2. Query PubMed for key drug publications
  3. Load existing downloaded materials from data/slides/portfolio/ where available
  4. Build RawRecord objects and upsert into DB
  5. Train SuccessPredictor on the full real-world DB
  6. Generate data/full_portfolio_analysis.html  — rich interactive HTML report

Run:
    python analyze_all_bioventures.py
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from src.collectors.base_collector import RawRecord
from src.learning.decision_model import SuccessPredictor
from src.storage.repository import bulk_upsert, fetch_all

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
PORTFOLIO_DIR = Path("data/slides/portfolio")
REPORT_OUT    = Path("data/full_portfolio_analysis.html")
DB_SOURCE     = "bioventures_full_v1"
RATE          = 0.35   # seconds between HTTP calls

CT_API   = "https://clinicaltrials.gov/api/v2/studies"
PM_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PM_EFETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 PortfolioAnalysis/2.0 "
        "(+https://github.com/Shirosaru/DecisionMaking)"
    )
}

# ══════════════════════════════════════════════════════════════════════════════
# FULL PORTFOLIO CATALOG — 97 companies across 5 BioVentures VC firms
# outcome_hint: "approved" | "acquired" | "failed_p3" | "failed_p2" | "active" | "preclinical"
# ══════════════════════════════════════════════════════════════════════════════
FULL_PORTFOLIO: list[dict] = [

    # ══════════════════════════════════════════════════════════════════════════
    # 3E BioVentures — Biotech / New Drugs  (19 companies)
    # ══════════════════════════════════════════════════════════════════════════
    dict(firm="3E BioVentures", cat="Biotech", slug="aravive", name="Aravive",
         ticker="ARAV",
         drug="Batiraxcept (AVB-S6-500)",
         mechanism="AXL receptor / GAS6-axis decoy protein (anti-metastatic biologic)",
         indication="oncology", website="https://aravive.com",
         outcome_hint="failed_p3",
         note="Phase 3 AXLerate-OC trial failed Aug 2023; company ran out of cash"),

    dict(firm="3E BioVentures", cat="Biotech", slug="oncoimmune", name="OncoImmune",
         ticker=None,
         drug="CD24Fc / efprezimod alfa (MK-7110)",
         mechanism="CD24-SiglecG/10 innate immune checkpoint fusion protein",
         indication="immunology", website="https://oncoimmune.com",
         outcome_hint="failed_p3",
         note="Phase 3 GVHD trial terminated; Merck licensed, then discontinued"),

    dict(firm="3E BioVentures", cat="Biotech", slug="c4_therapeutics", name="C4 Therapeutics",
         ticker="CCCC",
         drug="Cemsidomide (CFT7455) + CFT1946",
         mechanism="IKZF1/3 molecular glue cereblon E3 ligase degrader (CELMoD)",
         indication="oncology", website="https://c4therapeutics.com",
         outcome_hint="active",
         note="CFT7455 (MM/NHL) Phase 1/2; CFT1946 (BRAF-mutant solid tumors) Phase 1"),

    dict(firm="3E BioVentures", cat="Biotech", slug="cognition_therapeutics", name="Cognition Therapeutics",
         ticker="CGTX",
         drug="CT1812",
         mechanism="Sigma-2 receptor antagonist (displaces amyloid-beta oligomers from synapses)",
         indication="neurology", website="https://cognitx.com",
         outcome_hint="active",
         note="Phase 2 SHIMMER trial in DLB; SEQUEL trial in Alzheimer's retinal degeneration"),

    dict(firm="3E BioVentures", cat="Biotech", slug="oncoc4", name="OncoC4",
         ticker=None,
         drug="ONC-392 / IO-108 (anti-CD24 Ab)",
         mechanism="CTLA-4 + CD24 dual immune checkpoint antibody (best-in-class anti-CTLA-4)",
         indication="oncology", website="https://www.oncoc4.com",
         outcome_hint="active",
         note="ONC-392 Phase 1/2 in solid tumors; IO-108 anti-CD24 mAb Phase 1"),

    dict(firm="3E BioVentures", cat="Biotech", slug="quadriga_biosciences", name="Quadriga Biosciences",
         ticker=None,
         drug="QBM-001",
         mechanism="LAT1 (large neutral amino acid transporter 1) small molecule inhibitor",
         indication="oncology", website="http://www.quadrigabiosciences.com",
         outcome_hint="active",
         note="Targets nutrient deprivation in cancer cells via LAT1; preclinical/Phase 1"),

    dict(firm="3E BioVentures", cat="Biotech", slug="avirmax", name="Avirmax",
         ticker=None,
         drug="AVX-001 (AAV ophthalmic gene therapy)",
         mechanism="AAV-based gene delivery platform for chronic ocular diseases",
         indication="rare_disease", website="https://avirmax.com",
         outcome_hint="active",
         note="Next-generation gene therapy for retinal diseases; preclinical/IND stage"),

    dict(firm="3E BioVentures", cat="Biotech", slug="cullgen", name="Cullgen",
         ticker=None,
         drug="CG001419 / CG009301",
         mechanism="UBR-box targeted protein degradation (U-PROTAC platform)",
         indication="oncology", website="https://cullgen.com",
         outcome_hint="acquired",
         note="Acquired by Gyre Therapeutics 2023 for U-PROTAC platform"),

    dict(firm="3E BioVentures", cat="Biotech", slug="shanton_pharma", name="SHANTON Pharma",
         ticker=None,
         drug="Undisclosed XO/NLRP3 pathway inhibitor",
         mechanism="Xanthine oxidase / uric acid metabolism modulator (gout/metabolic syndrome)",
         indication="metabolic", website="https://shantonpharma.com",
         outcome_hint="active",
         note="Focus on uric acid metabolism, inflammatory pain, gout"),

    dict(firm="3E BioVentures", cat="Biotech", slug="rapafusyn", name="Rapafusyn Pharmaceuticals",
         ticker=None,
         drug="RFN-001 (rapafucin macrocycle)",
         mechanism="FKBP12-binding rapafucin macrolide (selective mTORC1 inhibitor)",
         indication="oncology", website="https://rapafusyn.com",
         outcome_hint="active",
         note="Proprietary rapafucin library; FKBP12 bifunctional macrocycles"),

    dict(firm="3E BioVentures", cat="Biotech", slug="dewpoint_therapeutics", name="Dewpoint Therapeutics",
         ticker=None,
         drug="DPTX3186",
         mechanism="Myc biomolecular condensate modulator (phase-separation biology)",
         indication="oncology", website="https://dewpointx.com",
         outcome_hint="active",
         note="Pioneer in condensate-targeted drug discovery; Myc condensate inhibitor"),

    dict(firm="3E BioVentures", cat="Biotech", slug="dermaliq", name="Dermaliq Therapeutics",
         ticker=None,
         drug="DLQ-001 (cyclosporine A nanoparticle)",
         mechanism="Topical cyclosporine A EyeSol/EarSol nanoformulation (dermatology/ocular)",
         indication="rare_disease", website="https://dermaliq.com",
         outcome_hint="active",
         note="Spin-off from Novaliq GmbH 2021; waterless topical/ophthalmic formulations"),

    dict(firm="3E BioVentures", cat="Biotech", slug="myro_therapeutics", name="Myro Therapeutics",
         ticker=None,
         drug="MYR-001",
         mechanism="Small molecule restoring brain health via common neurodegeneration mechanisms",
         indication="neurology", website="http://myrotx.com",
         outcome_hint="active",
         note="Founded by academic neuroscientists; brain health common disease mechanisms"),

    dict(firm="3E BioVentures", cat="Biotech", slug="retex_pharmaceuticals", name="Retex Pharmaceuticals",
         ticker=None,
         drug="RTX-001",
         mechanism="ADPKD (autosomal dominant polycystic kidney disease) — mTOR/Wnt pathway",
         indication="rare_disease", website="https://pronovotx.com",  # shared site likely
         outcome_hint="active",
         note="Develop a cure for ADPKD; preclinical"),

    dict(firm="3E BioVentures", cat="Biotech", slug="pronovo_therapeutics", name="ProNovo Therapeutics",
         ticker=None,
         drug="PN-001",
         mechanism="Precision small molecule for neuropsychiatric disorders",
         indication="neurology", website="https://pronovotx.com",
         outcome_hint="active",
         note="Advancing precision therapeutics for neuropsychiatric disorders"),

    dict(firm="3E BioVentures", cat="Biotech", slug="larkspur_therapeutics", name="Larkspur Therapeutics",
         ticker=None,
         drug="LAR-001",
         mechanism="Antigen presentation / TLR immunotherapy at tumor-immune intersection",
         indication="oncology", website=None,
         outcome_hint="active",
         note="Precision immunotherapy targeting antigen presentation intersections"),

    dict(firm="3E BioVentures", cat="Biotech", slug="lysoway_therapeutics", name="Lysoway Therapeutics",
         ticker=None,
         drug="LYS-001",
         mechanism="Lysosomal TRPML1/TRPML2 ion channel modulator (lysosomal storage disease)",
         indication="rare_disease", website="https://www.lysoway.com",
         outcome_hint="active",
         note="Leader in lysosomal ion channel disease biology"),

    dict(firm="3E BioVentures", cat="Biotech", slug="lipidio", name="Lipidio",
         ticker=None,
         drug="LIP-401",
         mechanism="ATGL / fatty acid oxidation lipase modulator (NASH/PWS/AIWG)",
         indication="metabolic", website="https://lipidiopharma.com",
         outcome_hint="active",
         note="NASH, Prader-Willi syndrome, antipsychotic drug-induced weight gain"),

    dict(firm="3E BioVentures", cat="Biotech", slug="arnatar_therapeutics", name="Arnatar Therapeutics",
         ticker=None,
         drug="ART101",
         mechanism="GalNAc-conjugated siRNA / RNAi hepatitis B replication inhibitor",
         indication="infectious", website="https://www.arnatar.com",
         outcome_hint="active",
         note="Reimagining RNA medicines for HBV; GalNAc-siRNA hepatic delivery"),

    # ══════════════════════════════════════════════════════════════════════════
    # 3E BioVentures — X-Disciplinary HealthTech  (12 companies)
    # ══════════════════════════════════════════════════════════════════════════
    dict(firm="3E BioVentures", cat="HealthTech", slug="cytek_biosciences", name="Cytek Biosciences",
         ticker="CTKB",
         drug="Aurora Full Spectrum Flow Cytometer",
         mechanism="Optical grating + APD full-spectrum flow cytometry platform",
         indication="diagnostics", website="https://cytekbio.com",
         outcome_hint="approved",
         note="Nasdaq-listed; FDA-cleared Aurora platform replacing filter-based cytometers"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="twist_bioscience_3e", name="Twist Bioscience",
         ticker="TWST",
         drug="Twist DNA synthesis platform",
         mechanism="Silicon-based cell-free DNA synthesis (high-throughput oligos/genes)",
         indication="diagnostics", website="https://twistbioscience.com",
         outcome_hint="approved",
         note="Nasdaq-listed; synthetic biology, NGS library prep, antibody discovery"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="lumithera", name="LumiThera",
         ticker=None,
         drug="Valeda Light Delivery System",
         mechanism="Photobiomodulation (PBM) device for dry age-related macular degeneration",
         indication="rare_disease", website="https://lumithera.com",
         outcome_hint="approved",
         note="CE Mark in Europe; FDA Breakthrough Device Designation for dry AMD"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="neural_galaxy", name="Neural Galaxy",
         ticker=None,
         drug="NEURAL neuromodulation system",
         mechanism="Focused ultrasound / neuromodulation (Harvard Medical School/MIT spinout)",
         indication="neurology", website=None,
         outcome_hint="active",
         note="From Harvard Medical School and MIT McGovern Institute"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="nanoinsights_tech", name="NanoInsights-Tech",
         ticker=None,
         drug="Super-resolution microscopy platform",
         mechanism="Single-molecule super-resolution imaging (Nobel Prize lab technology)",
         indication="diagnostics", website=None,
         outcome_hint="active",
         note="Super-resolution microscopy from Nobel Prize winner's laboratory"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="eluminex", name="Eluminex Biosciences",
         ticker=None,
         drug="ELX-001 ophthalmic",
         mechanism="Innovative ocular therapeutics (China-focused global markets)",
         indication="rare_disease", website="https://www.3ebiovc.com/www.eluminexbio.com",
         outcome_hint="active",
         note="Ocular therapeutics for China and global markets"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="eaglenos", name="Eaglenos",
         ticker=None,
         drug="Precision health platform",
         mechanism="IoT/AI continuous health monitoring (personalized health management)",
         indication="metabolic", website="https://www.eaglenos.com/en/",
         outcome_hint="active",
         note="Precision measurements and technology for personalized health management"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="profusa", name="Profusa",
         ticker=None,
         drug="Lumee Oxygen Sensor",
         mechanism="Injectable biosensor for continuous tissue-level chemistry monitoring",
         indication="metabolic", website="http://profusa.com",
         outcome_hint="active",
         note="Non-invasive continuous O2, glucose, pH, lactate biosensor platform"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="genotix_biosciences", name="Genotix Biosciences",
         ticker=None,
         drug="SuperLisa digital ELISA",
         mechanism="Digital single-molecule protein detection (ultra-high sensitivity ELISA)",
         indication="diagnostics", website="https://www.genotixbio.com",
         outcome_hint="active",
         note="Disruptive digital ELISA with ultra-high sensitivity, throughput"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="subtle_medical", name="Subtle Medical",
         ticker=None,
         drug="SubtleMR / SubtlePET",
         mechanism="AI deep-learning MRI and PET image enhancement (FDA cleared)",
         indication="diagnostics", website="https://subtlemedical.com",
         outcome_hint="approved",
         note="FDA-cleared AI for MRI/PET enhancement; faster, lower-dose imaging"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="smartlens", name="Smartlens Health",
         ticker=None,
         drug="Smartlens glaucoma platform",
         mechanism="Smart contact lens for continuous IOP monitoring + glaucoma management",
         indication="rare_disease", website="https://www.smartlens.health",
         outcome_hint="active",
         note="Prevent blindness from glaucoma via smart lens IOP monitoring"),

    dict(firm="3E BioVentures", cat="HealthTech", slug="acclaro", name="Acclaro Medical",
         ticker=None,
         drug="IRL mid-IR fiber laser system",
         mechanism="Mid-IR fiber laser for medical aesthetic and surgical applications",
         indication="rare_disease", website="https://fa-in.com",
         outcome_hint="active",
         note="Mid-IR fiber laser medical aesthetic platform"),

    # ══════════════════════════════════════════════════════════════════════════
    # BioVentures Capital — Medical Devices  (2 companies)
    # ══════════════════════════════════════════════════════════════════════════
    dict(firm="BioVentures Capital", cat="MedDevice", slug="oraliva", name="Oraliva",
         ticker=None,
         drug="Oraliva periodontal formulation",
         mechanism="Topical antimicrobial / anti-inflammatory oral drug delivery",
         indication="rare_disease", website="https://oraliva.com",
         outcome_hint="active",
         note="Periodontal therapy; oral drug delivery for gum disease"),

    dict(firm="BioVentures Capital", cat="MedDevice", slug="biopathogenix", name="Biopathogenix",
         ticker=None,
         drug="BPG-01",
         mechanism="Antimicrobial / anti-infective platform (pathogen-targeting)",
         indication="infectious", website="https://biopathogenix.com",
         outcome_hint="active",
         note="Anti-infective platform; antimicrobial mechanism"),

    # ══════════════════════════════════════════════════════════════════════════
    # BioVentures MedTech Funds  (10 companies)
    # ══════════════════════════════════════════════════════════════════════════
    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="optivio", name="Optivio",
         ticker=None,
         drug="Recover™ hemodynamic support system",
         mechanism="Extracorporeal cardiopulmonary hemodynamic support (cardiogenic shock)",
         indication="cardiovascular", website="https://www.optivio.com",
         outcome_hint="active",
         note="Extracorporeal hemodynamic support for cardiogenic shock patients"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="brainbox_solutions", name="Brainbox Solutions",
         ticker=None,
         drug="Brainbox device",
         mechanism="Undisclosed neuromonitoring / neurological medical device",
         indication="neurology", website="https://brainboxinc.com",
         outcome_hint="active",
         note="MedTech portfolio company; neuromonitoring device"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="caresignal", name="CareSignal",
         ticker=None,
         drug="CareSignal digital health platform",
         mechanism="Text-based remote patient monitoring for chronic disease management",
         indication="cardiovascular", website="https://www.caresignal.health",
         outcome_hint="active",
         note="Digital health / RPM for chronic disease; value-based care"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="conextions", name="CoNextions",
         ticker=None,
         drug="CoNextions TR implant",
         mechanism="Mechanical tendon stapler for zone-2 flexor tendon repair",
         indication="rare_disease", website="https://www.conextionsmed.com",
         outcome_hint="active",
         note="Novel implant for surgical flexor tendon repair; orthopaedics"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="deep_vein_medical", name="Deep Vein Medical",
         ticker=None,
         drug="DVT interventional device",
         mechanism="Venous insufficiency / deep vein thrombosis endovascular device",
         indication="cardiovascular", website="https://www.bioventuresinvestors.com",
         outcome_hint="active",
         note="Endovascular device for deep vein thrombosis / chronic venous disease"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="endotronix", name="Endotronix",
         ticker=None,
         drug="Cordella PA Sensor System",
         mechanism="Implantable wireless pulmonary artery pressure monitor (HF management)",
         indication="cardiovascular", website="https://endotronix.com",
         outcome_hint="active",
         note="Cordella PA sensor FDA study; remote PA pressure monitoring for heart failure"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="inseal_medical", name="InSeal Medical",
         ticker=None,
         drug="InSeal closure device",
         mechanism="Novel vascular closure device for post-procedural hemostasis",
         indication="cardiovascular", website=None,
         outcome_hint="active",
         note="Vascular/surgical sealing device"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="sono_motion", name="Sono Motion",
         ticker=None,
         drug="SonoMotion therapeutic ultrasound",
         mechanism="Therapeutic ultrasound for kidney stone / pain relief",
         indication="rare_disease", website="http://www.sonomotion.com",
         outcome_hint="active",
         note="Therapeutic ultrasound for kidney stone repositioning"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="uid_identification", name="UID Identification Solutions",
         ticker=None,
         drug="UID patient identification system",
         mechanism="Biometric/RFID patient identification and tracking device",
         indication="rare_disease", website="https://www.uidevices.com",
         outcome_hint="active",
         note="Patient identification device for hospital safety"),

    dict(firm="BioVentures MedTech Funds", cat="MedTech", slug="verax_biomedical", name="Verax Biomedical",
         ticker=None,
         drug="PGD Prime™ bacterial detection test",
         mechanism="Rapid pathogen detection assay for platelet blood safety testing",
         indication="rare_disease", website="https://www.veraxbiomedical.com",
         outcome_hint="active",
         note="Platelet pathogen detection; FDA cleared test for blood safety"),

    # ══════════════════════════════════════════════════════════════════════════
    # Pivotal Life Sciences  (38 companies)
    # ══════════════════════════════════════════════════════════════════════════
    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="addition_therapeutics", name="Addition Therapeutics",
         ticker=None,
         drug="ADD-001 (gene editing hemophilia)",
         mechanism="AAV gene editing / base editing for hemophilia A",
         indication="rare_disease", website="https://additiontx.com",
         outcome_hint="active",
         note="Gene editing platform for hemophilia; preclinical/IND"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="akouos", name="Akouos",
         ticker=None,
         drug="AK-OTOF (otoferlin gene therapy)",
         mechanism="AAV inner ear gene therapy for profound deafness (otoferlin mutations)",
         indication="rare_disease", website="https://www.akouos.com",
         outcome_hint="acquired",
         note="Acquired by Eli Lilly for $487M (2023); AAV inner ear gene therapy"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="aligos_therapeutics", name="Aligos Therapeutics",
         ticker="ALGS",
         drug="ALG-000184 (CAM inhibitor) + ALG-097558",
         mechanism="HBV core assembly machine (CAM) inhibitor + RNAi combination",
         indication="infectious", website="https://aligos.com",
         outcome_hint="active",
         note="HBV functional cure; CAM inhibitor + siRNA combination approach"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="alkahest", name="Alkahest",
         ticker=None,
         drug="ALK-001 / GRF6019 (plasma fraction)",
         mechanism="Plasma proteome fraction modulating aging and neurodegeneration",
         indication="neurology", website="https://www.alkahest.com",
         outcome_hint="acquired",
         note="Acquired by Grifols; plasma fractions for Alzheimer's / aging"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="anthos_therapeutics", name="Anthos Therapeutics",
         ticker=None,
         drug="Abelacimab (MAA868)",
         mechanism="Anti-FXI monoclonal antibody (Factor XI inhibitor, anticoagulation)",
         indication="cardiovascular", website="https://www.anthostherapeutics.com",
         outcome_hint="active",
         note="Abelacimab Phase 3 AZALEA-TIMI 71 for AFib; FXI inhibitor anticoagulant"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="arcutis_biotherapeutics", name="Arcutis Biotherapeutics",
         ticker="ARQT",
         drug="ZORYVE (roflumilast) cream/foam",
         mechanism="PDE4 inhibitor topical formulation for inflammatory skin diseases",
         indication="rare_disease", website="https://www.arcutis.com",
         outcome_hint="approved",
         note="FDA-approved Zoryve cream (plaque psoriasis) + foam (seborrheic dermatitis)"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="avalyn_pharma", name="Avalyn Pharma",
         ticker=None,
         drug="AP01 (inhaled pirfenidone) + AP02 (inhaled nintedanib)",
         mechanism="Inhaled pirfenidone/nintedanib formulation for pulmonary fibrosis",
         indication="rare_disease", website="https://www.avalynpharma.com",
         outcome_hint="active",
         note="IPO May 2026; MIST PPF study enrolling; inhaled formulation advantage"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="bioage_labs", name="BioAge Labs",
         ticker="BIOA",
         drug="BGE-102 (NLRP3 inhibitor)",
         mechanism="NLRP3 inflammasome inhibitor (oral brain-penetrant small molecule)",
         indication="metabolic", website="https://bioagelabs.com",
         outcome_hint="active",
         note="Pivoted from azelaprag failure; BGE-102 NLRP3 inhibitor in obesity/inflammation"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="bolt_biotherapeutics", name="Bolt Biotherapeutics",
         ticker="BOLT",
         drug="BDC-4182 (anti-HER2 TLR8 ISAC)",
         mechanism="TLR8 agonist immunostimulatory antibody conjugate (innate immunity + HER2)",
         indication="oncology", website="https://boltbio.com",
         outcome_hint="active",
         note="BDC-1001/3042 failed; BDC-4182 Phase 1 ongoing for HER2+ breast/GI cancers"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="cybin", name="Cybin (now Helus)",
         ticker=None,
         drug="CYB003 (deuterated psilocybin)",
         mechanism="Deuterated psilocybin 5-HT2A agonist (psychedelic-assisted therapy)",
         indication="neurology", website="https://www.helus.com",
         outcome_hint="active",
         note="Rebranded as Helus; CYB003 for major depressive disorder; Phase 2"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="engrail_therapeutics", name="Engrail Therapeutics",
         ticker=None,
         drug="ENR-001",
         mechanism="GABA-A receptor positive allosteric modulator (neuropsychiatric)",
         indication="neurology", website="https://engrailtherapeutics.com",
         outcome_hint="active",
         note="Neuropsychiatric pipeline; GABA receptor modulation"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="evommune", name="Evommune",
         ticker="EVOM",
         drug="EVO756 (MRGPRX2 inhibitor)",
         mechanism="MRGPRX2 receptor antagonist (mast cell activation, chronic inflammation)",
         indication="immunology", website="https://evommune.com",
         outcome_hint="active",
         note="MRGPRX2 inhibition in migraine + CSU; Phase 2 ongoing"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="exscientia", name="Exscientia",
         ticker="EXAI",
         drug="EXS74539 / GTAEXS617",
         mechanism="AI-designed androgen receptor / CDK7 small molecule inhibitor",
         indication="oncology", website="https://exscientia.ai",
         outcome_hint="acquired",
         note="Acquired by Recursion Pharmaceuticals 2024; AI drug design platform"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="parabilis_medicine", name="Parabilis Medicine (FogPharma)",
         ticker=None,
         drug="PBM-001 (stapled alpha-helix peptide)",
         mechanism="Hydrocarbon-stapled alpha-helical peptide KRAS/oncogene inhibitor",
         indication="oncology", website="https://parabilismed.com",
         outcome_hint="active",
         note="FogPharma rebranded as Parabilis; stapled peptide oncology pipeline"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="fusion_pharmaceuticals", name="Fusion Pharmaceuticals",
         ticker="FUSN",
         drug="FPI-2265 (225Ac-PSMA-I&T)",
         mechanism="Actinium-225 targeted alpha therapy (radiopharmaceutical, PSMA+)",
         indication="oncology", website="https://fusionpharma.com",
         outcome_hint="acquired",
         note="Acquired by AstraZeneca for $2.4B (2024); 225Ac-PSMA radiopharmaceutical"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="gossamer_bio", name="Gossamer Bio",
         ticker="GOSS",
         drug="Seralutinib",
         mechanism="Inhaled PDGFR/FGFR/CSF1R tyrosine kinase inhibitor (PAH)",
         indication="cardiovascular", website="https://gossamerbio.com",
         outcome_hint="failed_p3",
         note="PROSERA Phase 3 failed Feb 2026; 80% stock drop; class action lawsuit"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="gracell_biotechnologies", name="Gracell Biotechnologies",
         ticker="GRCL",
         drug="GC012F (BCMA×CD19 FasTCAR-T)",
         mechanism="Allogeneic dual-target FasTCAR-T cell therapy (BCMA + CD19)",
         indication="oncology", website="https://gracellbio.com",
         outcome_hint="acquired",
         note="Acquired by AstraZeneca for $2/share + CVR (Feb 2024); cell therapy"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="grail", name="GRAIL",
         ticker="GRAL",
         drug="Galleri multi-cancer early detection test",
         mechanism="cfDNA methylation sequencing + ML for multi-cancer early detection",
         indication="oncology", website="https://www.grail.com",
         outcome_hint="approved",
         note="Galleri test commercial launch; NHS-Galleri study; $300M Q1 2026 revenue run-rate"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="harmony_biosciences", name="Harmony Biosciences",
         ticker="HRMY",
         drug="WAKIX (pitolisant)",
         mechanism="Histamine H3 receptor inverse agonist / H3 antagonist (narcolepsy)",
         indication="neurology", website="https://harmonybiosciences.com",
         outcome_hint="approved",
         note="FDA-approved WAKIX for narcolepsy; $172.8M Q2 2024; $700-720M 2024 guidance"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="hotspot_therapeutics", name="HotSpot Therapeutics",
         ticker=None,
         drug="HST-1041",
         mechanism="Allosteric pocket binder (KRAS-independent / RAF kinase regulation)",
         indication="oncology", website="https://hotspottx.com",
         outcome_hint="active",
         note="Harnessing allosteric hotspots for small molecule oncology targets"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="immunome", name="Immunome",
         ticker="IMNM",
         drug="IMM-BCP-01 (anti-EGFL6 antibody)",
         mechanism="Survivor-derived monoclonal antibody targeting EGFL6 (tumor vasculature)",
         indication="oncology", website="https://www.immunome.com",
         outcome_hint="active",
         note="Phase 2 IMM-BCP-01 in breast cancer; human survivor antibody discovery platform"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="inozyme_pharma", name="Inozyme Pharma",
         ticker="INZY",
         drug="INZ-701",
         mechanism="ENPP1 recombinant enzyme replacement therapy (ABCC6/ENPP1 deficiency)",
         indication="rare_disease", website="https://inozymepharma.com",
         outcome_hint="active",
         note="ENPP1 deficiency (GACI, PXE); recombinant soluble ENPP1 ERT"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="io_biotech", name="IO Biotech",
         ticker="IOBT",
         drug="Cylembio (imsapepimut + etimupepimut / IO102-IO103)",
         mechanism="IDO/PD-L1/PD-L2 neoantigen peptide cancer vaccine (off-the-shelf)",
         indication="oncology", website="https://io-biotech.com",
         outcome_hint="failed_p3",
         note="FDA BLA rejected Sept 2025; COMBAT-301 Phase 3 negative; 50% layoffs"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="karuna_therapeutics", name="Karuna Therapeutics",
         ticker="KRTX",
         drug="Cobenfy (KarXT / xanomeline-trospium)",
         mechanism="M1/M4 muscarinic agonist + peripheral M2/M3 antagonist (schizophrenia)",
         indication="neurology", website="https://karunatx.com",
         outcome_hint="approved",
         note="FDA approved Sept 2024; acquired by BMS for $14B; first novel mechanism schizophrenia in 30 years"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="lb_pharmaceuticals", name="LB Pharmaceuticals",
         ticker=None,
         drug="LB-102",
         mechanism="N-acylated endogenous neurosteroid analog (GABA-A positive modulator)",
         indication="neurology", website="https://lbpharma.com",
         outcome_hint="active",
         note="Phase 2 LB-102 for schizophrenia and cannabis use disorder; GABAergic"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="lucy_therapeutics", name="Lucy Therapeutics",
         ticker=None,
         drug="LUT-001",
         mechanism="Mitochondrial electron transport chain (ETC) complex I activator",
         indication="neurology", website="https://lucytx.com",
         outcome_hint="active",
         note="Mitochondrial dysfunction in Angelman syndrome and Rett syndrome; Phase 1"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="maplight_therapeutics", name="MapLight Therapeutics",
         ticker=None,
         drug="ML-109",
         mechanism="AMPA receptor positive allosteric modulator (glutamate synaptic potentiator)",
         indication="neurology", website="https://maplighttx.com",
         outcome_hint="active",
         note="AMPA receptor PAM for depression/schizophrenia cognitive deficits; Phase 1"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="obsidian_therapeutics", name="Obsidian Therapeutics",
         ticker=None,
         drug="OBX-115 (IL-15 regulated TIL therapy)",
         mechanism="cytoDRiVE platform: drug-responsive domain (DRD) regulated IL-15 in TIL therapy",
         indication="oncology", website="https://obsidiantx.com",
         outcome_hint="active",
         note="OBX-115 Phase 2 data in melanoma at ASCO 2026; precision cell therapy"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="oculis", name="Oculis",
         ticker="OCS",
         drug="OCS-01 / Licaminlimab",
         mechanism="Dexamethasone nanoparticle eye drop / IL-4Rα antibody (atopic keratoconjunctivitis)",
         indication="rare_disease", website="https://oculis.com",
         outcome_hint="active",
         note="OCS-01 Phase 3 for post-op inflammation; licaminlimab Phase 2 for VKC/AKC"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="omniome", name="Omniome",
         ticker=None,
         drug="Sequencing by Binding (SBB) platform",
         mechanism="Polymerase-based DNA sequencing by binding (high accuracy NGS)",
         indication="diagnostics", website="https://pacificbiosciences.com",
         outcome_hint="acquired",
         note="Acquired by Pacific Biosciences (PacBio) for $303M (2021); NGS platform"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="plexium", name="Plexium",
         ticker=None,
         drug="PLX-4545 (SMARCA2 degrader)",
         mechanism="Monovalent SMARCA2 direct degrader (DELTA Discovery™ TPD platform)",
         indication="oncology", website="https://plexium.com",
         outcome_hint="active",
         note="SMARCA2 degrader PLX-4545 Phase 1; IKZF2 degrader PLX-2853 Phase 1; Pivotal-backed"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="rallybio", name="Rallybio",
         ticker="RLYB",
         drug="RLYB116 (anti-C5 antibody)",
         mechanism="C5 complement inhibitor monoclonal antibody (best-in-class SC dosing)",
         indication="rare_disease", website="https://rallybio.com",
         outcome_hint="active",
         note="RLYB116 Phase 2 PK/PD study; SC administration advantage in complement diseases"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="trevi_therapeutics", name="Trevi Therapeutics",
         ticker="TRVI",
         drug="Haduvio (nalbuphine ER)",
         mechanism="Kappa-opioid receptor agonist / mu-opioid receptor partial agonist (chronic cough)",
         indication="rare_disease", website="https://trevitherapeutics.com",
         outcome_hint="active",
         note="Haduvio for chronic cough in IPF; Phase 2b/3 PRISM trial"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="twist_bioscience_pls", name="Twist Bioscience",
         ticker="TWST",
         drug="Twist DNA synthesis + NGS libraries",
         mechanism="Silicon-based high-throughput DNA synthesis for NGS, synbio, antibody discovery",
         indication="diagnostics", website="https://twistbioscience.com",
         outcome_hint="approved",
         note="Nasdaq-listed; key synthetic biology enabler; revenue-generating"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="vaxcyte", name="Vaxcyte",
         ticker="PCVX",
         drug="VAX-31 (31-valent PCV) + VAX-24",
         mechanism="Cell-free protein synthesis pneumococcal conjugate vaccine (broad-spectrum)",
         indication="infectious", website="https://vaxcyte.com",
         outcome_hint="active",
         note="OPUS-1/2/3 Phase 3 trials enrolled; VAX-31 topline data expected Q4 2026"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="vericel", name="Vericel",
         ticker="VCEL",
         drug="MACI (autologous chondrocyte) + RECELL (epidermal system)",
         mechanism="Autologous cell therapy for cartilage repair + acute burn treatment",
         indication="rare_disease", website="https://www.vericel.com",
         outcome_hint="approved",
         note="FDA-approved MACI (knee cartilage) and RECELL (burns); revenue-generating"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="vigil_neuroscience", name="Vigil Neuroscience",
         ticker="VIGL",
         drug="VG-3927",
         mechanism="TREM2 agonist oral small molecule (microglial activator, Alzheimer's)",
         indication="neurology", website="https://vigilneuro.com",
         outcome_hint="acquired",
         note="Acquired by Sanofi for ~$600M (2024); TREM2 agonist approach to neuroinflammation"),

    dict(firm="Pivotal Life Sciences", cat="Biotech", slug="zenas_biopharma", name="Zenas BioPharma",
         ticker=None,
         drug="Obexelimab (ZB880)",
         mechanism="Anti-CD19 / FcgRIIb bispecific antibody (B-cell inhibitory checkpoint)",
         indication="immunology", website="https://www.zenaspharma.com",
         outcome_hint="active",
         note="Obexelimab Phase 3 for IgG4-related disease; anti-CD19/FcgRIIb bispecific"),

    # ══════════════════════════════════════════════════════════════════════════
    # Capital BioVentures — RIDGE Cohort  (8 companies)
    # ══════════════════════════════════════════════════════════════════════════
    dict(firm="Capital BioVentures", cat="RIDGE", slug="apiary_tx", name="Apiary TX",
         ticker=None,
         drug="Apitoxin-derived peptide therapeutics",
         mechanism="Antimicrobial peptide / apitoxin melittin derivatives for cancer/infection",
         indication="oncology", website="https://www.linkedin.com/company/apiarytx/",
         outcome_hint="active",
         note="Bee venom peptide therapeutics; antimicrobial + anti-tumor apitoxin derivatives"),

    dict(firm="Capital BioVentures", cat="RIDGE", slug="block_biosciences", name="Block Biosciences",
         ticker=None,
         drug="BLK-001 (purine biosynthesis inhibitor)",
         mechanism="Purine biosynthesis pathway inhibitor preventing brain metastasis initiation",
         indication="oncology", website="https://www.blockbiosciences.com",
         outcome_hint="active",
         note="First-in-class brain metastasis prevention; BBB-permeable purine inhibitor"),

    dict(firm="Capital BioVentures", cat="RIDGE", slug="copoly_ai", name="Copoly AI",
         ticker=None,
         drug="OncoSage AI diagnostics platform",
         mechanism="AI/ML cancer diagnostics and bioinformatics (early cancer detection)",
         indication="oncology", website="https://copoly.ai",
         outcome_hint="active",
         note="AI cancer diagnostics; OncoSage platform for early detection"),

    dict(firm="Capital BioVentures", cat="RIDGE", slug="esphera_synbio", name="Esphera Synbio",
         ticker=None,
         drug="Synthetic biology platform",
         mechanism="Synthetic biology for therapeutic protein / biologic production",
         indication="rare_disease", website="https://espherasynbio.ca",
         outcome_hint="active",
         note="Synthetic biology platform company; early stage"),

    dict(firm="Capital BioVentures", cat="RIDGE", slug="hylidx", name="HyliDx (Hylid Diagnostics)",
         ticker=None,
         drug="HyliDx home testing system",
         mechanism="Finger-prick home lab-accurate testing for CKD/HF biomarkers (creatinine, BNP)",
         indication="cardiovascular", website="https://hylidx.com",
         outcome_hint="active",
         note="CKD/HF home diagnostics; saves $50B in treatment costs; Ottawa Canada"),

    dict(firm="Capital BioVentures", cat="RIDGE", slug="tessella_biosciences", name="Tessella Biosciences",
         ticker=None,
         drug="TissuGel bioink materials",
         mechanism="High-fidelity 3D bioprinting extrusion bioinks (GelMA, PEGDA, collagen)",
         indication="rare_disease", website="https://www.tessellabio.com",
         outcome_hint="active",
         note="3D bioprinting bioinks for tissue engineering; McMaster University spinout"),

    dict(firm="Capital BioVentures", cat="RIDGE", slug="total_flow_medical", name="Total Flow Medical",
         ticker=None,
         drug="Cardiopulmonary bypass device",
         mechanism="Clinician-designed cardiopulmonary bypass / perfusion improvement device",
         indication="cardiovascular", website="https://www.totalflowmedical.com",
         outcome_hint="active",
         note="Vancouver-based; addressing unmet needs in cardiopulmonary bypass"),

    dict(firm="Capital BioVentures", cat="RIDGE", slug="virano_therapeutics", name="Virano Therapeutics",
         ticker=None,
         drug="VEPOs (immune modulating small molecules)",
         mechanism="Genetic immunomodulation: VEPOs + gene delivery for solid tumors/genetic diseases",
         indication="oncology", website="https://www.viranotx.com",
         outcome_hint="active",
         note="Reprogramming immune responses for solid tumors; Toronto/Ottawa Ontario"),

    # ══════════════════════════════════════════════════════════════════════════
    # Capital BioVentures — ASCENT Cohort 1  (10 companies)
    # ══════════════════════════════════════════════════════════════════════════
    dict(firm="Capital BioVentures", cat="ASCENT", slug="optimeyes_2020", name="2020 OptiMEyes",
         ticker=None,
         drug="2020-001 retinal therapy",
         mechanism="Ophthalmic device / retinal therapy (visual restoration)",
         indication="rare_disease", website="https://www.2020optimeyes.ca",
         outcome_hint="active",
         note="Ophthalmic / retinal device company; Ottawa Canada"),

    dict(firm="Capital BioVentures", cat="ASCENT", slug="atorvia", name="Atorvia",
         ticker=None,
         drug="ATR-001",
         mechanism="Undisclosed therapeutic mechanism",
         indication="rare_disease", website="https://www.atorvia.co",
         outcome_hint="active",
         note="Early-stage Ottawa biotech; undisclosed mechanism"),

    dict(firm="Capital BioVentures", cat="ASCENT", slug="cerebrotx", name="CerebroTX",
         ticker=None,
         drug="CRB-001 (undisclosed CNS asset)",
         mechanism="CNS neurological therapeutic (undisclosed mechanism)",
         indication="neurology", website="https://www.cerebrotx.com",
         outcome_hint="active",
         note="CNS neurological therapeutics; Ottawa Canada"),

    dict(firm="Capital BioVentures", cat="ASCENT", slug="cura_therapeutics", name="Cura Therapeutics",
         ticker=None,
         drug="Undisclosed LNP delivery platform",
         mechanism="Lipid nanoparticle (LNP) RNA / small molecule delivery system",
         indication="rare_disease", website="https://www.curatherapeutics.com",
         outcome_hint="active",
         note="LNP-based RNA delivery platform; rare disease focus"),

    dict(firm="Capital BioVentures", cat="ASCENT", slug="eder_tx", name="EdER TX",
         ticker=None,
         drug="Undisclosed rare disease asset",
         mechanism="Rare disease / gene therapy (undisclosed mechanism)",
         indication="rare_disease", website="https://www.edertx.ca",
         outcome_hint="active",
         note="Rare disease therapeutic; Ottawa accelerator company"),

    dict(firm="Capital BioVentures", cat="ASCENT", slug="fibrodynamx", name="FibroDynamX",
         ticker=None,
         drug="FDX-001 (anti-fibrotic)",
         mechanism="TGF-beta / fibrosis pathway inhibitor (anti-fibrotic compound)",
         indication="rare_disease", website="https://www.fibrodynamx.com",
         outcome_hint="active",
         note="Anti-fibrotic therapeutic targeting TGF-beta pathway"),

    dict(firm="Capital BioVentures", cat="ASCENT", slug="iglutherapeutics", name="Iglutherapeutics",
         ticker=None,
         drug="IGL-001",
         mechanism="Undisclosed therapeutic mechanism",
         indication="rare_disease", website="https://iglutherapeutics.com",
         outcome_hint="active",
         note="Early-stage biotech; Ottawa Canada"),

    dict(firm="Capital BioVentures", cat="ASCENT", slug="inspire_btx", name="Inspire BTX",
         ticker=None,
         drug="Undisclosed biologic",
         mechanism="Undisclosed biological therapeutic",
         indication="rare_disease", website="https://www.inspirebtx.com",
         outcome_hint="active",
         note="Early-stage biotech; Ottawa Canada accelerator"),

    dict(firm="Capital BioVentures", cat="ASCENT", slug="i_rna_therapeutics", name="i-RNA Therapeutics",
         ticker=None,
         drug="lncRNA-targeting RNAi eyedrop",
         mechanism="lncRNA-targeting RNAi ophthalmic delivery (topical ocular siRNA)",
         indication="rare_disease", website="https://www.i-rna.ca",
         outcome_hint="active",
         note="lncRNA-targeting siRNA ophthalmic delivery; Ottawa Canada"),
]


# ── API helpers ────────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None) -> Any:
    """Fetch JSON from URL with simple rate-limiting."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception as e:
        log.debug("GET %s failed: %s", url, e)
        return {}
    finally:
        time.sleep(RATE)


def fetch_ct_trials(company: str, drug: str, max_r: int = 12) -> list[dict]:
    """Query ClinicalTrials.gov v2 API for a company/drug."""
    query = f"{company} {drug}"[:120]
    data = _get(CT_API, {
        "query.term": query,
        "fields": "NCTId,BriefTitle,OverallStatus,Phase,BriefSummary,"
                  "Condition,InterventionName,StartDate,CompletionDate,WhyStopped",
        "pageSize": max_r,
        "format": "json",
    })
    studies = data.get("studies", [])
    results = []
    for s in studies:
        proto = s.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        desc   = proto.get("descriptionModule", {})
        cond   = proto.get("conditionsModule", {})
        arms   = proto.get("armsInterventionsModule", {})
        results.append({
            "nct_id":     ident.get("nctId", ""),
            "title":      ident.get("briefTitle", ""),
            "status":     status.get("overallStatus", ""),
            "why_stopped":status.get("whyStopped", ""),
            "phases":     design.get("phases", []),
            "summary":    desc.get("briefSummary", "")[:800],
            "conditions": cond.get("conditions", []),
            "interventions": [i.get("name", "") for i in
                              arms.get("interventions", [])[:3]],
        })
    return results


def fetch_pubmed(drug: str, company: str, max_r: int = 5) -> str:
    """Fetch PubMed abstracts for drug + company; returns concatenated text."""
    query = f"{drug}[tiab] AND {company}[tiab]"
    esearch = _get(PM_ESEARCH, {
        "db": "pubmed", "term": query, "retmax": max_r,
        "retmode": "json", "sort": "relevance",
    })
    ids = (esearch.get("esearchresult") or {}).get("idlist", [])
    if not ids:
        query2 = f"{drug}[tiab] clinical trial"
        esearch2 = _get(PM_ESEARCH, {
            "db": "pubmed", "term": query2, "retmax": max_r,
            "retmode": "json", "sort": "relevance",
        })
        ids = (esearch2.get("esearchresult") or {}).get("idlist", [])
    if not ids:
        return ""
    efetch = _get(PM_EFETCH, {
        "db": "pubmed", "id": ",".join(ids[:max_r]),
        "retmode": "text", "rettype": "abstract",
    })
    return str(efetch)[:4000]


def load_existing_text(slug: str) -> str:
    """Load existing downloaded text from data/slides/portfolio/{slug}/."""
    co_dir = PORTFOLIO_DIR / slug
    if not co_dir.exists():
        return ""
    chunks = []
    for sub in ["ct_gov", "pubmed", "gnw"]:
        for f in sorted((co_dir / sub).glob("*.txt") if (co_dir / sub).exists() else []):
            try:
                chunks.append(f.read_text(errors="ignore")[:4000])
            except OSError:
                pass
    for f in sorted(co_dir.glob("site_*.txt")):
        try:
            chunks.append(f.read_text(errors="ignore")[:3000])
        except OSError:
            pass
    return "\n\n".join(chunks)[:40_000]


# ── Stage/outcome derivation ──────────────────────────────────────────────────

_PHASE_ORDER = {"PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4, "NA": 0}

def _max_phase(trials: list[dict]) -> str:
    best = 0
    for t in trials:
        for p in t.get("phases", []):
            best = max(best, _PHASE_ORDER.get(p, 0))
    return {0: "unknown", 1: "phase1", 2: "phase2", 3: "phase3", 4: "approved"}.get(best, "unknown")


def derive_stage_outcome(co: dict, trials: list[dict]) -> tuple[str, str, str]:
    """Returns (clinical_stage, outcome, decision)."""
    hint = co.get("outcome_hint", "active")
    max_stage = _max_phase(trials) if trials else "preclinical"
    if max_stage == "unknown":
        max_stage = "preclinical"

    if hint == "approved":
        return "approved", "approved", "go"
    if hint == "acquired":
        return max_stage if max_stage != "preclinical" else "phase2", "ongoing", "acquired"
    if hint == "failed_p3":
        return "phase3", "discontinued_p3", "no-go"
    if hint == "failed_p2":
        return "phase2", "discontinued_p2", "no-go"

    # Preclinical / HealthTech / Devices
    if co.get("cat") in ("HealthTech", "MedTech", "MedDevice", "RIDGE", "ASCENT"):
        if hint == "approved":
            return "approved", "approved", "go"
        if max_stage == "preclinical":
            return "preclinical", "ongoing", "undecided"

    # Active — check termination in CT trials
    n_total = len(trials)
    n_term = sum(1 for t in trials if t.get("status", "") in ("TERMINATED", "WITHDRAWN"))
    if n_total > 0 and n_term / n_total >= 0.6:
        worst = max_stage
        out_key = f"discontinued_{worst.replace('phase', 'p')}" if worst.startswith("phase") else "discontinued"
        return worst, out_key, "no-go"

    return max_stage, "ongoing", "go" if any(
        t.get("status") in ("RECRUITING", "ACTIVE_NOT_RECRUITING") for t in trials
    ) else "undecided"


# ── Record builder ────────────────────────────────────────────────────────────

def build_record(co: dict, trials: list[dict], pm_text: str, existing: str) -> RawRecord:
    stage, outcome, decision = derive_stage_outcome(co, trials)

    # Build rich raw_text
    sections = []
    if co.get("drug"):
        sections.append(f"Drug: {co['drug']}")
    if co.get("mechanism"):
        sections.append(f"Mechanism: {co['mechanism']}")
    if co.get("note"):
        sections.append(f"Notes: {co['note']}")
    for t in trials[:6]:
        sections.append(
            f"Trial {t['nct_id']}: {t['title']} "
            f"[{t['status']}] Phase {'/'.join(t['phases'])} "
            f"— {t['summary'][:300]}"
            + (f" Why stopped: {t['why_stopped']}" if t.get("why_stopped") else "")
        )
    if pm_text:
        sections.append("PubMed:\n" + pm_text[:3000])
    if existing:
        sections.append("Downloaded materials:\n" + existing[:8000])

    raw_text = "\n\n".join(sections)[:60_000]

    return RawRecord(
        source=DB_SOURCE,
        source_id=co["slug"],
        url=co.get("website") or f"https://{co['slug'].replace('_', '')}.com",
        title=f"{co['name']} — {co['drug']}",
        indication=co["indication"],
        mechanism=co["mechanism"],
        clinical_stage=stage,
        decision=decision,
        outcome=outcome,
        investment_usd=0.0,
        raw_text=raw_text,
        extra={
            "firm": co["firm"],
            "category": co.get("cat", "Biotech"),
            "ticker": co.get("ticker"),
            "drug": co["drug"],
            "note": co.get("note", ""),
            "ct_n": len(trials),
            "ct_terminated": sum(1 for t in trials if t.get("status") in ("TERMINATED", "WITHDRAWN")),
            "ct_recruiting": sum(1 for t in trials if t.get("status") in
                                 ("RECRUITING", "ACTIVE_NOT_RECRUITING")),
        },
    )


# ── HTML report ───────────────────────────────────────────────────────────────

_SCORE_COLORS = [(0.70, "#22c55e"), (0.50, "#84cc16"), (0.35, "#f59e0b"), (0.00, "#ef4444")]

def _col(p: float) -> str:
    for t, c in _SCORE_COLORS:
        if p >= t:
            return c
    return "#ef4444"


def _bar_svg(values: list[tuple[str, float]], title: str, w: int = 600) -> str:
    if not values:
        return ""
    mx = max(v for _, v in values) or 1e-9
    rh, pl, pr = 22, 240, 70
    cw = w - pl - pr
    th = len(values) * rh + 50
    bars = []
    for i, (n, v) in enumerate(values):
        y = i * rh + 30
        bw = int(v / mx * cw)
        bars.append(
            f'<text x="{pl-6}" y="{y+14}" text-anchor="end" font-size="11" fill="#555">{n[:38]}</text>'
            f'<rect x="{pl}" y="{y}" width="{bw}" height="16" fill="#6366f1" rx="2"/>'
            f'<text x="{pl+bw+4}" y="{y+13}" font-size="10" fill="#888">{v:.3f}</text>'
        )
    return (
        f'<svg width="{w}" height="{th}" xmlns="http://www.w3.org/2000/svg">'
        f'<text x="{w//2}" y="18" text-anchor="middle" font-size="13" font-weight="bold" fill="#374151">'
        f'{title}</text>' + "".join(bars) + "</svg>"
    )


def _scatter_svg(results: list[dict]) -> str:
    W, H, P = 680, 460, 55
    cw, ch = W - P * 2, H - P * 2 - 20
    dots = []
    for r in results:
        p  = r["p_success"]
        ct_n = r.get("ct_n", 0)
        ct_t = r.get("ct_terminated", 0)
        health = 1 - (ct_t / ct_n if ct_n > 0 else 0)
        cx = int(P + p * cw)
        cy = int(P + 20 + (1 - health) * ch)
        col = _col(p)
        lbl = r["name"][:12]
        dots.append(
            f'<circle cx="{cx}" cy="{cy}" r="5" fill="{col}" fill-opacity="0.8" '
            f'stroke="#fff" stroke-width="1"><title>{r["name"]} — {r["firm"]}\nP={p:.1%}</title></circle>'
            f'<text x="{cx+7}" y="{cy+4}" font-size="8" fill="#374151">{lbl}</text>'
        )
    axes = (
        f'<line x1="{P}" y1="{P+20}" x2="{P}" y2="{H-P}" stroke="#9ca3af"/>'
        f'<line x1="{P}" y1="{H-P}" x2="{W-P}" y2="{H-P}" stroke="#9ca3af"/>'
        f'<text x="{W//2}" y="{H-8}" text-anchor="middle" font-size="11" fill="#6b7280">P(success)</text>'
        f'<text x="14" y="{H//2}" text-anchor="middle" font-size="11" fill="#6b7280" '
        f'transform="rotate(-90 14 {H//2})">Trial Health</text>'
        f'<text x="{P}" y="{H-P+14}" font-size="9" text-anchor="middle" fill="#9ca3af">0%</text>'
        f'<text x="{P+cw//2}" y="{H-P+14}" font-size="9" text-anchor="middle" fill="#9ca3af">50%</text>'
        f'<text x="{P+cw}" y="{H-P+14}" font-size="9" text-anchor="middle" fill="#9ca3af">100%</text>'
        f'<line x1="{P+cw//2}" y1="{P+20}" x2="{P+cw//2}" y2="{H-P}" '
        f'stroke="#f59e0b" stroke-dasharray="4,3" stroke-width="1.2"/>'
    )
    title = (f'<text x="{W//2}" y="16" text-anchor="middle" font-size="13" '
             f'font-weight="bold" fill="#374151">Risk Matrix: P(success) vs Trial Health</text>')
    return (f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg">'
            + title + axes + "".join(dots) + "</svg>")


def _firm_row(firm: str, cos: list[dict]) -> str:
    avg = sum(c["p_success"] for c in cos) / len(cos)
    go  = sum(1 for c in cos if c["verdict"] == "GO")
    acq = sum(1 for c in cos if c.get("outcome_hint") == "acquired")
    app = sum(1 for c in cos if c.get("outcome_hint") == "approved")
    fail = sum(1 for c in cos if "failed" in c.get("outcome_hint", ""))
    col = _col(avg)
    return (
        f'<tr>'
        f'<td><b>{firm}</b></td><td>{len(cos)}</td>'
        f'<td style="color:{col};font-weight:bold">{avg:.1%}</td>'
        f'<td style="color:#22c55e">{go}</td>'
        f'<td style="color:#6366f1">{acq}</td>'
        f'<td style="color:#f59e0b">{app}</td>'
        f'<td style="color:#ef4444">{fail}</td>'
        f'</tr>'
    )


def _company_card(r: dict) -> str:
    p   = r["p_success"]
    col = _col(p)
    vrd = r["verdict"]
    vbg = "#dcfce7" if vrd == "GO" else "#fee2e2"
    vcl = "#166534" if vrd == "GO" else "#991b1b"
    ohint = r.get("outcome_hint", "active")
    badge = ""
    if ohint == "approved":
        badge = '<span style="background:#ede9fe;color:#5b21b6;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600">APPROVED</span>'
    elif ohint == "acquired":
        badge = '<span style="background:#dbeafe;color:#1e40af;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600">ACQUIRED</span>'
    elif "failed" in ohint:
        badge = f'<span style="background:#fee2e2;color:#991b1b;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600">FAILED PH{ohint[-1].upper()}</span>'

    ct_n = r.get("ct_n", 0)
    ct_t = r.get("ct_terminated", 0)
    ct_r = r.get("ct_recruiting", 0)
    ct_bar = ""
    if ct_n > 0:
        tw = int(ct_t / ct_n * 100)
        rw = int(ct_r / ct_n * 100)
        ct_bar = (
            f'<div style="margin:4px 0;font-size:11px">'
            f'<b>Trials:</b> {ct_n} total &nbsp;·&nbsp; '
            f'<span style="color:#ef4444">{ct_t} terminated</span> &nbsp;·&nbsp; '
            f'<span style="color:#6366f1">{ct_r} recruiting</span></div>'
            f'<div style="height:5px;background:#e5e7eb;border-radius:3px;overflow:hidden;margin:2px 0 6px">'
            f'<div style="display:flex;height:100%">'
            f'<div style="width:{tw}%;background:#ef4444"></div>'
            f'<div style="width:{rw}%;background:#6366f1"></div>'
            f'</div></div>'
        )

    note = r.get("note", "")
    summary = r.get("summary", "")[:280]

    return f"""
  <div style="background:#fff;border:1px solid #e5e7eb;border-left:4px solid {col};
       border-radius:6px;padding:14px;margin:8px 0;box-shadow:0 1px 3px #0001">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">
      <div style="flex:1;min-width:200px">
        <div style="font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em">
          {r['firm']} · {r.get('category', '')}
        </div>
        <div style="font-size:15px;font-weight:700;margin:2px 0">
          {r['name']}
          {'<span style="font-size:11px;color:#6b7280;font-weight:400"> (' + r['ticker'] + ')</span>' if r.get('ticker') else ''}
          {'&nbsp;' + badge if badge else ''}
        </div>
        <div style="font-size:11px;color:#374151;margin:2px 0">{r['drug']}</div>
        <div style="font-size:10px;color:#6b7280">{r['mechanism'][:120]}</div>
      </div>
      <div style="text-align:right;min-width:80px">
        <div style="font-size:26px;font-weight:bold;color:{col}">{p:.1%}</div>
        <div style="background:{vbg};color:{vcl};border-radius:4px;padding:2px 8px;
             font-weight:bold;font-size:12px;text-align:center">{vrd}</div>
        <div style="font-size:10px;color:#9ca3af;margin-top:2px">
          {r.get('clinical_stage','').replace('_',' ')}</div>
      </div>
    </div>
    {ct_bar}
    {('<div style="font-size:11px;color:#374151;margin:4px 0">' + note + '</div>') if note else ''}
    {('<details style="margin-top:4px"><summary style="cursor:pointer;font-size:11px;color:#6366f1">Model summary</summary>'
       '<div style="font-size:11px;background:#f9fafb;padding:8px;border-radius:4px;margin-top:4px">'
       + summary + '</div></details>') if summary else ''}
  </div>"""


def generate_report(
    metrics: dict,
    fi: list[tuple[str, float]],
    results: list[dict],
    db_stats: dict,
) -> str:
    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Stats
    n_total = len(results)
    n_go    = sum(1 for r in results if r["verdict"] == "GO")
    n_acq   = sum(1 for r in results if r.get("outcome_hint") == "acquired")
    n_app   = sum(1 for r in results if r.get("outcome_hint") == "approved")
    n_fail  = sum(1 for r in results if "failed" in r.get("outcome_hint", ""))
    avg_p   = sum(r["p_success"] for r in results) / n_total

    firms   = defaultdict(list)
    for r in results:
        firms[r["firm"]].append(r)

    fi_svg  = _bar_svg(fi[:25], "Top 25 Feature Importances", 640)
    risk_svg = _scatter_svg(results)

    firm_rows_html = "".join(_firm_row(f, c) for f, c in sorted(firms.items()))

    # Firm sections
    firm_sections = []
    for firm, cos in sorted(firms.items()):
        cards = "".join(_company_card(c)
                        for c in sorted(cos, key=lambda x: -x["p_success"]))
        avg = sum(c["p_success"] for c in cos) / len(cos)
        col = _col(avg)
        cat_groups = defaultdict(list)
        for c in cos:
            cat_groups[c.get("category", "Biotech")].append(c)
        firm_sections.append(f"""
    <details id="firm-{firm.replace(' ', '_')}" open>
      <summary style="cursor:pointer;background:#f8fafc;border:1px solid #e5e7eb;
               border-radius:8px;padding:12px 16px;font-size:16px;font-weight:700;
               display:flex;justify-content:space-between;align-items:center;list-style:none">
        <span>{firm} <span style="font-size:12px;font-weight:400;color:#6b7280">
          ({len(cos)} companies)</span></span>
        <span style="color:{col};font-size:14px">{avg:.1%} avg P(success)</span>
      </summary>
      <div style="margin-top:4px">{cards}</div>
    </details>""")

    # Training block
    if "auc_roc" in metrics:
        train_block = f"""
    <div style="display:flex;gap:20px;flex-wrap:wrap;margin:12px 0">
      <div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;
           padding:14px;text-align:center;min-width:130px">
        <div style="font-size:28px;font-weight:bold;color:#16a34a">{metrics['auc_roc']:.3f}</div>
        <div style="font-size:11px;color:#15803d">AUC-ROC</div></div>
      <div style="background:#eff6ff;border:1px solid #93c5fd;border-radius:8px;
           padding:14px;text-align:center;min-width:130px">
        <div style="font-size:28px;font-weight:bold;color:#1d4ed8">{metrics['accuracy']:.3f}</div>
        <div style="font-size:11px;color:#1e40af">Accuracy</div></div>
      <div style="background:#faf5ff;border:1px solid #c4b5fd;border-radius:8px;
           padding:14px;text-align:center;min-width:130px">
        <div style="font-size:28px;font-weight:bold;color:#7c3aed">{metrics.get('n_train',0):,}</div>
        <div style="font-size:11px;color:#6d28d9">Train samples</div></div>
      <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:8px;
           padding:14px;text-align:center;min-width:130px">
        <div style="font-size:28px;font-weight:bold;color:#c2410c">{metrics.get('n_test',0):,}</div>
        <div style="font-size:11px;color:#9a3412">Test samples</div></div>
    </div>"""
    else:
        train_block = f"""<div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:12px">
    Model training skipped — {metrics.get('n',0)} labelled samples. Predictions use Bayesian priors only.</div>"""

    db_li = "".join(f"<li>{s}: <b>{n:,}</b></li>" for s, n in
                    sorted(db_stats.items(), key=lambda x: -x[1]) if s != "total_records")

    # Compact full company table
    sorted_all = sorted(results, key=lambda x: -x["p_success"])
    table_rows = []
    for r in sorted_all:
        p = r["p_success"]
        col = _col(p)
        oh = r.get("outcome_hint", "active")
        oh_badge = {"approved": "🟢 APPRVD", "acquired": "🔵 ACQ",
                    "failed_p3": "🔴 PH3 FAIL", "failed_p2": "🟡 PH2 FAIL",
                    "active": "🟡 Active", "preclinical": "⚪ Pre"}.get(oh, oh)
        ticker_span = (f'&nbsp;<span style="color:#6b7280;font-size:10px">({r["ticker"]})</span>'
                       if r.get("ticker") else "")
        table_rows.append(
            f'<tr>'
            f'<td style="font-size:12px">{r["firm"]}</td>'
            f'<td style="font-size:12px;font-weight:600">{r["name"]}{ticker_span}</td>'
            f'<td style="font-size:11px;color:#6b7280">{r["drug"][:45]}</td>'
            f'<td style="font-size:11px">{r["indication"]}</td>'
            f'<td style="font-size:11px">{r.get("clinical_stage","").replace("_"," ")}</td>'
            f'<td style="font-size:12px;font-weight:bold;color:{col}">{p:.1%}</td>'
            f'<td style="font-size:11px">{oh_badge}</td>'
            f'</tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioVentures Full Portfolio Analysis — {now}</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       margin:0;background:#f8fafc;color:#1f2937;line-height:1.5}}
  .wrap{{max-width:1020px;margin:0 auto;padding:24px 16px}}
  h1{{font-size:22px;margin:0 0 4px}}
  h2{{font-size:17px;border-bottom:2px solid #e5e7eb;padding-bottom:6px;margin-top:28px}}
  table{{border-collapse:collapse;width:100%;font-size:13px}}
  th{{background:#f3f4f6;text-align:left;padding:7px 10px;border-bottom:2px solid #e5e7eb;
      font-size:12px;white-space:nowrap}}
  td{{padding:5px 8px;border-bottom:1px solid #f3f4f6;vertical-align:top}}
  tr:hover td{{background:#f9fafb}}
  details summary{{outline:none;-webkit-user-select:none;user-select:none}}
  details summary::-webkit-details-marker{{display:none}}
  .pill{{display:inline-block;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600;margin:2px}}
  .toc{{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}}
  .toc a{{color:#6366f1;text-decoration:none;font-size:13px;padding:4px 12px;
          background:#f0f0ff;border-radius:6px}}
  .toc a:hover{{background:#e0e0ff}}
  @media(max-width:600px){{.wrap{{padding:12px 8px}} h1{{font-size:18px}}}}
</style>
</head>
<body>
<div class="wrap">

<h1>BioVentures Full Portfolio Analysis</h1>
<p style="color:#6b7280;font-size:13px">
  Generated {now} &nbsp;·&nbsp; {n_total} companies · 5 VC firms &nbsp;·&nbsp;
  {db_stats.get('total_records',0):,} total training records
</p>

<div class="toc">
  <a href="#overview">Overview</a>
  <a href="#model">Model</a>
  <a href="#features">Features</a>
  <a href="#matrix">Risk Matrix</a>
  <a href="#firms">By Firm</a>
  <a href="#table">Full Table</a>
  <a href="#detail">Company Cards</a>
</div>

<!-- OVERVIEW -->
<h2 id="overview">Portfolio Overview</h2>
<div style="display:flex;flex-wrap:wrap;gap:10px;margin:12px 0">
  <span class="pill" style="background:#dcfce7;color:#166534">{n_go} GO ({n_go/n_total:.0%})</span>
  <span class="pill" style="background:#fee2e2;color:#991b1b">{n_total-n_go} NO-GO ({(n_total-n_go)/n_total:.0%})</span>
  <span class="pill" style="background:#ede9fe;color:#5b21b6">{n_app} Approved</span>
  <span class="pill" style="background:#dbeafe;color:#1e40af">{n_acq} Acquired</span>
  <span class="pill" style="background:#fee2e2;color:#991b1b">{n_fail} Phase 3 failures</span>
  <span class="pill" style="background:#f3f4f6;color:#374151">Avg P(success) = {avg_p:.1%}</span>
</div>

<!-- MODEL -->
<h2 id="model">Model Training</h2>
{train_block}
<details style="margin-top:8px">
  <summary style="cursor:pointer;font-size:12px;color:#6b7280">DB source breakdown</summary>
  <ul style="font-size:12px;columns:2;margin-top:6px">{db_li}</ul>
</details>

<!-- FEATURES -->
<h2 id="features">Feature Importances (Gradient Boosting)</h2>
<div style="overflow-x:auto">{fi_svg}</div>

<!-- RISK MATRIX -->
<h2 id="matrix">Risk Matrix</h2>
<div style="overflow-x:auto">{risk_svg}</div>
<p style="font-size:11px;color:#9ca3af">
  Each dot = one portfolio company. X = P(success), Y = trial health (1 − termination rate).
  Hover for company name. Dashed line at 50% threshold.
</p>

<!-- FIRM TABLE -->
<h2 id="firms">Firm Summary</h2>
<table><thead><tr>
  <th>Firm</th><th>Count</th><th>Avg P</th>
  <th>GO</th><th>Acquired</th><th>Approved</th><th>Ph3 Fail</th>
</tr></thead><tbody>{firm_rows_html}</tbody></table>

<!-- FULL TABLE -->
<h2 id="table">All Companies — Ranked by P(success)</h2>
<div style="overflow-x:auto">
<table><thead><tr>
  <th>Firm</th><th>Company</th><th>Drug / Asset</th>
  <th>Indication</th><th>Stage</th><th>P(success)</th><th>Status</th>
</tr></thead><tbody>{"".join(table_rows)}</tbody></table>
</div>

<!-- COMPANY CARDS -->
<h2 id="detail">Detailed Company Cards (by firm)</h2>
{"".join(firm_sections)}

</div>
</body></html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== BioVentures Full Portfolio Analysis (%d companies) ===",
             len(FULL_PORTFOLIO))

    log.info("Step 1 — Fetching ClinicalTrials.gov + PubMed for each company…")
    records: list[RawRecord] = []
    co_trials: dict[str, list[dict]] = {}

    for i, co in enumerate(FULL_PORTFOLIO):
        slug = co["slug"]
        log.info("  [%d/%d] %s", i + 1, len(FULL_PORTFOLIO), co["name"])

        # CT trials
        trials = fetch_ct_trials(co["name"], co["drug"])
        co_trials[slug] = trials

        # PubMed
        drug_q = re.sub(r"\(.*?\)", "", co["drug"]).strip()
        pm_text = fetch_pubmed(drug_q, co["name"])

        # Existing downloaded text
        existing = load_existing_text(slug)

        rec = build_record(co, trials, pm_text, existing)
        records.append(rec)

    log.info("Step 2 — Upserting %d records into DB…", len(records))
    from src.storage.database import DB_PATH, _load, _save
    db = _load(DB_PATH)
    for k in list(db["projects"]):
        if k.startswith(f"{DB_SOURCE}::"):
            del db["projects"][k]
    _save(db, DB_PATH)
    bulk_upsert(records)

    log.info("Step 3 — Training SuccessPredictor…")
    model = SuccessPredictor()
    metrics = model.train()
    log.info("  Metrics: %s", metrics)

    log.info("Step 4 — Scoring all companies…")
    results = []
    for co, rec in zip(FULL_PORTFOLIO, records):
        row = {
            "title":          rec.title,
            "indication":     rec.indication,
            "mechanism":      rec.mechanism,
            "clinical_stage": rec.clinical_stage,
            "outcome":        rec.outcome,
            "raw_text":       rec.raw_text[:20_000],
        }
        expl = model.explain(row)
        slug = co["slug"]
        trials = co_trials.get(slug, [])
        extra = rec.extra
        results.append({
            **expl,
            "slug":         slug,
            "name":         co["name"],
            "firm":         co["firm"],
            "category":     co.get("cat", "Biotech"),
            "ticker":       co.get("ticker"),
            "drug":         co["drug"],
            "mechanism":    co["mechanism"],
            "indication":   co["indication"],
            "clinical_stage": rec.clinical_stage,
            "outcome_hint": co["outcome_hint"],
            "note":         co.get("note", ""),
            "ct_n":         extra.get("ct_n", 0),
            "ct_terminated":extra.get("ct_terminated", 0),
            "ct_recruiting":extra.get("ct_recruiting", 0),
        })

    log.info("Step 5 — Generating HTML report…")
    all_rows = fetch_all()
    from collections import Counter
    src_c = Counter(r.get("source", "?") for r in all_rows)
    db_stats = {"total_records": len(all_rows)}
    db_stats.update(src_c)

    fi = model.feature_importance()
    html = generate_report(metrics, fi, results, db_stats)
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text(html, encoding="utf-8")
    log.info("  Report → %s (%d KB)", REPORT_OUT, len(html) // 1024)

    # Console summary
    print("\n" + "═" * 66)
    print("  BioVentures Full Portfolio Analysis — Results")
    print("═" * 66)
    if "auc_roc" in metrics:
        print(f"  AUC: {metrics['auc_roc']:.3f}  |  Accuracy: {metrics['accuracy']:.3f}  "
              f"|  Train: {metrics.get('n_train',0):,}  Test: {metrics.get('n_test',0):,}")

    by_firm = defaultdict(list)
    for r in results:
        by_firm[r["firm"]].append(r)

    print(f"\n  {'Firm':<32} {'N':>3}  {'Avg P':>6}  {'GO':>3}  {'Acq':>3}  {'App':>3}  {'Fail':>4}")
    print("  " + "-" * 62)
    for firm in sorted(by_firm):
        cos = by_firm[firm]
        avg = sum(c["p_success"] for c in cos) / len(cos)
        go  = sum(1 for c in cos if c["verdict"] == "GO")
        acq = sum(1 for c in cos if c.get("outcome_hint") == "acquired")
        app = sum(1 for c in cos if c.get("outcome_hint") == "approved")
        fail= sum(1 for c in cos if "failed" in c.get("outcome_hint", ""))
        print(f"  {firm:<32} {len(cos):>3}  {avg:>5.1%}  {go:>3}  {acq:>3}  {app:>3}  {fail:>4}")

    srt = sorted(results, key=lambda x: -x["p_success"])
    print(f"\n  Top 10 (P(success)):")
    for r in srt[:10]:
        flag = {"approved": "✓ APPROVED", "acquired": "→ ACQUIRED",
                "failed_p3": "✗ FAILED P3"}.get(r.get("outcome_hint",""), "")
        print(f"    {r['p_success']:>5.1%}  {r['verdict']:6}  {r['name']:<30} {flag}")

    print(f"\n  Bottom 10 (P(success)):")
    for r in srt[-10:]:
        flag = {"approved": "✓ APPROVED", "acquired": "→ ACQUIRED",
                "failed_p3": "✗ FAILED P3"}.get(r.get("outcome_hint",""), "")
        print(f"    {r['p_success']:>5.1%}  {r['verdict']:6}  {r['name']:<30} {flag}")

    print(f"\n  Report → {REPORT_OUT}")
    print("═" * 66)


if __name__ == "__main__":
    main()
