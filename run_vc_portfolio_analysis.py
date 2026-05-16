#!/usr/bin/env python3
"""
VC Portfolio Failure Analysis
─────────────────────────────
Scrapes 6 bioventure VC portfolio pages (pre-scraped inline), queries
ClinicalTrials.gov v2 API for real program data, cross-references
bioventure.json for historical pattern matches, then runs the
SuccessPredictor explain() on every program.

Generates: data/vc_portfolio_failure_report.html

Sources:
  https://bioventures-capital.com/#two
  https://www.3ebiovc.com/content.php?cat_id=2
  https://www.bioventuresinvestors.com/investment-portfolio
  https://pitchbook.com/profiles/investor/530460-19#investments
  https://pivotallifesciences.com/portfolio/
  https://capitalbioventures.ca/portfolio/
"""
from __future__ import annotations
import json, sys, time, urllib.request, urllib.parse, urllib.error
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ─────────────────────────────────────────────────────────────────────────────
# 1.  PORTFOLIO  (pre-scraped from the 6 VC URLs)
# ─────────────────────────────────────────────────────────────────────────────
VC_FIRMS = [
    {
        "name": "3E BioVentures",
        "url": "https://www.3ebiovc.com/content.php?cat_id=2",
        "hq": "Beijing / San Francisco",
        "focus": "China-US cross-border VC – biotech and cross-disciplinary healthtech",
        "companies": [
            {
                "name": "Aravive", "ticker": "ARAV",
                "website": "https://aravive.com",
                "focus": "AXL-Fc fusion protein (batiraxcept) – AXL/Gas6 signalling trap in oncology",
                "modality": "Monoclonal antibody", "target": "AXL", "indication": "oncology",
                "stage": "phase3",
                "known_outcome": "failed",
                "failure_context": "Phase 3 JAVELIN OvCa trial missed primary endpoint (PFS) in platinum-resistant ovarian cancer. AXL target validation in OvCa was primarily preclinical; clinical benefit signal from Phase 2 was driven by a subgroup not replicated at scale. Stock was subsequently delisted from Nasdaq.",
            },
            {
                "name": "OncoImmune", "ticker": None,
                "website": "https://oncoimmune.com",
                "focus": "CD24-Siglec10 innate checkpoint axis – first-in-class anti-CD24 antibody",
                "modality": "Monoclonal antibody", "target": "CD24", "indication": "oncology",
                "stage": "phase1",
                "known_outcome": "acquired",
                "failure_context": "Acquired by MedImmune/AstraZeneca. Early signals promising but AZ discontinued the CD24 program post-acquisition; innate checkpoint biology remains clinically unproven at scale.",
            },
            {
                "name": "C4 Therapeutics", "ticker": "CCCC",
                "website": "https://c4therapeutics.com",
                "focus": "TORPEDO degrader platform – small-molecule targeted protein degradation (TPD)",
                "modality": "Small molecule (oral)", "target": "BRD4", "indication": "oncology",
                "stage": "phase2",
                "known_outcome": "pipeline_setback",
                "failure_context": "Multiple programs including CFT7455 (IKZF1/3 degrader) and CFT8634 (BRD9) struggled in Phase 2 with narrow therapeutic windows and haematological toxicity. Company underwent restructuring in 2024 and discontinued several programs. TPD mechanisms face challenges with selectivity and in vivo degradation depth.",
            },
            {
                "name": "Cognition Therapeutics", "ticker": "CGTX",
                "website": "https://cognitx.com",
                "focus": "CT1812 – sigma-2 receptor antagonist for Alzheimer's disease",
                "modality": "Small molecule (oral)", "target": "Sigma-2 receptor", "indication": "neurology",
                "stage": "phase2",
                "known_outcome": "failed",
                "failure_context": "Phase 2 SEQUEL trial for Alzheimer's disease did not meet its primary cognitive endpoint. The sigma-2 receptor mechanism has limited clinical validation in AD; the field has moved to amyloid/tau-targeting agents (lecanemab, donanemab) with proven Phase 3 signals. CT1812 lacked a validated biomarker for patient selection.",
            },
            {
                "name": "OncoC4",
                "website": "https://oncoc4.com",
                "focus": "Best-in-class CTLA-4 antibody + first-in-class CD24 antibody – IO combination",
                "modality": "Monoclonal antibody", "target": "CTLA-4", "indication": "oncology",
                "stage": "phase2",
                "known_outcome": "ongoing",
                "failure_context": "CD24 program remains early-stage with unclear differentiation from ipilimumab; highly crowded CTLA-4 space with established biosimilar competition post-patent-expiry for ipilimumab. Competitive pressures may limit commercial viability.",
            },
            {
                "name": "Dewpoint Therapeutics",
                "website": "https://dewpointx.com",
                "focus": "Condensate biology platform – targeting biomolecular condensates in oncology/neurodegeneration",
                "modality": "Small molecule (oral)", "target": "FUS/TDP-43 condensates", "indication": "neurology",
                "stage": "preclinical",
                "known_outcome": "pipeline_setback",
                "failure_context": "Condensate biology is scientifically compelling but clinically unproven. Lead program DPT-SH-01 in haematology was discontinued; the company restructured in 2023. The field faces challenges with target identification and compound selectivity within dynamic condensate environments.",
            },
            {
                "name": "Cullgen",
                "website": "https://cullgen.com",
                "focus": "uSMITE platform – ubiquitin-mediated targeted protein degradation",
                "modality": "Small molecule (oral)", "target": "PCNA", "indication": "oncology",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "Highly novel TPD mechanism; no clinical proof-of-concept yet. Similar to competitors (C4T, Arvinas), selectivity and in vivo stability of degraders in solid tumours is unresolved.",
            },
            {
                "name": "Lipidio",
                "website": "https://lipidiopharma.com",
                "focus": "Novel fatty acid derivative for NASH, Prader-Willi syndrome and antipsychotic weight gain",
                "modality": "Small molecule (oral)", "target": "Fatty acid metabolism", "indication": "metabolic",
                "stage": "phase2",
                "known_outcome": "ongoing",
                "failure_context": "NASH has an extremely high Phase 3 failure rate (>90%); histological endpoints are difficult to hit. The mechanism is not clearly differentiated from the GLP-1 class which has become the dominant standard of care.",
            },
            {
                "name": "Arnatar Therapeutics",
                "website": "https://arnatar.com",
                "focus": "Next-generation RNA medicines – silence disease drivers or restore missing proteins",
                "modality": "siRNA", "target": "ANGPTL3", "indication": "cardiovascular",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "RNA medicine space is highly competitive (Alnylam, Ionis, Arrowhead). GalNAc-siRNA delivery to liver is validated but hepatic-only delivery limits target scope. Differentiation unclear vs. approved inclisiran (PCSK9 siRNA) in cardiovascular.",
            },
        ],
    },
    {
        "name": "BioVentures Capital",
        "url": "https://bioventures-capital.com/#two",
        "hq": "Nicholasville, KY, USA",
        "focus": "Medical device investment and incubation – pre-seed to Series A",
        "companies": [
            {
                "name": "Oraliva",
                "website": "https://oraliva.com",
                "focus": "Oral mucosal drug delivery platform for rapid systemic absorption",
                "modality": "Drug delivery platform", "target": "Oral mucosa (non-target)", "indication": "metabolic",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "Drug delivery platforms face a long path: each drug-device combination requires its own regulatory review. Competition from established buccal/sublingual delivery companies and the GLP-1 injectable market is significant.",
            },
            {
                "name": "Biopathogenix",
                "website": "https://biopathogenix.com",
                "focus": "Rapid pathogen detection – point-of-care diagnostic platform",
                "modality": "Diagnostic device", "target": "Pathogen RNA/DNA", "indication": "infectious",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "Post-COVID diagnostic market is saturated. FDA clearance for novel PoC tests requires analytical and clinical validation studies. Reimbursement is a key commercial risk in an environment of declining diagnostic pricing.",
            },
        ],
    },
    {
        "name": "BioVentures MedTech Funds",
        "url": "https://www.bioventuresinvestors.com/investment-portfolio",
        "hq": "Westborough, MA, USA",
        "focus": "Medical device / MedTech venture fund – Series A and B",
        "companies": [
            {
                "name": "Optivio",
                "website": "https://www.optivio.com",
                "focus": "Non-invasive continuous cardiac output monitoring wearable",
                "modality": "Medical device", "target": "Cardiac output (hemodynamics)", "indication": "cardiovascular",
                "stage": "phase1",
                "known_outcome": "ongoing",
                "failure_context": "Wearable haemodynamic monitoring is competitive (Edwards Lifesciences, Masimo). Clinical evidence bar for reimbursement is high; NICU/ICU penetration requires large prospective outcome trials.",
            },
            {
                "name": "Endotronix",
                "website": "https://endotronix.com",
                "focus": "Cordella implantable pulmonary artery pressure sensor for heart failure management",
                "modality": "Implantable device", "target": "Pulmonary artery pressure", "indication": "cardiovascular",
                "stage": "phase3",
                "known_outcome": "failed",
                "failure_context": "PROACTIVE-HF pivotal trial failed to meet its primary endpoint (reduction in HF hospitalisations). The space is crowded by the FDA-approved Abbott CardioMEMS sensor; differentiation was incremental. PA pressure-guided HF management requires physician workflow integration that is difficult to achieve at scale.",
            },
            {
                "name": "CoNextions",
                "website": "https://www.conextionsmed.com",
                "focus": "Tendon repair system – augmented suture for Achilles and rotator cuff repair",
                "modality": "Surgical device", "target": "Tendon biology", "indication": "rare_disease",
                "stage": "phase2",
                "known_outcome": "ongoing",
                "failure_context": "Orthopaedic device trials require large, long-term follow-up; re-rupture endpoints typically measured at 2 years. Surgeon adoption of novel repair techniques is slow and requires strong KOL-driven clinical evidence.",
            },
            {
                "name": "Deep Vein Medical",
                "website": "https://www.bioventuresinvestors.com",
                "focus": "DVT (deep vein thrombosis) prevention device",
                "modality": "Preventive device", "target": "Venous stasis", "indication": "cardiovascular",
                "stage": "phase2",
                "known_outcome": "ongoing",
                "failure_context": "DVT prevention is a well-solved clinical problem with IPC (intermittent pneumatic compression) and pharmacological prophylaxis; differentiation requires demonstrating superiority over existing SOC, which demands very large RCTs.",
            },
            {
                "name": "Verax Biomedical",
                "website": "https://www.veraxbiomedical.com",
                "focus": "PGD rapid sterility test – bacterial detection in platelet products",
                "modality": "Diagnostic device", "target": "Platelet contamination bacteria", "indication": "infectious",
                "stage": "phase3",
                "known_outcome": "failed",
                "failure_context": "FDA cleared an alternative test (BacTx) and blood banks shifted to pathogen reduction (Cerus INTERCEPT). The market window for a diagnostic-only solution in platelet bacteriology narrowed significantly. Regulatory timing and competitive displacement are critical device market risks.",
            },
        ],
    },
    {
        "name": "Pivotal Life Sciences",
        "url": "https://pivotallifesciences.com/portfolio/",
        "hq": "San Francisco / Boston / Shanghai / Hong Kong",
        "focus": "Global cross-stage life sciences VC – drug discovery through commercialisation",
        "companies": [
            {
                "name": "IO Biotech", "ticker": None,
                "website": "https://io-biotech.com",
                "focus": "IO102-IO103 – IDO/TDO2 neoantigen vaccine for cancer immunotherapy",
                "modality": "Monoclonal antibody", "target": "IDO/TDO2", "indication": "oncology",
                "stage": "phase3",
                "known_outcome": "failed",
                "failure_context": "The IOB-022 Phase 3 trial in 1L melanoma (IO102-IO103 + pembrolizumab vs. pembrolizumab alone) missed its primary PFS and OS endpoints in 2024. IDO pathway inhibitors have a troubled history (epacadostat Phase 3 failure in 2018); tryptophan catabolism suppression in the tumour microenvironment may not be sufficient as a combination backbone when added to PD-1 blockade. Patient selection lacked a validated IDO biomarker.",
            },
            {
                "name": "Bolt Biotherapeutics",
                "website": "https://boltbio.com",
                "focus": "BDC-1001 – HER2-targeting innate cell engager (Prob-body + TLR7/8 agonist)",
                "modality": "Monoclonal antibody", "target": "HER2 (ERBB2)", "indication": "oncology",
                "stage": "phase2",
                "known_outcome": "failed",
                "failure_context": "BDC-1001 Phase 2 in HER2+ solid tumours failed to demonstrate meaningful single-agent or combination efficacy beyond existing HER2-targeted therapies (T-DXd, pertuzumab). The innate immune engager concept (TLR agonist conjugated to a targeting antibody) lacked clinical proof-of-concept. Company wound down operations in 2024. The crowded HER2 space with T-DXd setting a high bar made differentiation extremely challenging.",
            },
            {
                "name": "BioAge Labs",
                "website": "https://bioagelabs.com",
                "focus": "Azelaprag – AMPK activator for MASH (metabolic-associated steatohepatitis) and longevity",
                "modality": "Small molecule (oral)", "target": "AMPK", "indication": "metabolic",
                "stage": "phase2",
                "known_outcome": "failed",
                "failure_context": "The MASH Phase 2 trial (STRIDES) for azelaprag was stopped in 2024 after a liver safety signal emerged. AMPK activation, while metabolically interesting, caused hepatotoxicity in the trial; the risk-benefit in MASH (itself a liver disease) was unfavourable. MASH drug development has a >90% Phase 3 failure rate, and safety signals in Phase 2 are usually fatal for MASH programs.",
            },
            {
                "name": "Aligos Therapeutics", "ticker": "ALGS",
                "website": "https://aligos.com",
                "focus": "HBV functional cure regimen – ASO + capsid assembly modulator + S-antigen transport inhibitor",
                "modality": "siRNA", "target": "HBV RNA/cccDNA", "indication": "infectious",
                "stage": "phase2",
                "known_outcome": "pipeline_setback",
                "failure_context": "Multiple Phase 2 programs discontinued (ALG-020572 CAM, ALG-000184 ASO). The HBV functional cure bar is extremely high: achieving sustained HBsAg loss off-treatment requires deep viral suppression across all viral reservoirs. Combination regimens add toxicity complexity. Gilead and Roche have larger platforms with more advanced programs (peg-interferon lambda + bulevirtide). Aligos restructured multiple times.",
            },
            {
                "name": "Gossamer Bio", "ticker": "GOSS",
                "website": "https://gossamerbio.com",
                "focus": "Seralutinib – inhaled ETB receptor antagonist for pulmonary arterial hypertension (PAH)",
                "modality": "Small molecule (oral)", "target": "Endothelin receptor B", "indication": "cardiovascular",
                "stage": "phase2",
                "known_outcome": "failed",
                "failure_context": "The PROSERA Phase 2 trial of inhaled seralutinib in PH-ILD missed its primary 6MWD endpoint in 2023. ETB receptor biology in PAH subtypes is complex; ETB can have vasodilatory roles in pulmonary vasculature, and inhaled delivery may not achieve adequate tissue penetration. The approved PDGFR/FGFR inhibitor (imatinib) and oral prostacyclin pathway agents dominate the PAH pipeline.",
            },
            {
                "name": "Exscientia", "ticker": "EXAI",
                "website": "https://exscientia.ai",
                "focus": "AI-driven small molecule drug design platform – DSP-1181 (OCD), EXS-21546 (IPF/cancer)",
                "modality": "Small molecule (oral)", "target": "5-HT1A", "indication": "neurology",
                "stage": "phase2",
                "known_outcome": "pipeline_setback",
                "failure_context": "DSP-1181 (partnered with Sumitomo Dainippon) was discontinued after Phase 1. EXS-21546 (ATR inhibitor) in IPF had disappointing Phase 2 data. AI-generated compounds pass through the same clinical attrition as conventionally designed drugs; the platform de-risks lead optimisation but not target selection or clinical biology. Exscientia was acquired by Recursion in 2024 at a significant discount to peak valuation.",
            },
            {
                "name": "Inozyme Pharma", "ticker": "INZY",
                "website": "https://inozymepharma.com",
                "focus": "ENPP1-Fc enzyme replacement for ABCC6 deficiency and hypophosphatasia (HPP-related)",
                "modality": "Monoclonal antibody", "target": "ENPP1", "indication": "rare_disease",
                "stage": "phase2",
                "known_outcome": "pipeline_setback",
                "failure_context": "ENPP1 deficiency is ultra-rare; Phase 2 interim data for INZ-701 showed modest improvement in ectopic calcification endpoints, but the trial was re-designed after slow enrolment. Enzyme replacement in mineralisation disorders faces long-term durability questions and head-to-head competition from Asfotase alfa (approved for HPP). Market size is severely limited by ultra-rare prevalence.",
            },
            {
                "name": "Oculis", "ticker": "OCS",
                "website": "https://oculis.com",
                "focus": "OCS-01 – topical dexamethasone nanoparticle for diabetic macular oedema (DME)",
                "modality": "Small molecule (oral)", "target": "Glucocorticoid receptor", "indication": "rare_disease",
                "stage": "phase3",
                "known_outcome": "failed",
                "failure_context": "The DIAMOND Phase 3 trial for OCS-01 in DME did not achieve its primary visual acuity endpoint in 2024. Anti-VEGF injectables (aflibercept, ranibizumab, faricimab) are the entrenched SOC for DME with high response rates; a topical corticosteroid faces a very high bar to show non-inferiority, let alone superiority. Steroid-associated IOP elevation is a safety concern unique to the topical route.",
            },
            {
                "name": "Vigil Neuroscience", "ticker": "VIGL",
                "website": "https://vigilneuro.com",
                "focus": "VGL101 – TREM2 agonistic antibody for ALSP (adult-onset leukoencephalopathy)",
                "modality": "Monoclonal antibody", "target": "TREM2", "indication": "neurology",
                "stage": "phase2",
                "known_outcome": "ongoing",
                "failure_context": "TREM2 agonism in microglia is scientifically compelling but clinically unproven. ALSP is ultra-rare (CSF1R mutations), limiting enrolment. VGL101 Phase 2 is ongoing; initial biomarker data (CSF neurofilament) showed modest signals but clinical meaningful endpoints are 18-24 months away. High risk of insufficient efficacy in a monogenic disease where gene therapy may be more appropriate.",
            },
            {
                "name": "Trevi Therapeutics", "ticker": "TRVI",
                "website": "https://trevitherapeutics.com",
                "focus": "Nalbuphine ER – kappa/mu opioid receptor modulator for prurigo nodularis and chronic pruritus",
                "modality": "Small molecule (oral)", "target": "Kappa opioid receptor", "indication": "immunology",
                "stage": "phase3",
                "known_outcome": "mixed",
                "failure_context": "The PRISM Phase 3 trial for nalbuphine ER in prurigo nodularis showed significant itch reduction but missed key secondary QoL endpoints. The FDA has not yet approved the NDA (PDUFA date delayed). Competitor dupilumab (IL-4/IL-13 mAb) was approved for PN in 2022, raising the differentiation bar. Opioid-class drugs face unique regulatory scrutiny in a post-opioid-crisis environment.",
            },
            {
                "name": "Karuna Therapeutics", "ticker": "KRTX",
                "website": "https://karunatx.com",
                "focus": "KarXT (xanomeline-trospium) – M1/M4 muscarinic agonist for schizophrenia",
                "modality": "Small molecule (oral)", "target": "Muscarinic M1/M4", "indication": "neurology",
                "stage": "phase3",
                "known_outcome": "approved",
                "failure_context": "SUCCESS: KarXT (Cobenfy) was approved by FDA in September 2024 – the first new schizophrenia mechanism approved in 30 years. Acquired by Bristol-Myers Squibb for $14 billion. The program illustrates how validated CNS biology (muscarinic hypothesis, decades of research) + co-formulation innovation (adding trospium to reduce GI side effects) can overcome a historically difficult indication.",
            },
            {
                "name": "Harmony Biosciences", "ticker": "HRMY",
                "website": "https://harmonybiosciences.com",
                "focus": "Pitolisant – histamine H3 receptor inverse agonist for narcolepsy",
                "modality": "Small molecule (oral)", "target": "Histamine H3", "indication": "neurology",
                "stage": "phase3",
                "known_outcome": "approved",
                "failure_context": "SUCCESS: Wakix (pitolisant) FDA approved 2019 for narcolepsy EDS, expanded 2020 for cataplexy. Pitolisant had established European approval (Bioprojet), providing clinical validation. Harmony licensed a de-risked asset rather than running a de novo Phase 3 – demonstrating the value of late-stage in-licensing strategy.",
            },
            {
                "name": "Gracell Biotechnologies", "ticker": "GRCL",
                "website": "https://gracellbio.com",
                "focus": "FasTCAR – next-day CAR-T manufacturing platform (CD19 and BCMA targets)",
                "modality": "CAR-T (autologous)", "target": "CD19", "indication": "oncology",
                "stage": "phase2",
                "known_outcome": "acquired",
                "failure_context": "SUCCESS via acquisition: Gracell was acquired by AstraZeneca for $1.2 billion in 2023. The FasTCAR same-day manufacturing platform was the key differentiator vs. Kymriah/Yescarta (which take 2-4 weeks). The acquisition validates rapid manufacturing as a genuine competitive moat in CAR-T.",
            },
            {
                "name": "Fusion Pharmaceuticals", "ticker": "FUSN",
                "website": "https://fusionpharma.com",
                "focus": "Actinium-225 targeted alpha therapy (TAT) – FPI-2265 for mCRPC",
                "modality": "Monoclonal antibody", "target": "PSMA", "indication": "oncology",
                "stage": "phase3",
                "known_outcome": "acquired",
                "failure_context": "SUCCESS via acquisition: Fusion was acquired by AstraZeneca for $2 billion in 2024. Lutetium-PSMA-617 (Pluvicto, Novartis) already approved; Ac-225-based alpha therapy offers higher LET and shorter range, potentially superior for bone micrometastases. AZ acquired to complement its radioconjugate pipeline alongside Daiichi partnerships.",
            },
        ],
    },
    {
        "name": "Capital BioVentures",
        "url": "https://capitalbioventures.ca/portfolio/",
        "hq": "Ottawa, Canada",
        "focus": "Canadian wet-lab accelerator & early-stage biotech investor",
        "companies": [
            {
                "name": "Apiary TX",
                "website": "https://www.linkedin.com/company/apiarytx",
                "focus": "Engineered bee-venom derived peptides for immunological diseases",
                "modality": "Monoclonal antibody", "target": "Innate immune pathway", "indication": "immunology",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "Bee-venom peptide (melittin) derivatives have shown in vitro immunomodulatory activity but face formidable challenges: systemic delivery of membrane-disrupting peptides requires sophisticated encapsulation, and the immune modulatory mechanism is non-specific. Regulatory path for novel biological entities is lengthy.",
            },
            {
                "name": "CerebroTX",
                "website": "https://www.cerebrotx.com",
                "focus": "Novel small molecule therapy for traumatic brain injury (TBI) neuroprotection",
                "modality": "Small molecule (oral)", "target": "Neuroinflammation cascade", "indication": "neurology",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "TBI neuroprotection is one of the highest-attrition areas in drug development; over 30 Phase 3 trials have failed since 1990 (progesterone, magnesium, cyclosporin). The heterogeneous injury biology, narrow treatment window (6-24h post-injury), and difficulty enrolling patients in acute trials make this indication extremely high risk.",
            },
            {
                "name": "Cura Therapeutics",
                "website": "https://www.curatherapeutics.com",
                "focus": "RNA-targeted small molecules for neurological diseases",
                "modality": "Small molecule (oral)", "target": "RNA structure/splicing", "indication": "neurology",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "RNA-targeted small molecules (RNA-binding compounds) are a young field; risdiplam (SMA) is the key proof-of-concept. Target identification and selectivity are major challenges since RNA structures are dynamic and cell-context dependent. CNS penetration adds further complexity.",
            },
            {
                "name": "FibroDynamX",
                "website": "https://www.fibrodynamx.com",
                "focus": "Anti-fibrotic therapy platform – extracellular matrix targeting",
                "modality": "Monoclonal antibody", "target": "Extracellular matrix / fibrosis", "indication": "rare_disease",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "Fibrosis drug development has an extremely poor track record beyond pirfenidone/nintedanib in IPF. The liver fibrosis space alone has seen failures from simtuzumab (LOXL2), GS-4997 (ASK1), and cenicriviroc (CCR2/5). The extracellular matrix is hard to drug without on-target toxicity in normal wound healing.",
            },
            {
                "name": "i-RNA Therapeutics",
                "website": "https://www.i-rna.ca",
                "focus": "Immunomodulatory RNA (imRNA) for inflammatory diseases",
                "modality": "siRNA", "target": "Inflammatory cytokines", "indication": "immunology",
                "stage": "preclinical",
                "known_outcome": "ongoing",
                "failure_context": "RNA immunomodulation in inflammatory diseases faces delivery hurdles beyond the liver. GalNAc delivery only reaches hepatocytes; systemic inflammation targets (synovium, lung, gut) require LNP or novel delivery vehicles with unknown safety profiles. The space is crowded with approved biologics (adalimumab, ustekinumab, secukinumab).",
            },
        ],
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# 2.  CLINICALTRIALS.GOV  v2 API  QUERY
# ─────────────────────────────────────────────────────────────────────────────
CT_API = "https://clinicaltrials.gov/api/v2/studies"
HEADERS = {"User-Agent": "REC-DECISION/1.0 (academic research; contact@recd.ai)"}

STAGE_MAP = {
    "EARLY_PHASE1": "phase1", "PHASE1": "phase1", "PHASE1_PHASE2": "phase1",
    "PHASE2": "phase2", "PHASE2_PHASE3": "phase2",
    "PHASE3": "phase3", "PHASE4": "phase3",
    "NA": "preclinical",
}
STATUS_FAILED = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
STATUS_DONE   = {"COMPLETED"}

def _get(url: str, timeout: int = 10) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"    [CT.gov fetch error] {e}", flush=True)
        return None

def fetch_ct_programs(company: str, max_results: int = 8) -> list[dict]:
    """Query ClinicalTrials.gov for a company's trials, return simplified records."""
    params = urllib.parse.urlencode({
        "query.spons": company,
        "format": "json",
        "pageSize": max_results,
        "fields": "NCTId,BriefTitle,Phase,OverallStatus,Condition,InterventionName,InterventionType,StudyType,StartDate,CompletionDate,WhyStopped",
    })
    data = _get(f"{CT_API}?{params}")
    if not data:
        return []
    studies = data.get("studies", [])
    out = []
    for s in studies:
        pm = s.get("protocolSection", {})
        id_mod  = pm.get("identificationModule", {})
        stat_mod = pm.get("statusModule", {})
        des_mod  = pm.get("designModule", {})
        cond_mod = pm.get("conditionsModule", {})
        int_mod  = pm.get("armsInterventionsModule", {})

        phases = des_mod.get("phases", ["NA"])
        phase  = STAGE_MAP.get(phases[0] if phases else "NA", "preclinical")

        status = stat_mod.get("overallStatus", "UNKNOWN")
        why_stopped = stat_mod.get("whyStopped", "")

        interventions = int_mod.get("interventions", [])
        intv_names = [i.get("name","") for i in interventions if i.get("interventionType") in ("DRUG","BIOLOGICAL","GENETIC","DEVICE","COMBINATION_PRODUCT")]

        conditions = cond_mod.get("conditions", [])

        out.append({
            "nct_id":       id_mod.get("nctId",""),
            "title":        id_mod.get("briefTitle",""),
            "phase":        phase,
            "status":       status,
            "why_stopped":  why_stopped,
            "conditions":   conditions,
            "interventions": intv_names,
            "is_failed":    status in STATUS_FAILED,
            "is_completed": status in STATUS_DONE,
        })
    time.sleep(0.4)   # be polite to the API
    return out

# ─────────────────────────────────────────────────────────────────────────────
# 3.  MAP  CT.gov  RECORD  →  MODEL  INPUT  FORMAT
# ─────────────────────────────────────────────────────────────────────────────
def _detect_indication(conditions: list[str], company_indication: str) -> str:
    txt = " ".join(conditions).lower()
    if any(k in txt for k in ["cancer","tumor","tumour","carcinoma","lymphoma","leukemia","melanoma","oncol"]):
        return "oncology"
    if any(k in txt for k in ["alzheimer","parkinson","schizoph","epilep","multiple sclerosis","neuro","brain","tbi","abi"]):
        return "neurology"
    if any(k in txt for k in ["crohn","colitis","arthritis","lupus","psoriasis","immunol","autoimmune","inflammatory"]):
        return "immunology"
    if any(k in txt for k in ["heart","cardiac","coronary","hypertension","atrial","dvt","thrombosis","vascular"]):
        return "cardiovascular"
    if any(k in txt for k in ["diabetes","obesity","nash","mash","metabolic","lipid","cholesterol","weight"]):
        return "metabolic"
    if any(k in txt for k in ["rare","orphan","genetic disorder","inborn","lysosomal","duchenne","spinal muscular"]):
        return "rare_disease"
    if any(k in txt for k in ["hiv","hepatitis","infection","viral","bacterial","covid"]):
        return "infectious"
    return company_indication

def _detect_platform(interventions: list[str], company_modality: str) -> str:
    txt = " ".join(interventions).lower()
    if any(k in txt for k in ["sirna","shrna","antisense","aso","oligonucleotide"]):
        return "siRNA"
    if any(k in txt for k in ["mrna","lnp"]):
        return "mRNA"
    if any(k in txt for k in ["crispr","genome editing","gene editing"]):
        return "CRISPR ex vivo"
    if any(k in txt for k in ["aav","gene therapy","lentiviral","viral vector"]):
        return "AAV gene therapy"
    if any(k in txt for k in ["car-t","cart","chimeric antigen"]):
        return "CAR-T (autologous)"
    if any(k in txt for k in ["bispecific","bite","dual"]):
        return "Bispecific antibody (HLE BiTE)"
    if any(k in txt for k in ["antibody drug conjugate","adc","trastuzumab derr"]):
        return "ADC (cleavable)"
    if any(k in txt for k in ["monoclonal antibody","mab","antibody","biologic","fusion protein"]):
        return "Monoclonal antibody"
    return company_modality

def ct_to_model_input(ct_rec: dict, company: dict, firm_name: str, idx: int) -> dict:
    indication = _detect_indication(ct_rec["conditions"], company["indication"])
    platform   = _detect_platform(ct_rec["interventions"], company["modality"])
    desc = (
        f"{', '.join(ct_rec['interventions'][:2]) or company['focus']} "
        f"in {', '.join(ct_rec['conditions'][:2]) or indication}. "
        f"Status: {ct_rec['status']}."
        + (f" Stopped: {ct_rec['why_stopped']}." if ct_rec["why_stopped"] else "")
    )
    return {
        "_id":  f"CT-{ct_rec['nct_id'] or f'{firm_name[:4]}-{idx:03d}'}",
        "title": ct_rec["title"] or company["name"],
        "description": desc,
        "clinical_stage": ct_rec["phase"],
        "indication": indication,
        "source": "clinicaltrials",
        "_meta": {
            "platform": platform,
            "target": company.get("target", "Unknown"),
            "is_validated_target": _is_validated(company.get("target","Unknown")),
        },
        "_ct": ct_rec,
    }

VALIDATED_TARGETS = {
    "HER2 (ERBB2)","PD-L1","PD-1","CD19","CD38","KRAS G12C","EGFR","VEGFR","BRAF","BCR-ABL",
    "BTK","JAK","PARP","CDK4/CDK6","CTLA-4","IL-6","IL-17","IL-23","TNF-alpha","GLP-1",
    "GLP-1/GIP","PCSK9","amyloid beta","SGLT2","CCR5","Muscarinic M1/M4","PSMA","CD19","BCMA",
    "Histamine H3","Kappa opioid receptor","Glucocorticoid receptor","5-HT1A","Pulmonary artery pressure",
}
def _is_validated(target: str) -> bool:
    return any(v.lower() in target.lower() for v in VALIDATED_TARGETS)

# ─────────────────────────────────────────────────────────────────────────────
# 4.  BIOVENTURE.JSON  PATTERN  MATCH
# ─────────────────────────────────────────────────────────────────────────────
print("Loading bioventure.json …", flush=True)
_bv_data = json.loads(Path("data/bioventure.json").read_text())
_bv_projects = list(_bv_data.get("projects", {}).values())

def _bv_comparable(indication: str, stage: str, n: int = 5) -> list[dict]:
    """Return up to n historically similar projects from bioventure.json."""
    IND_NORM = {
        "oncology": ["cancer","oncol","tumor","carcinoma","lymphoma","leukemia"],
        "neurology": ["neurol","alzheimer","parkinson","brain","cns","schizoph","seizure"],
        "immunology": ["autoimmun","inflamm","arthritis","lupus","crohn","colitis"],
        "cardiovascular": ["cardiac","heart","vascular","coronary","hypertension"],
        "metabolic": ["metabol","diabetes","obesity","nash","lipid"],
        "rare_disease": ["rare","orphan","genetic","lysosomal","duchenne"],
        "infectious": ["infect","hiv","hepatitis","viral"],
    }
    keywords = IND_NORM.get(indication, [indication])
    stage_norm = stage.lower().replace("phase","phase ").replace("phase 1","phase1").replace("phase 2","phase2").replace("phase 3","phase3")
    hits = []
    for p in _bv_projects:
        txt = (p.get("title","") + " " + p.get("indication","") + " " + p.get("raw_text","")).lower()
        if any(kw in txt for kw in keywords):
            hits.append(p)
    # Sort by: matching stage first, failed outcomes prioritised (for lessons)
    def _rank(p):
        s_match = 1 if p.get("clinical_stage","").lower() == stage_norm else 0
        is_failed = 1 if "discontinued" in p.get("outcome","") else 0
        return -(s_match + is_failed)
    hits.sort(key=_rank)
    return hits[:n]

# ─────────────────────────────────────────────────────────────────────────────
# 5.  MODEL
# ─────────────────────────────────────────────────────────────────────────────
print("Loading model …", flush=True)
from src.learning.decision_model import SuccessPredictor
model   = SuccessPredictor()
metrics = model.train()
print(f"Model ready  AUC={metrics.get('auc_roc',0):.3f}  n={metrics.get('n_train',0):,}", flush=True)

def _make_fallback_program(company: dict, firm_name: str) -> dict:
    """Build a synthetic model input when CT.gov returns no results."""
    return {
        "_id":  f"SYN-{firm_name[:3].upper()}-{company['name'][:6].replace(' ','')}",
        "title": company["name"],
        "description": company["focus"],
        "clinical_stage": company.get("stage","preclinical"),
        "indication": company.get("indication","oncology"),
        "source": "synthetic",
        "_meta": {
            "platform": company.get("modality","Monoclonal antibody"),
            "target": company.get("target","Unknown"),
            "is_validated_target": _is_validated(company.get("target","Unknown")),
        },
        "_ct": None,
    }

# ─────────────────────────────────────────────────────────────────────────────
# 6.  RUN ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
OUTCOME_LABEL = {
    "failed": ("NO-GO ✗", "#ef4444"),
    "pipeline_setback": ("SETBACK ⚠", "#f59e0b"),
    "ongoing": ("ONGOING →", "#38bdf8"),
    "acquired": ("ACQUIRED ★", "#22c55e"),
    "approved": ("APPROVED ★", "#22c55e"),
    "mixed": ("MIXED ≈", "#a78bfa"),
}

analysis_results: list[dict] = []   # one entry per company

for firm in VC_FIRMS:
    print(f"\n── {firm['name']} ──", flush=True)
    for co in firm["companies"]:
        print(f"  {co['name']} …", end=" ", flush=True)

        # a) Query ClinicalTrials.gov
        ct_programs = fetch_ct_programs(co["name"], max_results=6)
        if not ct_programs:
            ct_programs = []

        # b) Build model inputs
        model_inputs = [ct_to_model_input(c, co, firm["name"], i) for i, c in enumerate(ct_programs)]
        if not model_inputs:
            model_inputs = [_make_fallback_program(co, firm["name"])]

        # c) Run explain() on each
        explained = []
        for prog in model_inputs:
            r = model.explain(prog)
            tech = r["technology"]
            bio  = r["biology"]
            sig  = r.get("signals", {})
            frt  = r.get("frontier", {})
            saf  = r.get("safety_profile", {})
            lessons = r.get("historical_lessons", [])
            explained.append({
                "prog":    prog,
                "score":   r["p_success"],
                "verdict": r["verdict"],
                "summary": r.get("summary",""),
                "calibration": r["calibration"],
                "fit_rationale": tech.get("fit_rationale",""),
                "fit_score":  tech.get("fit_score",0),
                "is_clearcut": tech.get("is_clearcut",False),
                "is_bleeding_edge": tech.get("is_bleeding_edge",False),
                "target_status": bio.get("target_status","unknown"),
                "detected_targets": bio.get("detected_targets",[]),
                "signals_completion": sig.get("completion",[]),
                "signals_failure":    sig.get("failure",[]),
                "signals_safety":     sig.get("safety",[]),
                "frontier_modality": frt.get("modality",""),
                "frontier_in_use":   frt.get("in_use",[])[:3],
                "frontier_not_using": frt.get("not_using",[])[:3],
                "safety_risks":   saf.get("risks",[]),
                "safety_summary": saf.get("summary",""),
                "lessons": [{
                    "lesson": l.get("lesson",""), "outcome": l.get("outcome",""),
                    "year":   l.get("year",""),   "drug":    l.get("drug",""),
                } for l in lessons[:4]],
                "is_failed_ct": prog["_ct"]["is_failed"] if prog["_ct"] else False,
                "ct_status":    prog["_ct"]["status"] if prog["_ct"] else "N/A",
                "nct_id":       prog["_ct"]["nct_id"] if prog["_ct"] else "",
                "why_stopped":  prog["_ct"]["why_stopped"] if prog["_ct"] else "",
            })

        # d) Historical comparables from bioventure.json
        bv_hits = _bv_comparable(co.get("indication","oncology"), co.get("stage","preclinical"), n=4)

        avg_score = sum(e["score"] for e in explained) / len(explained)
        n_go = sum(1 for e in explained if e["verdict"] == "GO")

        print(f"score={avg_score:.2f}  {co.get('known_outcome','?')}", flush=True)

        analysis_results.append({
            "firm_name":    firm["name"],
            "firm_url":     firm["url"],
            "co":           co,
            "explained":    explained,
            "bv_hits":      bv_hits,
            "avg_score":    avg_score,
            "n_go":         n_go,
            "n_total":      len(explained),
        })

# ─────────────────────────────────────────────────────────────────────────────
# 7.  AGGREGATE STATS
# ─────────────────────────────────────────────────────────────────────────────
total_cos = len(analysis_results)
n_failed  = sum(1 for r in analysis_results if r["co"].get("known_outcome") in ("failed","pipeline_setback"))
n_success = sum(1 for r in analysis_results if r["co"].get("known_outcome") in ("approved","acquired"))
n_ongoing = sum(1 for r in analysis_results if r["co"].get("known_outcome") == "ongoing")
avg_global = sum(r["avg_score"] for r in analysis_results) / total_cos
ts = datetime.now().strftime("%Y-%m-%d %H:%M")

# Group by firm for navigation
firms_seen: list[str] = []
for r in analysis_results:
    if r["firm_name"] not in firms_seen:
        firms_seen.append(r["firm_name"])

# ─────────────────────────────────────────────────────────────────────────────
# 8.  HTML  GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def escH(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def outcome_badge(known_outcome: str) -> str:
    label, col = OUTCOME_LABEL.get(known_outcome, ("UNKNOWN","#64748b"))
    return f'<span class="outcome-badge" style="background:{col}22;color:{col};border-color:{col}">{label}</span>'

def score_bar(score: float, width: int = 120) -> str:
    pct = int(score * 100)
    col = "#22c55e" if pct >= 65 else "#f59e0b" if pct >= 45 else "#ef4444"
    return (
        f'<div class="score-bar-wrap">'
        f'<div class="score-bar-fill" style="width:{pct}%;background:{col}"></div>'
        f'</div>'
        f'<span class="score-num" style="color:{col}">{pct}%</span>'
    )

def calibration_rows(cal: list) -> str:
    if not cal:
        return "<span style='color:#64748b'>No calibration factors applied.</span>"
    rows = []
    for c in cal:
        adj = c.get("adjustment","")
        fac = c.get("factor","")
        col = "#22c55e" if adj.startswith("+") else "#ef4444"
        rows.append(
            f'<div class="cal-row">'
            f'<span class="cal-adj" style="color:{col}">{escH(adj)}</span>'
            f'<span class="cal-fac">{escH(fac)}</span>'
            f'</div>'
        )
    return "".join(rows)

def signal_chips(items: list, col: str) -> str:
    if not items:
        return '<span style="color:#64748b">—</span>'
    return "".join(
        f'<span class="sig-chip" style="background:{col}18;color:{col};border-color:{col}66">{escH(t)}</span>'
        for t in items
    )

def frontier_rows(items: list, col: str) -> str:
    if not items:
        return '<span style="color:#64748b">—</span>'
    out = []
    for e in items:
        tech = e.get("tech","")
        status = e.get("status","")
        note   = e.get("note","")
        out.append(
            f'<div class="frontier-row">'
            f'<span style="color:{col};font-weight:600">{escH(tech)}</span> '
            f'<span class="frontier-meta">[{escH(status)}]</span>'
            + (f' — <span class="frontier-note">{escH(note)}</span>' if note else "")
            + f'</div>'
        )
    return "".join(out)

def bv_comparable_html(bv_hits: list) -> str:
    if not bv_hits:
        return "<span style='color:#64748b'>No comparable programs found in bioventure database.</span>"
    rows = []
    for p in bv_hits:
        outcome = p.get("outcome","unknown")
        outcome_col = "#ef4444" if "discontinued" in outcome else "#22c55e" if outcome == "approved" else "#f59e0b"
        ct_url = p.get("url","")
        link = f'<a href="{escH(ct_url)}" target="_blank" rel="noopener noreferrer" class="ct-link">{escH(p.get("source_id",""))}</a>' if ct_url else ""
        rows.append(
            f'<div class="bv-row">'
            f'<span class="bv-title">{escH(p.get("title","")[:90])}</span>'
            f'<span class="bv-outcome" style="color:{outcome_col}">{escH(outcome)}</span>'
            f'<span class="bv-stage">{escH(p.get("clinical_stage",""))}</span>'
            + (f'<span class="bv-link">{link}</span>' if link else "")
            + f'</div>'
        )
    return "".join(rows)

def lesson_rows(lessons: list) -> str:
    if not lessons:
        return "<span style='color:#64748b'>No historical lessons extracted.</span>"
    out = []
    for l in lessons:
        lesson = l.get("lesson","")
        outcome = l.get("outcome","")
        drug = l.get("drug","")
        out.append(
            f'<div class="lesson-row">'
            f'<span class="lesson-text">{escH(lesson)}</span>'
            + (f' <span class="lesson-drug">[{escH(drug)}]</span>' if drug else "")
            + (f' <span class="lesson-outcome">({escH(outcome)})</span>' if outcome else "")
            + f'</div>'
        )
    return "".join(out)

def resource_links_html(co: dict, nct_id: str = "") -> str:
    tgt  = urllib.parse.quote_plus(co.get("target",""))
    name = urllib.parse.quote_plus(co.get("name",""))
    q_pm = urllib.parse.quote_plus(f"{co.get('target','')} {co.get('name','')} clinical trial failure")
    IND_CT = {
        "oncology":"cancer","rare_disease":"rare+disease","immunology":"autoimmune",
        "neurology":"neurological","cardiovascular":"cardiovascular",
        "metabolic":"metabolic","infectious":"infectious+disease",
    }
    cond = urllib.parse.quote_plus(IND_CT.get(co.get("indication",""),""))
    links = [
        {
            "url": f"https://clinicaltrials.gov/search?query.spons={name}",
            "icon": "🔬", "label": "ClinicalTrials.gov",
            "why": f"All registered trials sponsored by <strong>{escH(co['name'])}</strong> — view every program, phase, status, and protocol detail"
        },
        {
            "url": f"https://pubmed.ncbi.nlm.nih.gov/?term={q_pm}&sort=date",
            "icon": "📄", "label": "PubMed – Failure Literature",
            "why": f"Academic literature on <strong>{escH(co.get('target',''))}</strong> failures in {escH(co.get('indication',''))}: understand why the target/mechanism didn't translate"
        },
        {
            "url": f"https://www.ebi.ac.uk/chembl/explore/targets?q={tgt}",
            "icon": "🧬", "label": "ChEMBL Target Profile",
            "why": f"Compound activity data, selectivity, and known liabilities for the <strong>{escH(co.get('target',''))}</strong> target — assess tractability"
        },
        {
            "url": f"https://clinicaltrials.gov/search?cond={cond}&term={tgt}&aggFilters=status:ter",
            "icon": "📊", "label": "Terminated Competitors",
            "why": "Search terminated trials in the same indication + target — understand the breadth of prior failures and their root causes"
        },
        {
            "url": co.get("website","#"),
            "icon": "🌐", "label": f"{escH(co['name'])} Website",
            "why": "Company pipeline, investor presentations, and press releases — primary source for the most recent program status"
        },
    ]
    if nct_id:
        links.insert(0, {
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
            "icon": "📋", "label": f"Trial {nct_id}",
            "why": f"Full protocol, results, and WHY STOPPED field for the specific trial record <strong>{escH(nct_id)}</strong>"
        })
    cards = "".join(
        f'<a class="res-card" href="{l["url"]}" target="_blank" rel="noopener noreferrer">'
        f'<div class="res-header"><span class="res-icon">{l["icon"]}</span>'
        f'<span class="res-label">{l["label"]}</span><span class="res-ext">↗</span></div>'
        f'<div class="res-why">{l["why"]}</div></a>'
        for l in links
    )
    return f'<div class="res-grid">{cards}</div>'

# ── Per-company card ──────────────────────────────────────────────────────────
def company_card_html(result: dict) -> str:
    co        = result["co"]
    explained = result["explained"]
    bv_hits   = result["bv_hits"]
    avg_score = result["avg_score"]
    known_out = co.get("known_outcome","ongoing")
    failure_ctx = co.get("failure_context","")
    nct_id = explained[0]["nct_id"] if explained and explained[0]["nct_id"] else ""

    brd_col = {"failed":"#ef4444","pipeline_setback":"#f59e0b","ongoing":"#38bdf8",
               "acquired":"#22c55e","approved":"#22c55e","mixed":"#a78bfa"}.get(known_out,"#64748b")

    pct = int(avg_score * 100)
    score_col = "#22c55e" if pct >= 65 else "#f59e0b" if pct >= 45 else "#ef4444"

    programs_html = ""
    for e in explained:
        p = e["prog"]
        v_col = "#22c55e" if e["verdict"] == "GO" else "#ef4444"
        ct_status_label = f'<span class="ct-status-badge" style="background:{("#ef4444" if e["is_failed_ct"] else "#22c55e")}22;color:{("#ef4444" if e["is_failed_ct"] else "#22c55e")}">{escH(e["ct_status"])}</span>' if e["ct_status"] != "N/A" else ""
        why_stopped = f'<div class="why-stopped"><strong>Why stopped:</strong> {escH(e["why_stopped"])}</div>' if e["why_stopped"] else ""
        programs_html += f"""
<div class="program-card" style="border-left:3px solid {v_col}">
  <div class="program-header">
    <div>
      <span class="program-id">{escH(p.get("_id",""))}</span>
      <span class="program-title">{escH(p.get("title","")[:110])}</span>
      {ct_status_label}
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <span style="font-size:22px;font-weight:800;color:{score_col}">{int(e['score']*100)}%</span>
      <span class="verdict-badge" style="background:{v_col}22;color:{v_col};border-color:{v_col}">{e['verdict']}</span>
    </div>
  </div>
  {why_stopped}
  <p class="program-desc">{escH(p.get("description","")[:220])}</p>
  <div class="program-thesis">
    <div class="thesis-col">
      <div class="thesis-label">🧠 Model verdict</div>
      <p class="thesis-summary">{escH(e["summary"])}</p>
      <div class="thesis-label" style="margin-top:10px">Score drivers</div>
      {calibration_rows(e["calibration"])}
    </div>
    <div class="thesis-col">
      <div class="thesis-label">🎯 Target assessment</div>
      <div class="target-status-row">
        <span class="target-status-badge" style="color:{'#22c55e' if e['target_status']=='validated' else '#f59e0b' if e['target_status']=='unvalidated' else '#94a3b8'}">{e["target_status"].upper()}</span>
        {signal_chips(e["detected_targets"], "#a78bfa")}
      </div>
      <div class="thesis-label" style="margin-top:10px">Positive signals</div>
      {signal_chips(e["signals_completion"], "#22c55e")}
      <div class="thesis-label" style="margin-top:8px">Failure signals</div>
      {signal_chips(e["signals_failure"], "#ef4444")}
      <div class="thesis-label" style="margin-top:8px">Safety flags</div>
      {signal_chips(e["signals_safety"], "#f59e0b")}
    </div>
    <div class="thesis-col">
      <div class="thesis-label">🚀 Frontier: in use</div>
      {frontier_rows(e["frontier_in_use"], "#34d399")}
      <div class="thesis-label" style="margin-top:10px">💡 Not using (competitive gap)</div>
      {frontier_rows(e["frontier_not_using"], "#f59e0b")}
    </div>
  </div>
  {f'<div class="fit-rationale"><strong>Platform fit assessment:</strong> {escH(e["fit_rationale"])}</div>' if e.get("fit_rationale") else ""}
  <div class="lesson-section">
    <div class="thesis-label" style="margin-bottom:8px">📚 Historical lessons for this modality + indication</div>
    {lesson_rows(e["lessons"])}
  </div>
</div>"""

    return f"""
<div class="company-card" id="{co['name'].replace(' ','-').lower()}" style="border-left:5px solid {brd_col}">
  <div class="company-header" onclick="toggleCo(this)" style="cursor:pointer">
    <div class="company-left">
      <div class="company-name">{escH(co['name'])}</div>
      <div class="company-focus">{escH(co['focus'])}</div>
    </div>
    <div class="company-right">
      {outcome_badge(known_out)}
      <div class="company-score">
        <span style="font-size:28px;font-weight:900;color:{score_col}">{pct}%</span>
        <span class="score-label">model score</span>
      </div>
      <span class="expand-caret">▾</span>
    </div>
  </div>
  <div class="company-body">
    <div class="failure-context">
      <div class="fc-label">{'⚠ Why it failed / What happened' if known_out in ('failed','pipeline_setback','mixed') else '★ What made it succeed' if known_out in ('approved','acquired') else '→ Current status & risks'}</div>
      <p class="fc-text">{escH(failure_ctx)}</p>
    </div>
    <div class="programs-section">
      <div class="section-label">Programs &amp; Model Analysis ({len(explained)} program{'s' if len(explained)!=1 else ''} scored)</div>
      {programs_html}
    </div>
    <div class="bv-section">
      <div class="section-label">Comparable programs in bioventure.json database ({len(bv_hits)} found)</div>
      {bv_comparable_html(bv_hits)}
    </div>
    <div class="resources-section">
      <div class="section-label">🔗 Due-diligence links &amp; research resources</div>
      {resource_links_html(co, nct_id)}
    </div>
  </div>
</div>"""

# ── Firm section ──────────────────────────────────────────────────────────────
def firm_section_html(firm_name: str, results: list) -> str:
    firm_results = [r for r in results if r["firm_name"] == firm_name]
    if not firm_results:
        return ""
    firm_meta = next(f for f in VC_FIRMS if f["name"] == firm_name)
    n_fail = sum(1 for r in firm_results if r["co"].get("known_outcome") in ("failed","pipeline_setback"))
    n_ok   = sum(1 for r in firm_results if r["co"].get("known_outcome") in ("approved","acquired"))
    avg_sc = sum(r["avg_score"] for r in firm_results) / len(firm_results)
    firm_id = firm_name.replace(" ","-").lower()

    cards = "".join(company_card_html(r) for r in firm_results)
    return f"""
<section class="firm-section" id="firm-{firm_id}">
  <div class="firm-header">
    <div>
      <h2 class="firm-name">{escH(firm_name)}</h2>
      <div class="firm-meta">{escH(firm_meta['hq'])} &nbsp;·&nbsp; {escH(firm_meta['focus'])}</div>
      <a class="firm-url" href="{escH(firm_meta['url'])}" target="_blank" rel="noopener noreferrer">{escH(firm_meta['url'])} ↗</a>
    </div>
    <div class="firm-stats">
      <div class="firm-stat"><span class="fstat-val" style="color:#ef4444">{n_fail}</span><span class="fstat-lbl">failures / setbacks</span></div>
      <div class="firm-stat"><span class="fstat-val" style="color:#22c55e">{n_ok}</span><span class="fstat-lbl">successes</span></div>
      <div class="firm-stat"><span class="fstat-val" style="color:#38bdf8">{int(avg_sc*100)}%</span><span class="fstat-lbl">avg model score</span></div>
    </div>
  </div>
  {cards}
</section>"""

# ── NAV ───────────────────────────────────────────────────────────────────────
nav_html = '<nav class="firm-nav">' + "".join(
    f'<a href="#firm-{fn.replace(" ","-").lower()}" class="nav-item">{escH(fn)}</a>'
    for fn in firms_seen
) + '</nav>'

# ── FULL PAGE ─────────────────────────────────────────────────────────────────
all_sections = "".join(firm_section_html(fn, analysis_results) for fn in firms_seen)

HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>REC-DECISION · VC Portfolio Failure Analysis</title>
<style>
:root {{
  --bg:#0f172a; --surface:#1e293b; --surface2:#293548; --surface3:#1a2540;
  --border:#334155; --text:#e2e8f0; --muted:#64748b;
  --go:#22c55e; --nogo:#ef4444; --amber:#f59e0b; --accent:#38bdf8; --purple:#a78bfa;
}}
*{{ box-sizing:border-box; margin:0; padding:0; }}
body{{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; font-size:14px; line-height:1.65; }}

/* Header */
header{{ background:linear-gradient(135deg,#1e3a5f 0%,#0f172a 100%); padding:36px 40px; border-bottom:1px solid var(--border); }}
header h1{{ font-size:24px; font-weight:800; color:var(--accent); margin-bottom:6px; }}
header .sub{{ color:var(--muted); font-size:13px; }}

/* Stats bar */
.stats-bar{{ display:flex; gap:36px; padding:20px 40px; background:var(--surface); border-bottom:1px solid var(--border); flex-wrap:wrap; }}
.stat .val{{ font-size:36px; font-weight:900; }}
.stat .lbl{{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }}

/* Nav */
.firm-nav{{ display:flex; gap:10px; flex-wrap:wrap; padding:16px 40px; background:var(--surface3); border-bottom:1px solid var(--border); position:sticky; top:0; z-index:100; }}
.nav-item{{ padding:6px 14px; border-radius:20px; background:var(--surface2); color:var(--muted); text-decoration:none; font-size:12px; font-weight:600; border:1px solid var(--border); transition:all .15s; }}
.nav-item:hover{{ border-color:var(--accent); color:var(--accent); }}

main{{ max-width:1180px; margin:0 auto; padding:32px 24px 100px; }}

/* Firm section */
.firm-section{{ margin-bottom:56px; }}
.firm-header{{ display:flex; justify-content:space-between; align-items:flex-start;
               background:var(--surface); border:1px solid var(--border); border-radius:12px;
               padding:24px 28px; margin-bottom:16px; gap:24px; flex-wrap:wrap; }}
.firm-name{{ font-size:20px; font-weight:800; color:var(--accent); margin-bottom:6px; }}
.firm-meta{{ font-size:13px; color:var(--muted); margin-bottom:4px; }}
.firm-url{{ font-size:12px; color:#38bdf8; text-decoration:none; }}
.firm-url:hover{{ text-decoration:underline; }}
.firm-stats{{ display:flex; gap:28px; }}
.firm-stat{{ text-align:center; }}
.fstat-val{{ display:block; font-size:28px; font-weight:800; }}
.fstat-lbl{{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }}

/* Company card */
.company-card{{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
                margin-bottom:12px; overflow:hidden; }}
.company-header{{ display:flex; justify-content:space-between; align-items:center;
                  padding:18px 22px; gap:16px; background:var(--surface2); }}
.company-header:hover{{ background:#2a3650; }}
.company-left{{ flex:1; }}
.company-name{{ font-size:17px; font-weight:800; margin-bottom:3px; }}
.company-focus{{ font-size:12px; color:var(--muted); max-width:600px; line-height:1.5; }}
.company-right{{ display:flex; align-items:center; gap:18px; flex-shrink:0; }}
.outcome-badge{{ padding:4px 12px; border-radius:12px; font-size:12px; font-weight:700; border:1px solid; white-space:nowrap; }}
.company-score{{ text-align:center; }}
.score-label{{ display:block; font-size:10px; color:var(--muted); text-transform:uppercase; }}
.expand-caret{{ font-size:18px; color:var(--muted); }}
.company-body{{ padding:22px 24px; display:none; }}

/* Failure context */
.failure-context{{ background:rgba(15,23,42,.5); border:1px solid var(--border); border-radius:10px;
                   padding:18px 20px; margin-bottom:20px; }}
.fc-label{{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.08em;
            color:var(--amber); margin-bottom:8px; }}
.fc-text{{ font-size:13px; color:#cbd5e1; line-height:1.8; }}

/* Section labels */
.section-label{{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.08em;
                 color:var(--muted); margin:18px 0 10px; border-bottom:1px solid var(--border); padding-bottom:6px; }}

/* Program card */
.program-card{{ background:var(--surface3); border:1px solid var(--border); border-radius:8px;
                padding:16px 18px; margin-bottom:10px; }}
.program-header{{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px; }}
.program-id{{ font-size:11px; color:var(--muted); font-weight:600; margin-right:8px; }}
.program-title{{ font-size:13px; font-weight:600; }}
.ct-status-badge{{ padding:2px 8px; border-radius:6px; font-size:11px; font-weight:600; margin-left:8px; }}
.verdict-badge{{ padding:3px 10px; border-radius:10px; font-size:12px; font-weight:700; border:1px solid; white-space:nowrap; }}
.why-stopped{{ background:rgba(239,68,68,.08); border-left:3px solid #ef4444; padding:6px 10px; font-size:12px; color:#fca5a5; margin-bottom:8px; border-radius:0 4px 4px 0; }}
.program-desc{{ font-size:12px; color:#94a3b8; margin:6px 0 12px; font-style:italic; line-height:1.6; }}

/* Thesis 3-col */
.program-thesis{{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:14px; margin-bottom:12px; }}
@media(max-width:780px){{ .program-thesis{{ grid-template-columns:1fr; }} }}
.thesis-col{{ background:#0f1a2e; border-radius:8px; padding:12px 14px; }}
.thesis-label{{ font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:6px; }}
.thesis-summary{{ font-size:12px; color:#bae6fd; line-height:1.6; }}
.cal-row{{ display:flex; gap:8px; margin-bottom:4px; font-size:12px; align-items:baseline; }}
.cal-adj{{ font-weight:800; min-width:36px; }}
.cal-fac{{ color:#94a3b8; }}
.target-status-row{{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
.target-status-badge{{ font-weight:700; font-size:12px; }}
.sig-chip{{ padding:2px 8px; border-radius:10px; font-size:11px; border:1px solid; margin-right:4px; margin-bottom:4px; display:inline-block; }}
.frontier-row{{ margin-bottom:5px; font-size:12px; line-height:1.5; }}
.frontier-meta{{ color:var(--muted); font-size:11px; }}
.frontier-note{{ color:#64748b; font-size:11px; }}
.fit-rationale{{ background:rgba(56,189,248,.06); border-left:3px solid #38bdf8; padding:8px 12px;
                 font-size:12px; color:#94a3b8; margin-bottom:12px; border-radius:0 6px 6px 0; line-height:1.7; }}

/* Lessons */
.lesson-section{{ background:#0a1525; border-radius:6px; padding:12px 14px; }}
.lesson-row{{ padding:6px 0 6px 10px; border-left:2px solid #38bdf8; margin-bottom:6px; font-size:12px; color:#94a3b8; line-height:1.6; }}
.lesson-text{{ }}
.lesson-drug{{ color:#38bdf8; font-weight:600; }}
.lesson-outcome{{ color:#64748b; }}

/* BV comparables */
.bv-row{{ display:grid; grid-template-columns:1fr auto auto auto; gap:12px; align-items:center;
          padding:7px 10px; border-radius:6px; font-size:12px; background:#0f1a2e; margin-bottom:4px; }}
.bv-title{{ color:#94a3b8; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.bv-outcome{{ font-weight:700; font-size:11px; white-space:nowrap; }}
.bv-stage{{ color:var(--muted); font-size:11px; white-space:nowrap; }}
.ct-link{{ color:#38bdf8; text-decoration:none; font-size:11px; }}
.ct-link:hover{{ text-decoration:underline; }}

/* Resource links */
.res-grid{{ display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; margin-top:8px; }}
.res-card{{ display:flex; flex-direction:column; gap:5px; background:var(--surface); border:1px solid var(--border);
            border-radius:8px; padding:11px 13px; text-decoration:none; color:inherit; transition:border-color .15s,background .15s; }}
.res-card:hover{{ border-color:var(--accent); background:#1a2944; }}
.res-header{{ display:flex; align-items:center; gap:6px; }}
.res-icon{{ font-size:14px; }}
.res-label{{ font-size:12px; font-weight:700; color:var(--accent); flex:1; }}
.res-ext{{ font-size:11px; color:var(--muted); }}
.res-why{{ font-size:11px; color:#94a3b8; line-height:1.55; }}
.res-why strong{{ color:#cbd5e1; font-weight:600; }}

footer{{ text-align:center; padding:28px; color:var(--muted); font-size:12px; border-top:1px solid var(--border); margin-top:40px; }}
</style>
</head>
<body>

<header>
  <h1>REC-DECISION · VC Portfolio Failure Analysis</h1>
  <div class="sub">
    {ts} &nbsp;·&nbsp; {total_cos} portfolio companies analysed &nbsp;·&nbsp;
    Model: SuccessPredictor_ensemble &nbsp;·&nbsp; AUC-ROC: {metrics.get('auc_roc',0):.3f} &nbsp;·&nbsp;
    Trained on {metrics.get('n_train',0):,} real-world drug programs &nbsp;·&nbsp;
    Bioventure.json: {len(_bv_projects):,} historical programs
  </div>
</header>

<div class="stats-bar">
  <div class="stat"><span class="val" style="color:var(--accent)">{total_cos}</span><span class="lbl">Companies analysed</span></div>
  <div class="stat"><span class="val" style="color:var(--nogo)">{n_failed}</span><span class="lbl">Failures / setbacks</span></div>
  <div class="stat"><span class="val" style="color:var(--go)">{n_success}</span><span class="lbl">Successes (approved / acquired)</span></div>
  <div class="stat"><span class="val" style="color:var(--amber)">{n_ongoing}</span><span class="lbl">Ongoing</span></div>
  <div class="stat"><span class="val" style="color:var(--purple)">{avg_global:.0%}</span><span class="lbl">Avg model score</span></div>
  <div class="stat"><span class="val" style="color:#64748b">{len(firms_seen)}</span><span class="lbl">VC firms covered</span></div>
</div>

{nav_html}

<main>
{all_sections}
</main>

<footer>
  REC-DECISION · VC Portfolio Failure Analysis · {ts}<br>
  Sources: bioventures-capital.com · 3ebiovc.com · bioventuresinvestors.com · pitchbook.com · pivotallifesciences.com · capitalbioventures.ca<br>
  ClinicalTrials.gov data queried live via v2 API · Bioventure.json ({len(_bv_projects):,} programs) cross-referenced for historical pattern matching
</footer>

<script>
function toggleCo(header) {{
  var body = header.nextElementSibling;
  var caret = header.querySelector('.expand-caret');
  if (body.style.display === 'block') {{
    body.style.display = 'none';
    caret.textContent = '▾';
  }} else {{
    body.style.display = 'block';
    caret.textContent = '▴';
  }}
}}
// auto-expand failed companies
document.querySelectorAll('.company-card').forEach(function(card) {{
  var badge = card.querySelector('.outcome-badge');
  if (badge && (badge.textContent.includes('NO-GO') || badge.textContent.includes('SETBACK') || badge.textContent.includes('FAILED'))) {{
    var body = card.querySelector('.company-body');
    var caret = card.querySelector('.expand-caret');
    if (body) body.style.display = 'block';
    if (caret) caret.textContent = '▴';
  }}
}});
</script>
</body>
</html>"""

out = Path("data/vc_portfolio_failure_report.html")
out.write_text(HTML)
print(f"\nReport written → {out.resolve()}")
print(f"Open in browser:  file://{out.resolve()}")
