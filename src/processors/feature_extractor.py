from __future__ import annotations

import re
from typing import Any

# Ordinal stage weights for feature encoding
STAGE_WEIGHTS = {
    "preclinical": 0.0,
    "ind_filing": 0.5,
    "phase1": 1.0,
    "phase2": 2.0,
    "phase3": 3.0,
    "nda_submitted": 3.75,
    "approved": 4.0,
    "discontinued": -1.0,
    "unknown": 0.5,
}

INDICATION_GROUPS = {
    "oncology": ["cancer", "oncology", "tumor", "leukemia", "lymphoma", "sarcoma", "melanoma"],
    "rare_disease": ["rare disease", "orphan", "inherited", "genetic"],
    "immunology": ["autoimmune", "inflammation", "immunology", "arthritis", "lupus"],
    "neurology": ["neurology", "neurodegenerative", "alzheimer", "parkinson", "als", "ms"],
    "cardiovascular": ["cardiovascular", "heart", "cardiac"],
    "metabolic": ["diabetes", "obesity", "metabolic", "nash", "liver"],
    "infectious": ["infectious", "bacterial", "viral", "hiv", "hepatitis"],
}

MECHANISM_GROUPS = {
    "antibody": ["antibody", "monoclonal", "mab", "bispecific", "adc"],
    "small_molecule": ["small molecule", "inhibitor", "kinase", "protease"],
    "cell_therapy": ["car-t", "cell therapy", "t cell", "nk cell"],
    "gene_therapy": ["gene therapy", "crispr", "aav", "lentiviral"],
    "rna": ["rna", "sirna", "mrna", "antisense", "aso"],
    "protein": ["protein", "enzyme", "fusion", "peptide"],
}

# Historical phase transition success rates (industry averages, publicly reported)
# Source: BIO Industry Analysis 2011-2020
BASE_TRANSITION_RATES = {
    "phase1": 0.52,   # P1 → P2
    "phase2": 0.29,   # P2 → P3
    "phase3": 0.58,   # P3 → Approval
    "preclinical": 0.10,
}

INDICATION_ADJUSTMENTS = {
    "oncology": {"phase1": 0.46, "phase2": 0.26, "phase3": 0.49},
    "rare_disease": {"phase1": 0.60, "phase2": 0.47, "phase3": 0.68},
    "immunology": {"phase1": 0.55, "phase2": 0.38, "phase3": 0.61},
    "neurology": {"phase1": 0.49, "phase2": 0.16, "phase3": 0.50},
    "cardiovascular": {"phase1": 0.58, "phase2": 0.34, "phase3": 0.60},
    "metabolic": {"phase1": 0.54, "phase2": 0.31, "phase3": 0.57},
    "infectious": {"phase1": 0.57, "phase2": 0.40, "phase3": 0.63},
}

# Validated molecular targets with at least one FDA/EMA approved drug.
# Mentioning these raises the prior — the biology is de-risked.
# Sources: FDA approvals database, ChEMBL max_phase=4 approved targets.
_VALIDATED_TARGETS = re.compile(
    r"\b("
    # Oncology — highly validated
    r"HER2|ERBB2|PD-?1|PD-?L1|VEGF|VEGFR|EGFR|ALK|BRAF|MEK|CDK4|CDK6|"
    r"BCR-?ABL|JAK|BTK|PARP|CD20|CD19|CD38|CD22|RANKL|CTLA-?4|"
    r"KRAS\s*G12C|RET|MET|NTRK|ROS1|FGFR|IDH1|IDH2|FLT3|"
    # Immunology
    r"IL-?6|IL-?17|IL-?23|IL-?4|IL-?13|TNF|TNF-?alpha|"
    r"CD80|CD86|integrin|BAFF|IL-?12|"
    # Neurology — only amyloid-beta has recent FDA approval (lecanemab 2023)
    r"amyloid beta|"
    # Cardiovascular
    r"PCSK9|factor Xa|thrombin|angiotensin|"
    # Metabolic
    r"GLP-?1|GLP1|GIP|SGLT2|DPP-?4|"
    # Infectious
    r"CCR5|integrase|protease|neuraminidase|capsid"
    r")\b",
    re.IGNORECASE,
)

# Targets with limited/no approved drugs — higher risk territory
_UNVALIDATED_TARGETS = re.compile(
    r"\b("
    r"mesothelin|EpCAM|claudin|mucin|GD2|PSMA|TIGIT|LAG-?3|TIM-?3|"
    r"VISTA|STING|RIG-?I|NLRP3|MDM2|BCL-?XL|PRMT5|LSD1|DOT1L|"
    r"tau\s+vaccine|tau\s+antibody|tau\s+targeting|SNCA|alpha-?synuclein|"
    r"TDP-?43|FUS\b|C9orf72|progranulin"
    r")\b",
    re.IGNORECASE,
)

# Program completion / termination signals in free text
_COMPLETION_SIGNAL = re.compile(
    r"\b(completed?|met\s+primary|significant\s+efficacy|positive\s+data|"
    r"approved|accelerated\s+approval|breakthrough\s+therapy|fast\s+track|"
    r"priority\s+review|positive\s+phase)\b",
    re.IGNORECASE,
)

_FAILURE_SIGNAL = re.compile(
    r"\b(terminated|withdrawn|failed|failure|futility|missed\s+primary|"
    r"no\s+significant\s+efficacy|discontinued|halted|suspended|"
    r"negative\s+(?:data|result)|did\s+not\s+meet)\b",
    re.IGNORECASE,
)


# ── Molecular format / scaffold detection ─────────────────────────────────────
# Fine-grained sub-class within a technology — tells you *how* the drug is built,
# not just what class it belongs to.  These appear in clinical filings, press
# releases, and ChEMBL mechanism-of-action fields.
_FORMAT_PATTERNS: dict[str, re.Pattern] = {
    # ── Antibody formats ─────────────────────────────────────────────────────
    "igg_full":        re.compile(
        r"\b(IgG1|IgG2|IgG4|full.?length antibody|monoclonal antibody|mAb)\b", re.IGNORECASE),
    "nanobody":        re.compile(
        r"\b(nanobody|VHH|single.?domain antibody|sdAb|camelid antibody)\b", re.IGNORECASE),
    "fab_scfv":        re.compile(
        r"\b(Fab fragment|Fab'\+|scFv|single.?chain Fv|minibody|diabody|triabody)\b", re.IGNORECASE),
    "fc_fusion":       re.compile(
        r"\b(Fc.?fusion|albumin.?fusion|Fc-fusion protein|IgG.?Fc|etanercept)\b", re.IGNORECASE),
    "fc_engineered":   re.compile(
        r"\b(ADCC[- ]enhanced|afucosyl|LALA|GASDALIE|half.?life extended Fc|YTE mutation)\b", re.IGNORECASE),
    "probody":         re.compile(
        r"\b(Probody|masked antibody|conditionally active|prodrug antibody|CytomX)\b", re.IGNORECASE),
    # Masking / conditional activation — antibody level
    "ph_switch_ab":    re.compile(
        r"\b(pH.?select(?:ive)?|pH.?depend(?:ent)?\s+(?:binding|antibody)|acid.?switch(?:ed)?\s+antibody|"
        r"sweeping antibody|catch.?and.?release antibody|FcRn.?recyl|pH.?tuned|histidine.?switch|"
        r"pH.?sensitive antibody|acid.?pH binding|REGN3500|pH.?6\.5.{0,15}bind)\b", re.IGNORECASE),
    "probody_dc":      re.compile(
        r"\b(Probody.?drug.?conjugate|PDC\b|masked ADC|activatable ADC|"
        r"protease.?activated.?ADC|conditional.?drug.?conjugate|prodrug.?ADC)\b", re.IGNORECASE),
    "hypoxia_act":     re.compile(
        r"\b(hypoxia.?activat(?:ed)?|HAP\b|hypoxia.?activated.?prodrug|"
        r"nitroimidazole.?conjugate|HAP.?ADC|HAP.?antibody|TH-302|evofosfamide|"
        r"bioreductive.?prodrug|oxygen.?sens(?:itive|ing))\b", re.IGNORECASE),
    "cobra_bispec":    re.compile(
        r"\b(COBRA\b|conditional bispecific|cleavage.?activated bispecific|"
        r"protease.?activat(?:ed)? bispecific|latent bispecific|zymogen.?bispecific|"
        r"pro.?bispecific|TMPP\b|conditional.?T.?cell.?redirection)\b", re.IGNORECASE),
    # ── Bispecific / multispecific formats ───────────────────────────────────
    "bite_format":     re.compile(
        r"\b(BiTE|T.?cell engager|CD3 engager|tandem scFv|TCE\b)\b", re.IGNORECASE),
    "hle_bite":        re.compile(
        r"\b(HLE.?BiTE|half.?life.?extended BiTE|mosunetuzumab|glofitamab|"
        r"epcoritamab|odronextamab|IgG.?like T.?cell engager)\b", re.IGNORECASE),
    "masked_tce":      re.compile(
        r"\b(masked BiTE|pro.?BiTE|tumor.?activated.*engager|XTEN.?masked|"
        r"Probody.?T.?cell engager|conditional.*BiTE|activatable.*T.?cell engager)\b", re.IGNORECASE),
    "split_car":       re.compile(
        r"\b(split CAR|zip.?CAR|ZIP.?CAR|dimerizable CAR|split.?receptor CAR|"
        r"LINK.?CAR|complementation.?CAR|rapamycin.?CAR|chemically.?induced.?dimerization.?CAR|"
        r"CID.?CAR|gated.?CAR\s+split|split.?scFv CAR)\b", re.IGNORECASE),
    "crossmab_kih":    re.compile(
        r"\b(CrossMAb|knob.?into.?hole|KiH\b|CODV|two.?in.?one antibody|IgG.?like bispecific)\b", re.IGNORECASE),
    "dart_format":     re.compile(
        r"\b(DART\b|dual.?affinity re.?targeting|ADAPTIR|TRIDENT)\b", re.IGNORECASE),
    # ── ADC linker / payload scaffolds ───────────────────────────────────────
    "adc_cleavable":   re.compile(
        r"\b(cleavable linker|pH.?sensitive linker|"
        r"MMAE|MMAF|DXd|SN-38|deruxtecan|vedotin|govitecan|exatecan)\b", re.IGNORECASE),
    "adc_noncleavable":re.compile(
        r"\b(non.?cleavable|DM1|DM4|emtansine|mertansine)\b", re.IGNORECASE),
    # ── CAR cell therapy formats ─────────────────────────────────────────────
    "autologous_car":  re.compile(
        r"\b(autologous CAR|autologous T.?cell|patient.?derived CAR)\b", re.IGNORECASE),
    "allogeneic_car":  re.compile(
        r"\b(allogeneic CAR|off.?the.?shelf CAR|iPSC.?derived CAR|"
        r"TALEN.?edited|allogeneic T.?cell)\b", re.IGNORECASE),
    "dual_logic_car":  re.compile(
        r"\b(dual CAR|logic.?gated CAR|AND.?gate CAR|tandem CAR|bispecific CAR)\b", re.IGNORECASE),
    # Conditional activation / next-gen CAR architectures
    "synnotch_car":   re.compile(
        r"\b(syn.?[Nn]otch|synNotch.?gated|synthetic notch receptor|MESA receptor|"
        r"transcriptional circuit CAR|gene circuit CAR)\b", re.IGNORECASE),
    "truck_car":      re.compile(
        r"\b(TRUCK\b|armou?red CAR|armou?red CAR.?T|4th.?gen(?:eration)? CAR|"
        r"IL.?12.?secret|IL.?15.?secret|cytokine.?arm(?:ed)? CAR)\b", re.IGNORECASE),
    "adapter_car":    re.compile(
        r"\b(adapter CAR|adaptor CAR|SUPRA.?CAR|universal CAR|switchable CAR|"
        r"leucine.?zipper CAR|anti.?FITC CAR|modular CAR)\b", re.IGNORECASE),
    "not_gate_car":   re.compile(
        r"\b(iCAR\b|inhibitory CAR|NOT.?gate CAR|protective antigen CAR|"
        r"safety.?switch CAR|antigen.?exclusion CAR)\b", re.IGNORECASE),
    # ── Small molecule scaffolds ─────────────────────────────────────────────
    "covalent_sm":     re.compile(
        r"\b(covalent inhibitor|irreversible inhibitor|cysteine.?reactive|"
        r"warhead|KRAS G12C|osimertinib|afatinib|acrylamide)\b", re.IGNORECASE),
    "macrocycle":      re.compile(
        r"\b(macrocycle|macrolide|cyclic peptide|stapled peptide|peptide macrocycle)\b", re.IGNORECASE),
    "allosteric_sm":   re.compile(
        r"\b(allosteric inhibitor|allosteric modulator|non.?competitive inhibitor|cryptic site)\b", re.IGNORECASE),
    "oral_sm":         re.compile(
        r"\b(oral bioavailability|orally bioavailable|once.?daily oral|oral administration|"
        r"BID oral|tablet|capsule formulation)\b", re.IGNORECASE),
    # ── RNA / ASO formats ────────────────────────────────────────────────────
    "galnac_rnai":     re.compile(
        r"\b(GalNAc|N.?acetylgalactosamine conjugate|trivalent GalNAc|GalNAc.?siRNA)\b", re.IGNORECASE),
    "splice_switch":   re.compile(
        r"\b(splice.?switching|splice modulation|exon skipping|"
        r"nusinersen|SMN2 splicing|cryptic exon)\b", re.IGNORECASE),
    "circular_rna":    re.compile(
        r"\b(circular RNA|circRNA|oRNA|lasso RNA)\b", re.IGNORECASE),
    # ── Gene therapy vectors / formats ───────────────────────────────────────
    "aav_vector":      re.compile(
        r"\b(AAV9|AAV5|AAV8|AAV2|AAVrh10|rAAV|recombinant AAV|"
        r"adeno.?associated virus vector)\b", re.IGNORECASE),
    "lentiviral_vec":  re.compile(
        r"\b(lentiviral vector|LV vector|self.?inactivating lentiviral|SIN.?LV)\b", re.IGNORECASE),
    "base_editing":    re.compile(
        r"\b(base edit(?:ing|or)?|adenine base editor|ABE\b|CBE\b|cytosine base editor)\b", re.IGNORECASE),
    "prime_editing":   re.compile(
        r"\b(prime edit(?:ing|or)?|pegRNA|PEgRNA)\b", re.IGNORECASE),
    # ── Delivery / formulation scaffolds ────────────────────────────────────
    "subcutaneous":    re.compile(
        r"\b(subcutaneous|SC injection|SC formulation|pre.?filled syringe|"
        r"auto.?injector|SC delivery)\b", re.IGNORECASE),
    "pegylated":       re.compile(
        r"\b(PEGylat|PEG.?conjugat|pegylated protein|long.?acting PEG)\b", re.IGNORECASE),
    "nanoparticle":    re.compile(
        r"\b(nanoparticle|nanocapsule|PLGA|polymeric nanoparticle|self.?assembling nanoparticle)\b", re.IGNORECASE),
}

_FORMAT_LABELS: dict[str, str] = {
    "igg_full":        "Full-length IgG antibody",
    "nanobody":        "Nanobody / Single-domain antibody (VHH)",
    "fab_scfv":        "Fab / scFv fragment",
    "fc_fusion":       "Fc-fusion protein",
    "fc_engineered":   "Fc-engineered antibody (ADCC-enhanced / LALA)",
    "probody":         "Masked / Conditionally-active antibody (Probody)",
    "ph_switch_ab":    "pH-selective / Sweeping antibody (acid-switched, FcRn catch-and-release)",
    "probody_dc":      "Probody Drug Conjugate (PDC) / Masked ADC (protease-activated)",
    "hypoxia_act":     "Hypoxia-activated prodrug biologic or conjugate (HAP)",
    "cobra_bispec":    "COBRA / Conditional bispecific (latent, protease-activated)",
    "bite_format":     "BiTE / T-cell engager (tandem-scFv)",
    "crossmab_kih":    "IgG-like bispecific (CrossMAb / Knob-into-Hole)",
    "dart_format":     "DART / ADAPTIR bispecific",
    "adc_cleavable":   "ADC — cleavable linker (MMAE, DXd, SN-38)",
    "adc_noncleavable":"ADC — non-cleavable linker (DM1, DM4)",
    "autologous_car":  "Autologous CAR-T",
    "allogeneic_car":  "Allogeneic (off-the-shelf) CAR-T",
    "dual_logic_car":  "Dual / AND-gate logic CAR-T",
    "synnotch_car":    "SynNotch-gated CAR-T (transcriptional circuit)",
    "truck_car":       "Armored / TRUCK CAR-T (4th-generation cytokine-secreting)",
    "adapter_car":     "Adapter / Modular / Universal CAR-T",
    "not_gate_car":    "NOT-gate / Inhibitory CAR-T (iCAR)",
    "hle_bite":        "Half-life extended BiTE (IgG-like T-cell engager)",
    "masked_tce":      "Tumor-activated / Masked T-cell engager (conditional)",
    "split_car":       "Split / ZIP-CAR (dimerizable, chemically-induced assembly)",
    "covalent_sm":     "Covalent / irreversible small molecule",
    "macrocycle":      "Macrocycle / cyclic peptide",
    "allosteric_sm":   "Allosteric inhibitor",
    "oral_sm":         "Oral small molecule",
    "galnac_rnai":     "GalNAc-conjugated siRNA / RNAi",
    "splice_switch":   "Splice-switching ASO",
    "circular_rna":    "Circular RNA (circRNA)",
    "aav_vector":      "AAV gene therapy vector",
    "lentiviral_vec":  "Lentiviral vector (ex-vivo engineering)",
    "base_editing":    "Base editing (ABE / CBE)",
    "prime_editing":   "Prime editing",
    "subcutaneous":    "Subcutaneous formulation",
    "pegylated":       "PEGylated / long-acting",
    "nanoparticle":    "Polymeric nanoparticle delivery",
}

# Format-specific engineering notes: key risk, advantage, or unresolved challenge
_FORMAT_NOTES: dict[str, str] = {
    "nanobody":        "Nanobodies access cryptic epitopes and penetrate solid tumors better than full IgG; half-life extension (Fc-fusion or albumin binding) is typically required for systemic use.",
    "fab_scfv":        "Fragments provide faster tissue penetration and renal clearance; half-life is short without engineering.",
    "probody":         "Conditional activation in the tumor microenvironment reduces systemic toxicity; cleavage efficiency is an open engineering challenge.",
    "ph_switch_ab":    "pH-selective antibodies exploit the acidic tumour microenvironment (pH 6.0–6.8 vs plasma 7.4) via histidine substitutions in CDRs that weaken binding at acidic pH (tumour-release/FcRn recycling strategy) or, in the reverse design, strengthen binding specifically at low pH for tumour-selective engagement. Sweeping antibodies clear soluble antigen via pH-dependent FcRn-mediated recycling without sink saturation. Key design challenge: window between tumour pH and endosomal pH (~5.5) is narrow — over-sensitive designs release too early in endosomes.",
    "probody_dc":      "Probody Drug Conjugates (PDCs, CytomX) attach a masking peptide to the ADC antibody that is cleaved by tumour proteases, activating both the antigen-binding arm and the cytotoxic payload simultaneously. Doubles the selectivity of a standard ADC by masking both the warhead delivery mechanism and the antigen-targeting step. Prototype PDC-001 (CD71, MMAE) showed favourable therapeutic index in models; clinical validation ongoing.",
    "hypoxia_act":     "Hypoxia-activated prodrugs (HAPs) exploit the hypoxic tumour core (pO₂ <10 mmHg vs normal 40–60 mmHg). Nitroimidazole or nitroaromatic trigger groups are reductively activated only under low-oxygen conditions, releasing a cytotoxin (e.g., nitrogen mustard, duocarmycin) or activating an antibody-conjugate. First-generation HAPs (tirapazamine, TH-302/evofosfamide) had limited clinical success partly due to tumour heterogeneity in hypoxia. Next-generation HAP-ADCs and HAP-nanobodies attempt to combine tumour-targeting selectivity with hypoxia-gating.",
    "cobra_bispec":    "COBRA (Conditional Bispecific Redirected Activation) and related latent bispecifics are cleavage-activated formats where the CD3-engaging arm is sterically blocked by a propeptide or masking domain until protease cleavage occurs in the tumour microenvironment. In circulation the molecule is pharmacologically inert with respect to T-cell engagement, eliminating systemic CRS risk. The therapeutic window depends entirely on differential protease activity (e.g., uPA, MMP-14, legumain) between tumour and normal tissues. Key risk: off-tumour protease activity in inflamed or wound-healing tissue can activate the bispecific systemically.",
    "bite_format":     "BiTE format delivers potent T-cell killing but the short half-life (~hours) requires continuous infusion in most indications; half-life-extended formats (HLE-BiTE) under development.",
    "crossmab_kih":    "IgG-like bispecifics retain Fc-mediated effector functions and have week-scale PK; mispairing of heavy/light chains is a manufacturing risk.",
    "dart_format":     "DART molecules are more stable than BiTEs with slightly better PK; still requires engineering for half-life without Fc.",
    "adc_cleavable":   "Cleavable linker ADCs (DXd, MMAE, SN-38) enable bystander killing of antigen-low neighbors but carry broader off-tumor toxicity risk (e.g., ILD with DXd).",
    "adc_noncleavable":"Non-cleavable linker ADCs (DM1, DM4) offer tighter tumor selectivity but have limited bystander effect — suitable only for high-antigen-expressing tumors.",
    "autologous_car":  "Autologous CAR-T achieves high engraftment and persistence but requires 3–6 week vein-to-vein manufacturing per patient; scale and cost are barriers.",
    "allogeneic_car":  "Off-the-shelf allogeneic CAR-T eliminates per-patient manufacturing but immune rejection (host-vs-graft) and lower persistence vs autologous remain key challenges.",
    "dual_logic_car":  "AND-gate logic-gated CARs improve solid tumor selectivity by requiring two antigens; added circuit complexity raises manufacturing and regulatory risk.",
    "synnotch_car":    "SynNotch-gated CARs use a two-step transcriptional circuit: SynNotch receptor binds antigen A → drives expression of a second CAR targeting antigen B. Highest selectivity of any gated architecture but complex gene circuit manufacture; requires both antigens co-expressed on tumour; antigen escape via either target is a resistance mechanism.",
    "truck_car":       "4th-generation armored/TRUCK CARs constitutively or inductively secrete cytokines (IL-12, IL-15, IL-18, IL-21) to remodel the immunosuppressive TME. Systemic cytokine spill can amplify CRS above standard CAR-T; dose calibration of the transgenic cytokine construct is critical. IL-12-armored CARs in early trials show improved efficacy in solid tumours at cost of elevated toxicity.",
    "adapter_car":     "Modular/adapter CARs (SUPRA-CAR, switchable CAR) separate the antigen-binding arm from the T-cell activation signal via an adapter molecule (e.g., leucine zipper, anti-FITC). Allows real-time tumour antigen switching and dose titration of T-cell engagement without re-engineering cells; lower peak CRS due to titratable activation. Manufacturing advantage: one cell product, multiple adapter targets.",
    "not_gate_car":    "NOT-gate CARs (iCAR) co-express an inhibitory receptor (e.g., anti-MUC16 iCAR) that PREVENTS killing when a protective antigen is detected on normal cells, while a standard activating CAR drives tumour killing. Designed to protect normal tissues expressing the on-target antigen. Early clinical data limited; key challenge is matching iCAR affinity to prevent escape without over-suppression.",
    "hle_bite":        "Half-life extended IgG-like BiTEs (mosunetuzumab, glofitamab, epcoritamab) achieve weekly SC or IV dosing — eliminating the 28-day continuous infusion burden of blinatumomab. Fc domain enables outpatient step-dose ramp; CRS rate and severity are lower than tandem-scFv BiTEs due to slower T-cell recruitment kinetics.",
    "masked_tce":      "Tumor-activated T-cell engagers (masked BiTE, Probody-TCE, XTEN-masked) use a cleavable masking domain that is only removed by tumour-specific proteases (e.g., uPA, MMP, legumain). Systemic pharmacology is that of a prodrug — T-cell engagement is minimal in circulation; activated locally in tumour. Reduces systemic CRS and on-target/off-tumour T-cell activation but requires high tumour protease expression for efficacy.",
    "split_car":       "Split / ZIP-CAR designs split the CAR into two non-functional halves that only dimerize upon simultaneous engagement of two antigens (or on addition of a small-molecule CID, e.g., rapamycin-analogue). Neither half alone triggers T-cell activation. Full-AND-gate selectivity is achievable without a transcriptional circuit (unlike SynNotch), making this faster-activating but less durable for low-antigen targets. Key engineering challenge: optimising dimerization affinity to avoid spontaneous assembly at high local concentration.",
    "covalent_sm":     "Covalent inhibitors achieve durable, often once-daily dosing but require careful off-target selectivity profiling to avoid idiosyncratic toxicity.",
    "macrocycle":      "Macrocycles can engage protein-protein interfaces unreachable by conventional small molecules; oral bioavailability is a formulation challenge due to molecular size.",
    "allosteric_sm":   "Allosteric sites are often conserved across resistance mutations; however, identifying tractable allosteric pockets requires deep structural biology investment.",
    "oral_sm":         "Oral route maximises patient convenience; hepatic first-pass, food-effect, and transporter interactions are key PK risks for novel scaffolds.",
    "galnac_rnai":     "GalNAc-conjugated siRNA is the validated liver-delivery system (inclisiran, givosiran); enables SC dosing. Delivery beyond the liver requires alternative conjugates.",
    "splice_switch":   "Splice-switching ASOs can restore full-length protein from near-normal pre-mRNA; CNS indications require intrathecal delivery (nusinersen model).",
    "circular_rna":    "Circular RNA is more resistant to exonuclease degradation than linear mRNA; delivery and translation efficiency at scale are unresolved.",
    "aav_vector":      "AAV serotype dictates tissue tropism (AAV9 → CNS/heart; AAV8 → liver); pre-existing NAb prevalence can exclude >30% of patients. Re-dosing is generally not feasible.",
    "lentiviral_vec":  "Lentiviral vectors provide stable integration for ex-vivo engineering (CAR-T, HSC gene therapy); insertional mutagenesis risk managed by SIN design.",
    "base_editing":    "Base editors achieve single-nucleotide changes without DSBs, reducing indels and off-target risk; limited to C→T or A→G transitions without PE.",
    "prime_editing":   "Prime editing can install all 12 point mutations and small indels without DSBs; efficiency in primary cells in vivo is lower than ABEs.",
    "subcutaneous":    "SC delivery requires volume ≤1.5 mL and low viscosity (<20 cP); high-concentration formulation of large proteins is a key CMC challenge.",
    "pegylated":       "PEGylation extends half-life 5–10× and reduces immunogenicity; can reduce receptor-binding affinity and accumulates in tissue with repeated dosing.",
    "nanoparticle":    "Polymeric NPs offer sustained release and surface functionalisation; batch-to-batch reproducibility and regulatory pathway for novel excipients are open questions.",
}


def detect_formats(full_text: str) -> list[str]:
    """Return list of all detected molecular format/scaffold keys found in full_text."""
    return [fmt for fmt, pat in _FORMAT_PATTERNS.items() if pat.search(full_text)]


# ── Frontier technology landscape by modality ──────────────────────────────────
# For each broad modality group, lists the *newest* technologies being actively
# pursued in clinical/pre-clinical pipelines (as of 2025-2026).
# Each entry: { "tech", "status", "pursuit_level", "note", "detect_hint" }
#   status:        "approved" | "phase3" | "phase2" | "phase1" | "preclinical"
#   pursuit_level: "mainstream" | "emerging" | "experimental"
#   detect_hint:   optional regex string to detect if this specific tech is used

MODALITY_FRONTIER: dict[str, list[dict]] = {

    "antibody": [
        {"tech": "Bispecific / T-cell engager (BiTE, DART, CrossMAb)",
         "status": "approved", "pursuit_level": "mainstream",
         "note": "Multiple bispecifics approved (blinatumomab, teclistamab, mosunetuzumab). "
                 "IgG-like formats (half-life-extended) now dominant for SC dosing.",
         "detect_hint": r"bispecific|BiTE|T.cell engager|CrossMAb|knob.into.hole"},
        {"tech": "Trispecific / multispecific antibody",
         "status": "phase1", "pursuit_level": "emerging",
         "note": "Trispecifics targeting two tumor antigens + CD3 or NK activating receptor "
                 "to overcome antigen escape; very early clinical.",
         "detect_hint": r"trispecific|multispecific|tri.?specific"},
        {"tech": "Nanobody / VHH (single-domain antibody)",
         "status": "approved", "pursuit_level": "emerging",
         "note": "Caplacizumab approved (2019); VHH formats enabling CNS penetration, "
                 "aerosol delivery, and cryptic epitope access are actively pursued.",
         "detect_hint": r"nanobody|VHH|single.domain antibody|sdAb"},
        {"tech": "Masked / Probody (conditional activation)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Probodies activated by tumor-specific proteases (MMP, uPA) to reduce "
                 "systemic toxicity; key test is whether TME protease activity is sufficient.",
         "detect_hint": r"Probody|masked antibody|conditionally active|prodrug antibody"},
        {"tech": "pH-selective antibody (acid-switched, SWEEPS)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Antibodies engineered to bind antigen at pH 6.5 (tumor) but release "
                 "at pH 7.4 (blood), enabling FcRn-recycling and tumour selectivity.",
         "detect_hint": r"pH.selective|acid.switch|pH.dependent binding|SWEEPS"},
        {"tech": "FcRn-engineered antibody (YTE, LS, GASDALIE) for extended half-life",
         "status": "approved", "pursuit_level": "mainstream",
         "note": "YTE/LS mutations extend IgG half-life to 60–90 days enabling quarterly "
                 "SC dosing; widely adopted in respiratory, metabolic, and rare disease.",
         "detect_hint": r"YTE mutation|LS mutation|GASDALIE|FcRn engineering|half.life extended"},
        {"tech": "Antibody-oligonucleotide conjugate (AOC)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Anti-TfR1 antibody conjugated to siRNA or ASO delivers RNA payloads "
                 "to muscle (Duchenne) and CNS — extends tissue reach of RNAi beyond liver.",
         "detect_hint": r"antibody.oligonucleotide|AOC\b|TfR1.siRNA|FORCE platform"},
    ],

    "adc": [
        {"tech": "Site-specific conjugation (DAR 2 homogeneous)",
         "status": "phase2", "pursuit_level": "mainstream",
         "note": "Random conjugation (DAR 2–8 mixture) replaced by site-specific methods "
                 "(engineered cysteines, amber stop codon, enzymatic) for predictable PK and reduced toxicity.",
         "detect_hint": r"site.specific conjugat|homogeneous DAR|DAR\s*2|amber stop|sortase"},
        {"tech": "Bispecific ADC (bsADC)",
         "status": "phase1", "pursuit_level": "emerging",
         "note": "ADC where the antibody arm is bispecific — improves uptake on antigen-low "
                 "tumors by co-targeting a second internalising antigen.",
         "detect_hint": r"bispecific ADC|bsADC|dual.antigen ADC"},
        {"tech": "Dual-payload ADC",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Two mechanistically distinct payloads on one antibody — aims to pre-empt "
                 "payload-resistance; linker complexity is a CMC challenge.",
         "detect_hint": r"dual.payload|two.payload|combination payload ADC"},
        {"tech": "Immune-stimulating ADC (ISAC / ISDC)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Payload is a TLR agonist or STING agonist rather than a cytotoxin — "
                 "converts tumor to immunogenic phenotype in situ.",
         "detect_hint": r"ISAC|immune.stimulat|STING\s+agonist ADC|TLR\s+agonist ADC"},
        {"tech": "Non-internalising ADC (tumor-microenvironment cleavage)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Linker cleaved by extracellular enzymes (MMP, cathepsin B secreted in TME) "
                 "rather than lysosomal processing — opens targets that don't internalise.",
         "detect_hint": r"non.internaliz|extracellular cleavage|TME.activat"},
        {"tech": "Radioimmunoconjugate (targeted radionuclide + antibody)",
         "status": "approved", "pursuit_level": "emerging",
         "note": "Lutathera and Pluvicto established the class with small-molecule ligands; "
                 "antibody-based radioimmunoconjugates (Ac-225, Lu-177) are now in Phase 1/2.",
         "detect_hint": r"radioimmunoconjugate|actinium.225|Ac.225|Lu.177\s+antibody"},
    ],

    "small_molecule": [
        {"tech": "Targeted covalent inhibitor (beyond KRAS G12C)",
         "status": "phase2", "pursuit_level": "mainstream",
         "note": "KRAS G12C (sotorasib, adagrasib) validated the class; G12D, G12V, "
                 "G13C, and KRAS-SOS1 covalent inhibitors now in Phase 1/2.",
         "detect_hint": r"G12D|G12V|G13C|SOS1 inhibitor|covalent KRAS"},
        {"tech": "Molecular glue degrader (CELMoD, RPM-based)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Thalidomide-derived CELMoDs (iberdomide, mezigdomide) recruit neo-substrates "
                 "to CRBN E3 ligase. New natural product-derived molecular glues being discovered by DEL.",
         "detect_hint": r"molecular glue|CELMoD|iberdomide|mezigdomide|CRBN.neo.substrate"},
        {"tech": "Macrocyclic / stapled peptide (PPI disruptor)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Macrocycles and hydrocarbon-stapled peptides disrupt protein-protein "
                 "interfaces (MDM2-p53, BCL-2 family) historically undruggable by flat SMs.",
         "detect_hint": r"macrocycle|stapled peptide|hydrocarbon stap|PPI disrupt"},
        {"tech": "Allosteric inhibitor (cryptic site, cryo-EM enabled)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Cryo-EM has resolved dozens of cryptic allosteric pockets invisible to X-ray; "
                 "SHP2 allosteric inhibitors (RMC-4630) validated the approach.",
         "detect_hint": r"allosteric inhibitor|cryptic pocket|SHP2|cryo.EM.enabled"},
        {"tech": "DNA-encoded chemical library (DEL) + AI hit expansion",
         "status": "preclinical", "pursuit_level": "emerging",
         "note": "Billion-compound DEL screens identify hits for targets with no prior small-molecule "
                 "tractability; AI models then expand hits to lead series.",
         "detect_hint": r"DNA.encoded library|DEL\b|encoded library"},
        {"tech": "Targeted RNA small molecule (rSM, ribocil-class)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Small molecules that bind structured RNA (riboswitches, internal ribosome "
                 "entry sites, repeat expansions) — validated in bacteria, early human trials.",
         "detect_hint": r"RNA.targeting small molecule|riboswitch|ribocil|rSM\b|repeat expansion small"},
        {"tech": "AI / ML de novo designed small molecule",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Insilico Medicine's INS018_055 (IPF, TNIK inhibitor) reached Phase 2 as "
                 "first fully AI-designed small molecule; Recursion, Exscientia actively pursuing.",
         "detect_hint": r"AI.designed|generative AI|AlphaFold|de novo design|Insilico|Exscientia"},
    ],

    "cell_therapy": [
        {"tech": "Allogeneic donor-derived CAR-T (TALEN / CRISPR-edited)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Gene-edited donor T cells (TALEN-knockout of TCR/HLA) provide off-the-shelf "
                 "supply; persistence is lower than autologous and host rejection remains a challenge.",
         "detect_hint": r"allogeneic CAR|off.the.shelf CAR|TALEN.edited|allogeneic T.cell"},
        {"tech": "Allogeneic iPSC-derived CAR-NK / CAR-T",
         "status": "phase1", "pursuit_level": "emerging",
         "note": "iPSC-derived NK cells (Fate Therapeutics, Nkarta) provide reproducible "
                 "off-the-shelf supply; persistence and GvH risk lower than allogeneic T cells.",
         "detect_hint": r"iPSC.derived|iPSC.NK|allogeneic CAR.NK|induced pluripotent"},
        {"tech": "In-vivo CAR-T generation (LNP-delivered mRNA)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "LNP-mRNA encoding CAR construct delivered IV to transiently express CAR "
                 "in patient T cells in vivo — eliminates ex-vivo manufacturing entirely.",
         "detect_hint": r"in.?vivo CAR|LNP.?CAR|mRNA CAR|in vivo T.cell programming"},
        {"tech": "Logic-gated / AND-gate CAR (synNotch, LINK CAR)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Circuit-controlled CARs require two antigen signals to activate — "
                 "reduces on-target/off-tumor toxicity in solid tumors.",
         "detect_hint": r"AND.gate CAR|logic.gated|synNotch|LINK CAR|dual.receptor circuit"},
        {"tech": "Armored CAR (cytokine-secreting, IL-15 / IL-21)",
         "status": "phase1", "pursuit_level": "emerging",
         "note": "CARs engineered to secrete IL-15 or IL-21 survive in the suppressive "
                 "tumor microenvironment where standard CARs exhaust.",
         "detect_hint": r"armored CAR|IL.15 secreting|IL.21 CAR|cytokine.secreting CAR"},
        {"tech": "Gamma-delta T cell therapy",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "γδ T cells are MHC-unrestricted and have intrinsic tumor surveillance; "
                 "allogeneic γδ therapy avoids GvH without TCR knockout.",
         "detect_hint": r"gamma.delta|γδ T cell|Vgamma9|Vdelta2|gdT"},
        {"tech": "TIL therapy (tumor-infiltrating lymphocyte)",
         "status": "approved", "pursuit_level": "emerging",
         "note": "Lifileucel (Iovance) FDA-approved 2024 for melanoma — first approved TIL "
                 "therapy; expanding to NSCLC, cervical cancer.",
         "detect_hint": r"TIL therapy|tumor.infiltrating lymphocyte|lifileucel|Iovance"},
        {"tech": "Regulatory T cell (Treg) therapy for autoimmune",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Polyclonal and antigen-specific CAR-Treg therapies for organ transplant "
                 "rejection, T1D, and MS — tolerogenic rather than cytotoxic paradigm.",
         "detect_hint": r"Treg therapy|regulatory T.cell|CAR.Treg|tolerogenic"},
    ],

    "gene_therapy": [
        {"tech": "Base editing (ABE / CBE)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Adenine base editors (ABE8e) correct A→G mutations without DSBs; "
                 "BEAM-101 for sickle cell in Phase 1/2. Highly precise; limited to transitions.",
         "detect_hint": r"base edit|ABE\b|CBE\b|adenine base editor|cytosine base editor"},
        {"tech": "Prime editing (pegRNA-guided)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Prime editing installs all 12 point mutations and small indels without DSBs; "
                 "efficiency in primary cells in-vivo remains lower than base editors.",
         "detect_hint": r"prime edit|pegRNA|PEgRNA"},
        {"tech": "Epigenome editing (CRISPRa / CRISPRi / dCas9-KRAB)",
         "status": "preclinical", "pursuit_level": "experimental",
         "note": "Transcriptional activation or silencing without altering DNA sequence — "
                 "avoids permanent change concerns; reversible by design.",
         "detect_hint": r"epigenome edit|CRISPRa|CRISPRi|dCas9.KRAB|epigenetic edit"},
        {"tech": "Engineered AAV capsid (MyoAAV, AAVMYO, lung-tropic variants)",
         "status": "phase1", "pursuit_level": "emerging",
         "note": "Directed evolution or ML-designed AAV capsids show 10–100× improved "
                 "muscle/lung/CNS tropism at lower doses, reducing immunogenicity thresholds.",
         "detect_hint": r"MyoAAV|AAVMYO|engineered capsid|capsid engineering|evolved AAV|Anc80"},
        {"tech": "Non-viral gene delivery (LNP, polymer, nanocapsule)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "LNP-based CRISPR delivery (in-vivo) bypasses AAV immunogenicity and "
                 "repeat-dosing limitations; liver delivery proven, extrahepatic LNP in development.",
         "detect_hint": r"non.viral gene|LNP.CRISPR|lipid nanoparticle gene|polymer gene delivery"},
        {"tech": "RNA base editing (ADAR-mediated, RESTORE / LEAPER)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Recruits endogenous ADAR to edit A→I in mRNA without touching DNA — "
                 "reversible, repeat-dosable, avoids DSB risk entirely.",
         "detect_hint": r"ADAR edit|RNA edit|RESTORE\b|LEAPER\b|A.to.I edit"},
        {"tech": "Zinc finger nuclease / ARCUS meganuclease",
         "status": "approved", "pursuit_level": "mainstream",
         "note": "ZFN used in marketed sickle cell/beta-thal therapies (Casgevy precursor); "
                 "ARCUS meganucleases (Precision BioSciences) in active Phase 1.",
         "detect_hint": r"zinc finger nuclease|ZFN\b|meganuclease|ARCUS|Precision BioSciences"},
    ],

    "rna": [
        {"tech": "Self-amplifying RNA (saRNA / replicon RNA)",
         "status": "approved", "pursuit_level": "emerging",
         "note": "saRNA encodes its own replicase — single low-dose achieves durable "
                 "antigen expression. First saRNA vaccine (ARCT-154 for COVID) approved in Japan 2023.",
         "detect_hint": r"self.amplifying RNA|saRNA|replicon RNA|ARCT.154"},
        {"tech": "Circular RNA (circRNA / oRNA)",
         "status": "preclinical", "pursuit_level": "experimental",
         "note": "Covalently closed RNA lacks 5'/3' ends for exonuclease attack — longer "
                 "in-vivo persistence than linear mRNA; translation efficiency still optimised.",
         "detect_hint": r"circular RNA|circRNA|oRNA\b|Olink RNA"},
        {"tech": "ADAR-mediated RNA editing (A→I, W-to-R correction)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Guide RNA recruits endogenous ADAR2 to edit pathogenic A→G point "
                 "mutations in mRNA — avoids genome modification. Wave Life Sciences, Korro Bio.",
         "detect_hint": r"ADAR|RNA edit|W481R|Wave Life Sciences|Korro Bio"},
        {"tech": "Antibody-oligonucleotide conjugate (AOC) for tissue delivery",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Anti-TfR1 antibody conjugated to siRNA delivers to muscle and CNS, "
                 "overcoming liver-centric delivery limitation of GalNAc-siRNA.",
         "detect_hint": r"AOC\b|antibody.oligonucleotide|TfR1.siRNA|FORCE platform|Avidity Biosciences"},
        {"tech": "Selective organ targeting LNP (SORT-LNP, ionisable lipid diversity)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Adding a 5th lipid component (SORT) to LNP redirects mRNA delivery from "
                 "liver to lung, spleen, or kidney — expands mRNA beyond hepatic targets.",
         "detect_hint": r"SORT.LNP|selective organ target|ionisable lipid|organ.selective LNP"},
        {"tech": "tRNA suppression therapy (nonsense mutation readthrough)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Engineered tRNA with cognate anticodon suppresses premature stop codons "
                 "(PTC) to restore full-length protein — applicable to 10% of rare disease pts.",
         "detect_hint": r"tRNA suppression|nonsense suppression|PTC readthrough|amber suppressor tRNA"},
        {"tech": "Anti-miRNA / miRNA replacement (miRNA mimics, anti-miR)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Inhibit oncomiRs (miR-21, miR-122) or replace tumour-suppressor miRNAs; "
                 "GalNAc conjugation enabling liver delivery at low doses.",
         "detect_hint": r"anti.miRNA|miRNA mimic|anti.miR|oncomiR|miR.21|miR.122"},
    ],

    "radioligand": [
        {"tech": "Alpha-emitter radioligand (Ac-225, Bi-213, Ra-223)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Alpha particles deposit 1000× more energy per track than Lu-177 beta — "
                 "2–3 cell diameter range limits off-target irradiation. Key challenge: Ac-225 supply.",
         "detect_hint": r"alpha.emitter|Ac.225|actinium.225|Bi.213|bismuth.213|Ra.223"},
        {"tech": "Pretargeted radioimmunotherapy (click chemistry in-vivo)",
         "status": "phase1", "pursuit_level": "experimental",
         "note": "Antibody pre-delivered to tumor; radionuclide-tetrazine small molecule "
                 "administered later and clicks to TCO on the antibody — reduces systemic radiation dose.",
         "detect_hint": r"pretarget|click chemistry|TCO|tetrazine|bioorthogonal radiolabel"},
        {"tech": "Theranostic pair (same ligand, diagnostic + therapeutic isotope)",
         "status": "approved", "pursuit_level": "mainstream",
         "note": "68Ga-DOTATATE for imaging / 177Lu-DOTATATE for therapy. PSMA-11 / Pluvicto "
                 "pair established the FDA theranostic approval pathway.",
         "detect_hint": r"theranostic|68Ga|diagnostic.therapeutic pair|companion imaging"},
        {"tech": "Radioimmunoconjugate (antibody + radionuclide, large format)",
         "status": "phase2", "pursuit_level": "emerging",
         "note": "Full antibody delivers higher payload but slow clearance increases marrow "
                 "dose; antibody fragments (Fab, nanobody) improve TBR and kidney clearance.",
         "detect_hint": r"radioimmunoconjugate|antibody.radionuclide|RIC\b|radio.antibody"},
    ],
}


def frontier_context(mech_group: str, tech_classes: list[str], fmt_classes: list[str], full_text: str = "") -> dict:
    """
    Given the detected modality, technology classes, formats, and full text, return:
    {
      "landscape":  full list of frontier techs in this modality
                    (each: tech, status, pursuit_level, note, in_use: bool)
      "in_use":     subset the program actually uses (detected in text)
      "not_using":  high-pursuit frontier techs in this modality that are NOT used
    }
    """
    landscape_raw = MODALITY_FRONTIER.get(mech_group, [])

    landscape: list[dict] = []
    for entry in landscape_raw:
        hint = entry.get("detect_hint", "")
        in_use = bool(re.search(hint, full_text, re.IGNORECASE)) if hint and full_text else False
        landscape.append({
            "tech":          entry["tech"],
            "status":        entry["status"],
            "pursuit_level": entry["pursuit_level"],
            "note":          entry["note"],
            "in_use":        in_use,
        })

    in_use_entries = [e for e in landscape if e["in_use"]]
    not_using = [
        e for e in landscape
        if not e["in_use"] and e["pursuit_level"] in ("mainstream", "emerging")
    ]

    return {
        "modality":   mech_group,
        "landscape":  landscape,
        "in_use":     in_use_entries,
        "not_using":  not_using,
    }


# ── On-target / off-tumor and modality inherent toxicity profiles ─────────────
#
# TARGET_SAFETY_PROFILES: per-antigen risks arising from normal tissue expression.
#   detect:         regex to match this antigen in full_text
#   normal_tissues: tissues expressing the target that are NOT the intended tumour
#   risks:          list of {toxicity, severity, frequency, mechanism, manageable, mitigation}
#   class_risk:     "low" | "low-moderate" | "moderate" | "moderate-high" | "high"
#   key_warning:    (optional) critical one-liner shown as an alert

TARGET_SAFETY_PROFILES: list[dict] = [
    {
        "detect": re.compile(r"\bCD19\b", re.IGNORECASE),
        "target": "CD19",
        "normal_tissues": ["Normal B cells (pan-B-cell marker)"],
        "risks": [
            {"toxicity": "B-cell aplasia",
             "severity": "expected / class-effect", "frequency": "universal",
             "mechanism": "CD19 expressed on ALL B cells; depletion is pharmacological, not incidental",
             "manageable": True,
             "mitigation": "IV immunoglobulin (IgG) replacement; reversible on treatment stop"},
            {"toxicity": "Hypogammaglobulinaemia / opportunistic infection",
             "severity": "moderate", "frequency": "common",
             "mechanism": "Prolonged B-cell depletion reduces antibody repertoire",
             "manageable": True,
             "mitigation": "Prophylactic antivirals, IVIG; screen for hepatitis B before therapy"},
        ],
        "class_risk": "moderate",
    },
    {
        "detect": re.compile(r"\bCD20\b", re.IGNORECASE),
        "target": "CD20",
        "normal_tissues": ["Normal B cells", "minor T-cell subsets"],
        "risks": [
            {"toxicity": "B-cell depletion / hypogammaglobulinaemia",
             "severity": "expected / class-effect", "frequency": "universal",
             "mechanism": "On-target B-cell pharmacology",
             "manageable": True,
             "mitigation": "IVIG replacement; antimicrobial prophylaxis"},
            {"toxicity": "First-dose infusion reactions",
             "severity": "mild-moderate", "frequency": "common (~77% rituximab)",
             "mechanism": "Fc-mediated cytokine release on B-cell lysis",
             "manageable": True,
             "mitigation": "Premedication (antihistamine, paracetamol, steroid); step-rate escalation"},
        ],
        "class_risk": "low-moderate",
    },
    {
        "detect": re.compile(r"\bBCMA\b", re.IGNORECASE),
        "target": "BCMA (CD269)",
        "normal_tissues": ["Normal plasma cells", "plasmablasts"],
        "risks": [
            {"toxicity": "Hypogammaglobulinaemia",
             "severity": "moderate", "frequency": "common",
             "mechanism": "Normal plasma cell depletion reduces endogenous IgG production",
             "manageable": True,
             "mitigation": "IVIG replacement; depth of depletion correlates with efficacy"},
        ],
        "class_risk": "low-moderate",
    },
    {
        "detect": re.compile(r"\bCD38\b", re.IGNORECASE),
        "target": "CD38",
        "normal_tissues": ["Red blood cells (low-level)", "plasma cells", "NK cells", "renal tubular cells"],
        "risks": [
            {"toxicity": "Interference with blood bank crossmatch (pan-agglutination)",
             "severity": "logistical", "frequency": "universal",
             "mechanism": "CD38 on RBCs causes false-positive indirect antiglobulin test (IAT)",
             "manageable": True,
             "mitigation": "Daratumumab neutralisation of test RBCs; blood bank pre-notification mandatory"},
            {"toxicity": "Transient NK-cell depletion",
             "severity": "mild", "frequency": "early treatment",
             "mechanism": "CD38 on NK cells → transient NK cytotoxicity reduction",
             "manageable": True,
             "mitigation": "NK reconstitution within weeks; no clinical sequelae at approved dosing"},
            {"toxicity": "Infusion reactions (first-dose)",
             "severity": "mild-moderate", "frequency": "common (~40% first dose, <5% subsequent)",
             "mechanism": "Fc-mediated, particularly driven by RBC-bound CD38",
             "manageable": True,
             "mitigation": "Premedication; SC formulation (daratumumab-hyaluronidase) reduces IRR to <10%"},
        ],
        "class_risk": "low-moderate",
    },
    {
        "detect": re.compile(r"\bHER2|ERBB2\b", re.IGNORECASE),
        "target": "HER2 / ERBB2",
        "normal_tissues": ["Cardiomyocytes (low-level HER2)", "lung epithelium", "GI epithelium", "skin"],
        "risks": [
            {"toxicity": "Cardiotoxicity (LVEF decline / cardiomyopathy)",
             "severity": "moderate", "frequency": "5–15% grade ≥2 (higher with prior anthracyclines)",
             "mechanism": "HER2 signalling required for cardiomyocyte stress response; inhibition → cardiac dysfunction",
             "manageable": True,
             "mitigation": "Baseline LVEF + monitoring every 12 weeks; hold/discontinue on ≥10-point drop or LVEF <50%"},
            {"toxicity": "Interstitial lung disease (ILD) — ADC-specific (DXd payload)",
             "severity": "severe (grade 3-4 ~2–4%; fatal ~0.6%)", "frequency": "~15% any grade with T-DXd",
             "mechanism": "DXd bystander effect in lung epithelium that expresses low HER2 + high capillary exposure",
             "manageable": True,
             "mitigation": "Baseline + 3/6/12-month CT; early high-dose steroids (≥1 mg/kg); "
                           "permanent discontinuation on grade ≥2 ILD per label algorithm"},
        ],
        "class_risk": "moderate",
    },
    {
        "detect": re.compile(r"\bEGFR\b", re.IGNORECASE),
        "target": "EGFR",
        "normal_tissues": ["Skin (keratinocytes)", "GI epithelium", "liver", "kidney tubules"],
        "risks": [
            {"toxicity": "Acneiform skin rash",
             "severity": "mild-moderate (grade 3 in ~10%)", "frequency": "very common (>80%)",
             "mechanism": "EGFR in keratinocytes drives follicular inflammation when inhibited",
             "manageable": True,
             "mitigation": "Prophylactic doxycycline; topical steroids; rash severity correlates with efficacy"},
            {"toxicity": "Diarrhoea / GI mucositis",
             "severity": "mild-moderate", "frequency": "common (40–60%)",
             "mechanism": "EGFR in GI epithelium required for normal crypt maintenance",
             "manageable": True,
             "mitigation": "Loperamide; dose reduction if grade ≥3; rarely treatment-limiting"},
            {"toxicity": "Hypomagnesaemia (anti-EGFR mAbs)",
             "severity": "mild-moderate", "frequency": "30–50% (cetuximab / panitumumab)",
             "mechanism": "EGFR in renal tubule regulates Mg²⁺ reabsorption",
             "manageable": True,
             "mitigation": "IV/oral Mg supplementation; monitor serum Mg regularly"},
        ],
        "class_risk": "moderate",
    },
    {
        "detect": re.compile(r"\bVEGF|VEGFR\b", re.IGNORECASE),
        "target": "VEGF / VEGFR",
        "normal_tissues": ["Vascular endothelium (systemic)", "kidney glomerulus", "wound-healing tissues"],
        "risks": [
            {"toxicity": "Hypertension",
             "severity": "moderate (grade 3 in 5–10%)", "frequency": "very common (25–40%)",
             "mechanism": "VEGF maintains vascular NO production; blockade → vasoconstriction",
             "manageable": True,
             "mitigation": "Standard antihypertensives; dose hold if ≥grade 3"},
            {"toxicity": "Wound healing impairment / surgical risk",
             "severity": "serious", "frequency": "class-effect across all VEGF inhibitors",
             "mechanism": "VEGF required for angiogenesis and tissue repair",
             "manageable": True,
             "mitigation": "Hold VEGF inhibitors ≥4 weeks before/after major surgery"},
            {"toxicity": "GI perforation / fistula",
             "severity": "severe (rare, ~1%)", "frequency": "uncommon",
             "mechanism": "VEGF maintains GI mucosal vasculature integrity",
             "manageable": False,
             "mitigation": "Patient selection: avoid prior abdominal RT; monitor for sudden abdominal pain"},
            {"toxicity": "Proteinuria / nephrotoxicity",
             "severity": "mild-moderate", "frequency": "common (20–30%)",
             "mechanism": "VEGF critical for glomerular podocyte integrity",
             "manageable": True,
             "mitigation": "Urine protein:creatinine monitoring; hold on grade 3+ proteinuria (>3.5 g/day)"},
        ],
        "class_risk": "moderate",
    },
    {
        "detect": re.compile(r"\bPD-?1|PD-?L1\b", re.IGNORECASE),
        "target": "PD-1 / PD-L1 (immune checkpoint)",
        "normal_tissues": ["All peripheral tolerant tissues — expressed on activated T cells and broadly on normal cells"],
        "risks": [
            {"toxicity": "Immune-related Adverse Events (irAEs) — broad class",
             "severity": "variable: mild rash → fatal myocarditis",
             "frequency": "any-grade ~50–60%; grade 3-4 ~10–15%",
             "mechanism": "PD-1/PD-L1 maintains peripheral tolerance; blockade releases autoreactive T cells",
             "manageable": True,
             "mitigation": "Corticosteroids (prednisone 1–2 mg/kg); permanent discontinuation for grade 3-4 organ toxicity"},
            {"toxicity": "Immune colitis",
             "severity": "moderate-severe", "frequency": "~2–5% grade 3-4",
             "mechanism": "Autoreactive T-cell attack on colonic epithelium",
             "manageable": True,
             "mitigation": "Steroids → infliximab for refractory; colonoscopy; permanent discontinuation"},
            {"toxicity": "Immune pneumonitis",
             "severity": "moderate-severe (can be fatal)", "frequency": "~3–5% any grade; ~1% grade 3-4",
             "mechanism": "Autoimmune inflammation of alveolar epithelium",
             "manageable": True,
             "mitigation": "High-dose steroids; permanent discontinuation; early HRCT for new respiratory symptoms"},
            {"toxicity": "Immune myocarditis (~0.1% incidence, ~50% mortality)",
             "severity": "life-threatening", "frequency": "rare",
             "mechanism": "Autoimmune cardiac inflammation; mechanism poorly understood",
             "manageable": False,
             "mitigation": "Immediate high-dose steroids + cardiac support; poor prognosis once established; "
                           "troponin + ECG monitoring at baseline and cycle 1–2"},
        ],
        "class_risk": "moderate-high",
    },
    {
        "detect": re.compile(r"\bCTLA-?4\b", re.IGNORECASE),
        "target": "CTLA-4",
        "normal_tissues": ["T regulatory cells (Tregs)", "activated effector T cells"],
        "risks": [
            {"toxicity": "Severe immune colitis (grade 3-4)",
             "severity": "severe", "frequency": "20–30% grade 3-4 (ipilimumab 3 mg/kg)",
             "mechanism": "CTLA-4 on Tregs; blockade amplifies T-effector activation, particularly in gut mucosa",
             "manageable": True,
             "mitigation": "IV methylprednisolone; infliximab if steroid-refractory; colostomy in extreme cases"},
            {"toxicity": "Hypophysitis (pituitary inflammation)",
             "severity": "moderate", "frequency": "~10% with ipilimumab",
             "mechanism": "Pituitary expresses CTLA-4; direct immune attack",
             "manageable": True,
             "mitigation": "Hormone replacement (hydrocortisone, levothyroxine) — often permanent"},
        ],
        "class_risk": "high",
    },
    {
        "detect": re.compile(r"\bEpCAM\b", re.IGNORECASE),
        "target": "EpCAM",
        "normal_tissues": ["All GI epithelium (high)", "liver ductal cells", "pancreatic ducts", "lung epithelium", "kidney tubules"],
        "risks": [
            {"toxicity": "Severe GI toxicity (vomiting, diarrhoea, mucosal damage)",
             "severity": "severe (dose-limiting in catumaxomab Phase 2/3)",
             "frequency": "near-universal at systemic therapeutic doses",
             "mechanism": "EpCAM expressed at HIGH levels on NORMAL GI epithelium — no therapeutic window for systemic T-cell engagers",
             "manageable": False,
             "mitigation": "No proven systemic mitigation; catumaxomab viable only via intraperitoneal (locoregional) delivery"},
            {"toxicity": "Hepatotoxicity (bile duct attack)",
             "severity": "moderate-severe", "frequency": "common (liver ductal EpCAM expression)",
             "mechanism": "On-target attack on bile duct epithelium",
             "manageable": True,
             "mitigation": "LFT monitoring; dose reduction; locoregional delivery strategies"},
        ],
        "class_risk": "high",
        "key_warning": "⚠ EpCAM HIGH-RISK for systemic therapy: ubiquitous GI epithelium expression means systemic "
                       "EpCAM-targeting T-cell engagers and CAR-T have caused severe, often fatal GI toxicity in trials. "
                       "Locoregional delivery (IP, intratumoral) required. Clinical development as systemic agent is extremely challenging.",
    },
    {
        "detect": re.compile(r"\bMesothelin\b", re.IGNORECASE),
        "target": "Mesothelin",
        "normal_tissues": ["Pleura (low)", "pericardium (low)", "peritoneum (low)"],
        "risks": [
            {"toxicity": "Pleuritis / pleural effusion (on-target serosal inflammation)",
             "severity": "mild-moderate", "frequency": "uncommon at therapeutic doses",
             "mechanism": "Low-level mesothelin on normal serosal surfaces; on-target effect at high drug concentrations",
             "manageable": True,
             "mitigation": "Clinical monitoring; pleural drainage if symptomatic"},
            {"toxicity": "Anaphylaxis / ADA (immunotoxin formats)",
             "severity": "severe (immunotoxin-specific)", "frequency": "common with non-humanised PE38 toxin conjugates",
             "mechanism": "Anti-drug antibody formation against bacterial Pseudomonas exotoxin moiety",
             "manageable": True,
             "mitigation": "Humanised / deimmunised constructs; premedication; limited dosing cycles"},
        ],
        "class_risk": "low-moderate",
        "note": "Mesothelin normal tissue expression is LOW on serosal surfaces (10–100× lower than tumour). "
                "Mesothelioma, pancreatic, and ovarian tumours overexpress it. Therapeutic window exists "
                "but requires careful patient selection and tumour biopsy confirmation.",
    },
    {
        "detect": re.compile(r"\bGD2\b", re.IGNORECASE),
        "target": "GD2 (ganglioside)",
        "normal_tissues": ["Peripheral nerve fibres (DRG, peripheral neurons)", "CNS neurons (low-level)"],
        "risks": [
            {"toxicity": "Severe neuropathic pain (allodynia, hyperalgesia)",
             "severity": "severe (dose-limiting)", "frequency": "near-universal — on-target peripheral nerve toxicity",
             "mechanism": "GD2 on peripheral nerve fibres — antibody + complement/ADCC causes direct neuronal damage",
             "manageable": True,
             "mitigation": "Concurrent IV morphine infusion (dinutuximab label); gabapentin; "
                           "humanised antibodies (dinutuximab-beta) reduce pain severity"},
            {"toxicity": "Peripheral sensory neuropathy",
             "severity": "moderate", "frequency": "common with prolonged treatment",
             "mechanism": "Chronic GD2 targeting of peripheral neurons",
             "manageable": True,
             "mitigation": "Dose reduction; humanised or afucosylated formats reduce ADCC-driven nerve damage"},
        ],
        "class_risk": "high",
        "key_warning": "⚠ GD2: mandatory concurrent opioid analgesia during dinutuximab infusions "
                       "due to severe neuropathic pain. Pain is on-target and expected.",
    },
    {
        "detect": re.compile(r"\bPSMA\b", re.IGNORECASE),
        "target": "PSMA",
        "normal_tissues": ["Salivary glands (high)", "proximal renal tubules (moderate)", "small intestine", "liver (low)"],
        "risks": [
            {"toxicity": "Xerostomia (dry mouth)",
             "severity": "moderate — quality-of-life impacting", "frequency": "very common with Lu-177-PSMA (~86% any grade)",
             "mechanism": "Salivary gland PSMA is HIGH — radioligand accumulates → radiation sialadenitis",
             "manageable": True,
             "mitigation": "Salivary gland cooling during infusion; sialendoscopy; parotid-sparing dose planning; "
                           "S-303 PSMA-targeting with reduced salivary uptake under development"},
            {"toxicity": "Nephrotoxicity",
             "severity": "mild-moderate", "frequency": "uncommon at approved Lu-177 doses",
             "mechanism": "Renal tubular PSMA → radioligand retention in proximal tubules",
             "manageable": True,
             "mitigation": "Amino acid infusion (lysine/arginine) to block tubular reabsorption; renal dosimetry; eGFR monitoring"},
        ],
        "class_risk": "moderate",
    },
    {
        "detect": re.compile(r"\bTNF|TNF.?alpha\b", re.IGNORECASE),
        "target": "TNF-alpha",
        "normal_tissues": ["Broadly expressed on activated macrophages/T cells — systemic immunosuppression"],
        "risks": [
            {"toxicity": "Serious bacterial infections",
             "severity": "moderate-severe", "frequency": "2–3× higher vs placebo",
             "mechanism": "TNF required for granuloma maintenance and acute bacterial defence",
             "manageable": True,
             "mitigation": "TB screening (Quantiferon); hepatitis B screen; avoid in active infection; annual flu vaccine"},
            {"toxicity": "Tuberculosis reactivation",
             "severity": "severe", "frequency": "~4× baseline risk",
             "mechanism": "TNF essential for controlling Mycobacterium tuberculosis granulomata integrity",
             "manageable": True,
             "mitigation": "MANDATORY latent TB treatment (isoniazid 9 months) before starting anti-TNF"},
            {"toxicity": "Demyelination (MS-like, rare)",
             "severity": "severe (rare)", "frequency": "rare (case reports)",
             "mechanism": "TNF inhibition may unmask or worsen autoimmune demyelination",
             "manageable": False,
             "mitigation": "Absolute contraindication in MS; discontinue on new CNS demyelinating symptoms"},
        ],
        "class_risk": "moderate",
    },
    {
        "detect": re.compile(r"\bIL-?6\b", re.IGNORECASE),
        "target": "IL-6 / IL-6R",
        "normal_tissues": ["Systemic pleiotropic cytokine — liver, immune, CNS, haematopoietic"],
        "risks": [
            {"toxicity": "Serious infections (bacterial)",
             "severity": "moderate", "frequency": "elevated vs placebo",
             "mechanism": "IL-6 drives acute-phase response; blockade blunts innate immune signalling",
             "manageable": True,
             "mitigation": "Infection monitoring; avoid in active serious infection"},
            {"toxicity": "Masking of fever in infection / febrile neutropenia",
             "severity": "serious diagnostic concern", "frequency": "class-effect",
             "mechanism": "IL-6 is the main pyrogenic cytokine; blockade prevents fever even in sepsis",
             "manageable": True,
             "mitigation": "CRP and PCT monitoring instead of temperature; lower threshold for empirical antibiotics"},
            {"toxicity": "Dyslipidaemia (elevated LDL / triglycerides)",
             "severity": "mild", "frequency": "common (tocilizumab ~20%)",
             "mechanism": "IL-6 normally promotes lipoprotein clearance via VLDL receptor",
             "manageable": True,
             "mitigation": "Statin initiation per cardiovascular risk; lipid monitoring"},
        ],
        "class_risk": "low-moderate",
    },
    {
        "detect": re.compile(r"\bIL-?17\b", re.IGNORECASE),
        "target": "IL-17 / IL-17A",
        "normal_tissues": ["Mucosal immune response — gut, lung", "antifungal immunity at mucosal barriers"],
        "risks": [
            {"toxicity": "Mucocutaneous Candida infections",
             "severity": "mild-moderate", "frequency": "elevated (secukinumab ~2%)",
             "mechanism": "IL-17 critical for antifungal immunity at mucous membranes",
             "manageable": True,
             "mitigation": "Antifungal treatment; usually mild and fluconazole-responsive"},
            {"toxicity": "IBD exacerbation / new-onset IBD",
             "severity": "moderate-severe", "frequency": "uncommon but class-effect signal",
             "mechanism": "IL-17 has a protective role in gut epithelial barrier integrity",
             "manageable": True,
             "mitigation": "Contraindicated in active IBD; monitor for new GI symptoms"},
        ],
        "class_risk": "low",
    },
    {
        "detect": re.compile(r"\bKRAS\s*G12C\b", re.IGNORECASE),
        "target": "KRAS G12C (mutant-selective covalent inhibitor)",
        "normal_tissues": ["None — G12C mutation is tumour-exclusive (somatic); WT KRAS minimally inhibited at therapeutic doses"],
        "risks": [
            {"toxicity": "Diarrhoea / GI toxicity",
             "severity": "mild-moderate (grade 3 ~20%)", "frequency": "common",
             "mechanism": "Partial WT KRAS inhibition in GI epithelium at high Cmax",
             "manageable": True,
             "mitigation": "Dose reduction; loperamide; food effect — take with food to blunt Cmax"},
            {"toxicity": "Hepatotoxicity (ALT/AST elevation)",
             "severity": "moderate (grade 3 in ~7%)", "frequency": "uncommon but notable",
             "mechanism": "Unknown; possibly metabolite-mediated or off-target covalent binding in hepatocytes",
             "manageable": True,
             "mitigation": "LFT monitoring every 3 weeks; dose hold on grade 3"},
        ],
        "class_risk": "low",
        "note": "KRAS G12C is a tumour-specific somatic mutation — best-in-class therapeutic window "
                "among RAS-family targets. On-target/off-tumour risk is minimal compared to pan-KRAS approaches.",
    },
    {
        "detect": re.compile(r"\bGLP-?1|GLP1\b", re.IGNORECASE),
        "target": "GLP-1 / GLP-1R",
        "normal_tissues": ["GI L-cells", "pancreatic beta cells", "CNS (appetite regulation)", "heart"],
        "risks": [
            {"toxicity": "GI side effects (nausea, vomiting, diarrhoea)",
             "severity": "mild-moderate", "frequency": "very common (30–50%); usually transient",
             "mechanism": "GLP-1R in gut → delayed gastric emptying; CNS → reduced appetite signalling",
             "manageable": True,
             "mitigation": "Slow dose escalation; resolves over 4–8 weeks; adjust injection timing"},
            {"toxicity": "Pancreatitis (rare)",
             "severity": "severe (rare)", "frequency": "<0.5%",
             "mechanism": "GLP-1R in pancreatic acinar cells; elevated amylase/lipase common but usually asymptomatic",
             "manageable": True,
             "mitigation": "Discontinue on confirmed pancreatitis; contraindicated in prior pancreatitis history"},
            {"toxicity": "Thyroid C-cell tumour (rodent signal, human relevance unconfirmed)",
             "severity": "black-box warning (precautionary)", "frequency": "not established in humans",
             "mechanism": "GLP-1R on rodent thyroid C-cells → C-cell hyperplasia; human C-cells have lower receptor density",
             "manageable": True,
             "mitigation": "Black-box warning; contraindicated in personal/family history of medullary thyroid carcinoma or MEN2"},
        ],
        "class_risk": "low-moderate",
    },
    {
        "detect": re.compile(r"\bPCSK9\b", re.IGNORECASE),
        "target": "PCSK9",
        "normal_tissues": ["Liver (primary target site)", "intestine (minor)"],
        "risks": [
            {"toxicity": "Neurocognitive effects (reported signal, not confirmed in large trials)",
             "severity": "unconfirmed", "frequency": "not established",
             "mechanism": "Possible role of LDL-R in CNS lipid metabolism; disputed",
             "manageable": True,
             "mitigation": "FOURIER and ODYSSEY outcome trials did not confirm; no intervention required"},
            {"toxicity": "Injection site reactions",
             "severity": "mild", "frequency": "common (5–10%)",
             "mechanism": "Local immune response to SC antibody",
             "manageable": True,
             "mitigation": "Site rotation; warm needle to room temperature before injection"},
        ],
        "class_risk": "low",
    },
]


# ── Modality / format inherent toxicity profiles ───────────────────────────────
# These arise from HOW the drug is engineered, independent of the target.
# Keyed by tech_class or fmt_class from _TECH_PATTERNS / _FORMAT_PATTERNS.

MODALITY_TOXICITY: dict[str, list[dict]] = {
    # ── Masking / conditional activation ────────────────────────────────────
    "probody": [
        {"toxicity": "Incomplete masking / systemic mask shedding",
         "severity": "moderate (abrogates selectivity if mask fails)",
         "frequency": "depends on linker stability; observed with early EGFR Probody (CX-072 early dose-cohorts)",
         "mechanism": "Protease cleavage of masking peptide in non-tumour tissues (wound healing, inflammation) releases active antibody "
                      "systemically, converting prodrug pharmacology back to standard unmasked antibody toxicity",
         "manageable": True,
         "mitigation": "Human plasma stability assays mandatory; optimise substrate to tumour-selective proteases (uPA, legumain); "
                       "Probody design requires >100-fold tumour/plasma protease ratio for selectivity"},
        {"toxicity": "Reduced potency vs unmasked antibody (on-tumour activation efficiency)",
         "severity": "efficacy concern", "frequency": "depends on tumour protease activity and antigen density",
         "mechanism": "If masking efficiency is >99% systemically but only 80% cleaved in tumour, effective on-tumour dose is lower than unmasked comparator",
         "manageable": True,
         "mitigation": "Protease activity IHC on patient biopsies as enrolment criterion; companion Probody diagnostic tool"},
    ],
    "ph_switch_ab": [
        {"toxicity": "Reduced target engagement at physiological pH (efficacy concern in antigen-low tumours)",
         "severity": "efficacy concern", "frequency": "class-level; depends on histidine substitution depth",
         "mechanism": "pH-selective binding inherently reduces affinity at pH 7.4 (systemic); if tumour pH is not uniformly acidic "
                      "(e.g., well-vascularised tumour core), drug activation is incomplete",
         "manageable": True,
         "mitigation": "pH mapping of tumour (pHe imaging) as patient selection criterion; confirm tumour pHe <6.8 by clinical pH probe or CEST-MRI"},
        {"toxicity": "Endosomal premature release (sweeping antibody off-target recycling)",
         "severity": "mild", "frequency": "theoretical for FcRn-sweeping formats",
         "mechanism": "Endosomal pH (~5.5) is well below the design activation pH (~6.5); "
                      "antibody releases antigen in endosome before lysosomal degradation — rapid antigen clearance from circulation. "
                      "Off-target: if normal cells present the antigen at low levels, sweeping clears soluble antigen indiscriminately",
         "manageable": True,
         "mitigation": "Titrate histidine affinity-switch pH optimum; use pH 6.5-selective designs to avoid early endosomal release"},
    ],
    "probody_dc": [
        {"toxicity": "Systemic payload release if mask fails (amplified ADC toxicity)",
         "severity": "severe if masking fails (off-tumour payload release = unmasked ADC toxicity profile)",
         "frequency": "depends on mask stability; early PDCs showed similar toxicity to unmasked ADCs at high doses",
         "mechanism": "PDC masking adds one additional selectivity layer but does not eliminate systemic ADC toxicity if mask is cleaved off-tumour. "
                      "The same ILD (DXd), neuropathy (DM1), or myelosuppression risks apply if the ADC component is active",
         "manageable": True,
         "mitigation": "Mask stability in human plasma (96h); confirm tumour/normal protease selectivity ratio >100-fold; "
                       "dose-escalation with monitoring identical to unmasked ADC"},
        {"toxicity": "Residual unmasked ADC fraction at high doses (hook-effect analogue)",
         "severity": "moderate", "frequency": "dose-dependent",
         "mechanism": "At high PDC doses, even 0.1% systemically unmasked fraction can reach pharmacologically active ADC concentrations",
         "manageable": True,
         "mitigation": "Maximum recommended dose limited by masking efficiency margin; PK/PD modelling of unmasked fraction mandatory"},
    ],
    "hypoxia_act": [
        {"toxicity": "Activation in normal hypoxic tissues (wound healing, ischaemic tissue, bone marrow)",
         "severity": "moderate", "frequency": "documented with evofosfamide — haematological toxicity driven by marrow hypoxia activation",
         "mechanism": "Bone marrow, wound healing, and renal medulla are physiologically hypoxic (pO2 10–20 mmHg); "
                      "HAP activation in these normal tissues releases cytotoxin off-tumour",
         "manageable": True,
         "mitigation": "Narrow activation pO2 threshold (<5 mmHg) with more reductive trigger groups; "
                       "exploit tumour-unique severely hypoxic core vs physiological hypoxia gradient"},
        {"toxicity": "Heterogeneous tumour activation (efficacy concern: well-vascularised tumours not activated)",
         "severity": "efficacy concern", "frequency": "very common — most solid tumours have variable hypoxia distribution",
         "mechanism": "HAP only activates in tumour regions with pO2 <5–10 mmHg; well-oxygenated tumour edge cells and circulating "
                      "tumour cells are not killed, leaving resistant populations",
         "manageable": True,
         "mitigation": "Pimonidazole or CAIX IHC for hypoxia patient stratification; combine with anti-angiogenic to deepen hypoxia"},
        {"toxicity": "Myelosuppression (on-target normal-tissue activation in bone marrow)",
         "severity": "moderate-severe (dose-limiting in evofosfamide trials)",
         "frequency": "common at therapeutic doses of alkylating HAPs",
         "mechanism": "Bone marrow sinusoidal hypoxia activates HAP in haematopoietic progenitors",
         "manageable": True,
         "mitigation": "G-CSF support; CBC weekly; dose-hold on grade 3 neutropenia; "
                       "HAP-biologics (conjugated to antibody) may restrict distribution to tumour-bound drug"},
    ],
    "cobra_bispec": [
        {"toxicity": "CRS risk REDUCED vs unmasked bispecific (design intent) — but not eliminated",
         "severity": "mild-moderate (lower than unmasked CD3 bispecific)", "frequency": "expected; clinical data limited",
         "mechanism": "Protease-activated bispecific has near-zero CD3 engagement in circulation; "
                      "on-tumour activation still triggers T-cell cytokine release",
         "manageable": True,
         "mitigation": "Step-dose ramp even for conditional bispecifics; tocilizumab on standby"},
        {"toxicity": "Off-tumour protease activation in inflamed tissue (abrogates selectivity)",
         "severity": "moderate (could restore systemic T-cell engagement)", "frequency": "risk in patients with active inflammatory disease",
         "mechanism": "uPA, MMP, and legumain are upregulated in wound healing, RA synovium, and IBD mucosa; "
                      "COBRA activation in these sites redirects T cells to normal tissues",
         "manageable": True,
         "mitigation": "Exclude patients with active inflammatory conditions; biomarker-guided patient selection; "
                       "choose activation proteases with highest tumour selectivity (legumain > MMP-2 > uPA for specificity)"},
    ],
    "split_car": [
        {"toxicity": "CRS and ICANS (attenuated vs standard CAR-T — AND-gate reduces non-specific activation)",
         "severity": "mild-moderate", "frequency": "expected; data emerging",
         "mechanism": "Both split halves must co-engage their respective antigens for full T-cell activation; "
                      "single-antigen cells trigger only partial signalling, reducing bystander T-cell activation",
         "manageable": True,
         "mitigation": "Standard tocilizumab/dexamethasone; step-dose ramp; likely outpatient feasible based on attenuated CRS prediction"},
        {"toxicity": "Spontaneous assembly at high local CAR density (loss of AND-gate selectivity)",
         "severity": "moderate (transforms split-CAR into standard single-target CAR at high expression)",
         "frequency": "in vitro concern; clinical significance depends on expression level engineering",
         "mechanism": "When both split-CAR halves are expressed at very high density, dimerization probability increases even without antigen, "
                      "giving proximity-driven background signalling",
         "manageable": True,
         "mitigation": "Titrate lentiviral MOI to control CAR expression; design with lower-affinity dimerization domain (rapamycin CID avoids spontaneous assembly)"},
        {"toxicity": "Chemical dimerizer (CID) toxicity — rapamycin or rapalog",
         "severity": "mild-moderate (rapalogs have on-target immunosuppressive / mTOR effects)",
         "frequency": "dose-dependent; relevant for rapalog-gated split-CAR designs",
         "mechanism": "Rapamycin / rapalog CID required to activate split-CAR triggers mTORC1 inhibition, impairing T-cell expansion and "
                      "memory formation in parallel with activating the split-CAR",
         "manageable": True,
         "mitigation": "Low-dose / pulsed CID dosing; novel orthogonal CID pairs (e.g., gibberellin, abscisic acid) that lack off-target human signalling"},
    ],
    # ── T-cell engager formats ───────────────────────────────────────────────
    "bite_format": [
        {"toxicity": "Cytokine Release Syndrome (CRS)",
         "severity": "moderate-severe (grade 3+ in 2–10%)", "frequency": "very common (~50% any grade, blinatumomab)",
         "mechanism": "Massive T-cell activation on antigen encounter → bulk IL-6, IFN-γ, TNF release",
         "manageable": True,
         "mitigation": "Tocilizumab (IL-6R blockade); corticosteroids; step-dose ramp (9 μg → 28 μg); "
                       "hospitalisation for cycle 1 mandatory"},
        {"toxicity": "ICANS — immune effector cell-associated neurotoxicity",
         "severity": "moderate-severe", "frequency": "~10–20% any grade",
         "mechanism": "T-cell cytokine influx to CNS + BBB disruption → encephalopathy, aphasia, seizure",
         "manageable": True,
         "mitigation": "Dexamethasone; seizure prophylaxis (levetiracetam); neurology referral"},
        {"toxicity": "Short half-life → continuous IV infusion requirement",
         "severity": "logistical (quality-of-life)", "frequency": "class-effect for tandem-scFv BiTEs",
         "mechanism": "Lack of Fc → renal clearance; blinatumomab t½ ~2 hours, requires 28-day continuous infusion",
         "manageable": True,
         "mitigation": "HLE-BiTE (IgG-like, half-life-extended) formats under development; ambulatory pumps for outpatient"},
    ],
    "crossmab_kih": [
        {"toxicity": "CRS (attenuated vs BiTE)",
         "severity": "mild-moderate", "frequency": "common but lower grade than tandem-scFv",
         "mechanism": "IgG-like format has slower T-cell engagement kinetics → less bulk simultaneous activation",
         "manageable": True,
         "mitigation": "Step-dose ramp; outpatient management more feasible"},
    ],
    "dart_format": [
        {"toxicity": "CRS (moderate)",
         "severity": "mild-moderate", "frequency": "similar to IgG-like bispecifics",
         "mechanism": "Intermediate kinetics between BiTE scFv and full IgG",
         "manageable": True,
         "mitigation": "Step-dosing; tocilizumab on standby"},
    ],
    "autologous_car": [
        {"toxicity": "Cytokine Release Syndrome (CRS)",
         "severity": "severe (grade 3+ in 10–30%)", "frequency": "very common (~70–90% any grade)",
         "mechanism": "Massive CAR-T cell expansion on antigen contact → cytokine storm; peak day 7–14 post-infusion",
         "manageable": True,
         "mitigation": "Tocilizumab; siltuximab; corticosteroids; REMS programme; ICU-capable centre required"},
        {"toxicity": "ICANS (CAR-T-related encephalopathy syndrome, CRES)",
         "severity": "severe (grade 3+ in 10–30%)", "frequency": "common (~50% any grade)",
         "mechanism": "Cytokine-mediated BBB disruption + direct T-cell CNS infiltration → confusion, aphasia, cerebral oedema",
         "manageable": True,
         "mitigation": "Dexamethasone; seizure prophylaxis; MRI/EEG monitoring; ICU for grade ≥3"},
        {"toxicity": "Haemophagocytic lymphohistiocytosis / macrophage activation syndrome (HLH/MAS)",
         "severity": "life-threatening", "frequency": "uncommon (~1–5%)",
         "mechanism": "Extreme immune activation → macrophage dysregulation; ferritin >10,000 ng/mL is hallmark",
         "manageable": True,
         "mitigation": "Etoposide; anakinra (IL-1 blockade); ruxolitinib (JAK1/2); aggressive supportive care"},
        {"toxicity": "Prolonged cytopenias (bone marrow suppression)",
         "severity": "moderate-severe", "frequency": "common (post-lymphodepletion conditioning chemo)",
         "mechanism": "Fludarabine/cyclophosphamide conditioning damages haematopoietic progenitors",
         "manageable": True,
         "mitigation": "G-CSF; transfusion support; 30–60 day recovery window expected; infections during nadir"},
        {"toxicity": "Manufacturing failure / out-of-spec product",
         "severity": "operational (patient safety)", "frequency": "3–8% of apheresis attempts",
         "mechanism": "Heavily pre-treated patient T cells may be functionally exhausted → poor ex-vivo expansion",
         "manageable": True,
         "mitigation": "Bridging therapy to reduce tumour burden; cryopreservation redundancy; allogeneic backup strategies"},
    ],
    "allogeneic_car": [
        {"toxicity": "Host rejection of allogeneic cells (limited persistence)",
         "severity": "efficacy concern", "frequency": "common — persistence typically weeks without immune suppression",
         "mechanism": "Host immune system recognises donor HLA; TCR-KO reduces GvH but host-vs-graft still occurs",
         "manageable": True,
         "mitigation": "HLA-KO strategies (B2M knockout); deeper lymphodepletion conditioning; repeat dosing feasible"},
        {"toxicity": "Graft-versus-Host Disease (GvHD) — residual risk",
         "severity": "moderate (lower than haematopoietic SCT)", "frequency": "low with TCR-KO efficiency ~90–95%",
         "mechanism": "Residual alloreactive T cells in product despite TCR knockout",
         "manageable": True,
         "mitigation": "TCR/CD3 depletion post-culture; HLA-matched donor selection; symptom monitoring"},
        {"toxicity": "CRS and ICANS (same profile as autologous)",
         "severity": "moderate", "frequency": "common; possibly attenuated in early trials vs autologous",
         "mechanism": "CAR-T cytokine release on antigen encounter regardless of donor origin",
         "manageable": True,
         "mitigation": "Same as autologous: tocilizumab, dexamethasone"},
    ],
    "dual_logic_car": [
        {"toxicity": "CRS and ICANS (expected; attenuated vs single-target CAR)",
         "severity": "moderate (lower on-target-off-tumor vs standard CAR)", "frequency": "expected; clinical data emerging",
         "mechanism": "AND-gate dual-antigen requirement reduces non-specific T-cell activation, but CRS still occurs at tumour",
         "manageable": True,
         "mitigation": "Standard tocilizumab/dexamethasone toolkit; step-dose ramp; outpatient feasible for lower-grade CRS"},
        {"toxicity": "Antigen escape via single-antigen downregulation",
         "severity": "efficacy concern", "frequency": "common resistance mechanism in heavily pre-treated patients",
         "mechanism": "AND-gate requires BOTH antigens; tumour escapes by downregulating either target",
         "manageable": True,
         "mitigation": "Multiplex IHC / flow cytometry to confirm co-expression before enrolment; consider tri-specific backup"},
    ],
    "synnotch_car": [
        {"toxicity": "CRS and ICANS (expected; potentially most attenuated of all conditional CAR formats)",
         "severity": "mild-moderate (two-step activation inherently slower kinetics)", "frequency": "expected; very limited clinical data",
         "mechanism": "SynNotch-primed expression of second CAR requires sequential antigen encounter → slower T-cell expansion → lower peak cytokine",
         "manageable": True,
         "mitigation": "Standard tocilizumab/dexamethasone; step-dose ramp if needed"},
        {"toxicity": "Off-target gene circuit leak expression (SynNotch basal transcription)",
         "severity": "theoretical genotoxic / functional concern", "frequency": "detected in vitro at low frequency",
         "mechanism": "Basal SynNotch transcriptional activation without antigen can prime CAR expression in absence of tumour",
         "manageable": True,
         "mitigation": "Tight promoter selection; insulator elements in gene circuit design; in vitro antigen-negative killing assay as release criterion"},
        {"toxicity": "Complex multi-component gene circuit: higher risk of loss of circuit integrity on cell expansion",
         "severity": "manufacturing / durability concern", "frequency": "reported in long-term culture studies",
         "mechanism": "Large gene payload (~8–12 kb) strains lentiviral/transposon capacity; silencing of one circuit element disables the gate",
         "manageable": True,
         "mitigation": "mRNA / transposon-based circuit alternatives; PCR/FISH QC of circuit integrity in final product"},
    ],
    "truck_car": [
        {"toxicity": "Systemic hypercytokinaemia (amplified CRS from transgenic cytokine secretion)",
         "severity": "severe (above standard CAR-T baseline)", "frequency": "common in IL-12-armored trials; dose-limiting in early studies",
         "mechanism": "Constitutive or antigen-induced transgenic cytokine (IL-12, IL-15, IL-18) secretion adds systemic cytokine load "
                      "on top of endogenous CAR-T activation; IL-12 particularly drives IFN-gamma burst → macrophage activation",
         "manageable": True,
         "mitigation": "Lower transgenic cytokine dose; antigen-inducible promoter rather than constitutive; "
                       "anti-cytokine antibodies (anti-IL-12 p40) on standby; dose-escalation study design required"},
        {"toxicity": "ICANS / neurotoxicity (amplified by IL-12-driven IFN-gamma)",
         "severity": "moderate-severe", "frequency": "elevated vs standard CAR-T",
         "mechanism": "IL-12 promotes IFN-gamma → amplified macrophage activation and BBB disruption",
         "manageable": True,
         "mitigation": "Dexamethasone; seizure prophylaxis; IFN-gamma monitoring as early biomarker"},
        {"toxicity": "Autoimmune sequelae from persistent IL-12 or IL-15 signalling",
         "severity": "moderate (long-term concern)", "frequency": "not well characterised in short follow-up",
         "mechanism": "Chronic elevated IL-12 can drive Th1 autoimmunity; IL-15 drives NK and T-cell expansion beyond tumour killing",
         "manageable": True,
         "mitigation": "Safety switch (iCasp9, CD20 depletion) incorporated in product design; long-term immune monitoring"},
    ],
    "adapter_car": [
        {"toxicity": "CRS (titratable — key advantage of this format)",
         "severity": "mild-moderate (titratable via adapter dose reduction)", "frequency": "lower than autologous CAR-T in early data",
         "mechanism": "T-cell activation driven by adapter molecule concentration; reducing adapter dose reduces T-cell engagement rate",
         "manageable": True,
         "mitigation": "Reduce or stop adapter infusion; adapter has short half-life (~hours) so effects resolve faster than standard CAR-T"},
        {"toxicity": "Adapter molecule PK/immunogenicity",
         "severity": "mild (novel concern for repeated adapter dosing)", "frequency": "ADA formation risk with repeated bifunctional adapter dosing",
         "mechanism": "Adapter molecule (e.g., FITC-tagged scFv) is itself a foreign protein; anti-drug antibody (ADA) formation can neutralise it",
         "manageable": True,
         "mitigation": "Humanised or minimal adapter design; ADA monitoring; multiple adapter targets allow switching if ADA develops"},
        {"toxicity": "Risk of adapter-independent background CAR-T activation (off-target)",
         "severity": "mild", "frequency": "low with well-designed universal receptor",
         "mechanism": "Universal CAR receptor (e.g., anti-FITC) should not engage endogenous ligands; must validate in human serum",
         "manageable": True,
         "mitigation": "In vitro + in vivo pharmacology without adapter as release criterion; avoid CAR receptors with endogenous cross-reactivity"},
    ],
    "not_gate_car": [
        {"toxicity": "On-target/off-tumour protection: designed to prevent this (iCAR advantage)",
         "severity": "lower than standard CAR-T for targeted normal tissue",
         "frequency": "efficacy of protection depends on iCAR affinity tuning",
         "mechanism": "iCAR co-receptor (e.g., anti-MUC16) transmits inhibitory signal (PD-1/CTLA-4 ITIMs) upon binding protective antigen, "
                      "preventing killing even when activating CAR engages tumour target",
         "manageable": True,
         "mitigation": "Validate iCAR protection with antigen-positive normal cell killing assays; iCAR affinity must exceed activating CAR threshold"},
        {"toxicity": "Escape via protective antigen downregulation on tumour (iCAR evasion)",
         "severity": "efficacy concern", "frequency": "tumours can downregulate iCAR-targeted antigen to restore killing in normal tissues",
         "mechanism": "If tumour also expresses iCAR antigen, downregulation of it allows activating CAR to kill both tumour and normal cells",
         "manageable": True,
         "mitigation": "Choose iCAR antigen with stable normal-tissue expression and no oncogenic downregulation pathway"},
        {"toxicity": "Standard CAR-T CRS and ICANS (from activating CAR arm — unchanged)",
         "severity": "moderate", "frequency": "similar to standard autologous CAR-T",
         "mechanism": "iCAR only controls normal tissue killing, not the magnitude of anti-tumour cytokine release",
         "manageable": True,
         "mitigation": "Standard tocilizumab/dexamethasone toolkit unchanged"},
    ],
    "hle_bite": [
        {"toxicity": "CRS (attenuated vs tandem-scFv BiTE, manageable outpatient)",
         "severity": "mild-moderate (grade 3 in ~2–5%)", "frequency": "common any-grade (~40%) but lower severity than blinatumomab",
         "mechanism": "IgG-like Fc domain slows tissue penetration and T-cell recruitment kinetics vs tandem-scFv; step-up dosing feasible SC",
         "manageable": True,
         "mitigation": "Step-dose ramp (e.g., mosunetuzumab: C1D1 1mg → C1D8 2mg → C1D15 60mg); "
                       "outpatient administration feasible for most patients after C1"},
        {"toxicity": "ICANS (lower than standard BiTE but real signal)",
         "severity": "mild-moderate (grade 3 in ~1–3%)", "frequency": "uncommon",
         "mechanism": "Fc-mediated extended exposure can sustain CNS cytokine levels; less acute than continuous infusion BiTE",
         "manageable": True,
         "mitigation": "Dexamethasone; dose hold; seizure prophylaxis if grade ≥2"},
    ],
    "masked_tce": [
        {"toxicity": "Systemic CRS risk REDUCED vs unmasked BiTE (conditional activation advantage)",
         "severity": "mild (systemic pharmacology is prodrug — minimal systemic T-cell activation)",
         "frequency": "lower rate expected vs comparable unmasked BiTE",
         "mechanism": "Masking domain prevents CD3 binding in systemic circulation; only tumour protease-cleaved drug is active",
         "manageable": True,
         "mitigation": "Monitor protease activity biomarkers; confirm tumour protease expression by biopsy before enrolment"},
        {"toxicity": "Incomplete masking / mask shedding in circulation",
         "severity": "moderate (abrogates selectivity advantage if masking fails)",
         "frequency": "depends on linker design and plasma protease background",
         "mechanism": "Non-tumour proteases (TMPRSS2, plasmin) in circulation can cleave mask; releases active BiTE systemically",
         "manageable": True,
         "mitigation": "Mask stability data in human plasma mandatory; optimised linker selectivity (uPA/legumain preferred over MMP)"},
    ],
    "adc_cleavable": [
        {"toxicity": "Interstitial Lung Disease (ILD) — DXd / topoisomerase-I payloads",
         "severity": "severe (grade 3-4 ~2–4%; fatal ~0.6%)", "frequency": "any-grade ~15% with T-DXd",
         "mechanism": "DXd bystander killing in lung epithelium with low HER2 + high capillary exposure from circulating ADC",
         "manageable": True,
         "mitigation": "Baseline + serial CT (3/6/12 months); early high-dose steroids (prednisolone ≥1 mg/kg); "
                       "permanent discontinuation on grade ≥2 ILD — label algorithm mandatory"},
        {"toxicity": "Myelosuppression (neutropenia, thrombocytopenia)",
         "severity": "moderate (grade 3+ in ~20%)", "frequency": "common",
         "mechanism": "Systemic bystander cytotoxicity from cleaved payload in circulation before full antigen-mediated uptake",
         "manageable": True,
         "mitigation": "G-CSF support; CBC monitoring; dose delay / reduction per label"},
        {"toxicity": "Ocular toxicity (keratitis, blurred vision) — MMAE-containing ADCs",
         "severity": "mild-moderate", "frequency": "common with MMAE payload (enfortumab vedotin: ~40%)",
         "mechanism": "MMAE targets rapidly dividing corneal epithelial cells via bystander mechanism",
         "manageable": True,
         "mitigation": "Ophthalmic drops; slit-lamp monitoring; dose hold for grade ≥2"},
    ],
    "adc_noncleavable": [
        {"toxicity": "Peripheral neuropathy — DM1 / DM4 payloads",
         "severity": "moderate (grade 3 in ~5–10% with T-DM1)", "frequency": "common; cumulative with dose",
         "mechanism": "DM1 released from lysosomes after internalisation enters axons via maytansinoid mechanism",
         "manageable": True,
         "mitigation": "Dose reduction; discontinue on grade ≥3; no proven reversal agents; gabapentin for symptom control"},
        {"toxicity": "Thrombocytopenia",
         "severity": "moderate", "frequency": "common (~30% any grade with T-DM1)",
         "mechanism": "DM1-mediated megakaryocyte inhibition in bone marrow; not receptor-mediated",
         "manageable": True,
         "mitigation": "Platelet count monitoring; dose delay/reduction; no HER2 on platelets — mechanism unclear"},
    ],
    "radioligand": [
        {"toxicity": "Myelosuppression (bone marrow irradiation)",
         "severity": "moderate (grade 3+ in ~5–10% with Lu-177-PSMA)", "frequency": "common",
         "mechanism": "Circulating radioligand irradiates haematopoietic progenitors in bone marrow",
         "manageable": True,
         "mitigation": "FBC monitoring each cycle; dose delay for grade ≥3 cytopenia; dosimetry-guided dosing"},
        {"toxicity": "Nephrotoxicity (renal tubular irradiation)",
         "severity": "mild-moderate", "frequency": "uncommon at approved Lu-177 doses",
         "mechanism": "Renal tubular PSMA/SSTR expression + glomerular filtration → radioligand residence in tubules",
         "manageable": True,
         "mitigation": "Amino acid infusion (lysine/arginine) to compete with tubular reabsorption; eGFR monitoring"},
        {"toxicity": "Long-term secondary malignancy (MDS/AML)",
         "severity": "theoretical (not yet established)", "frequency": "not quantified in short follow-up trials",
         "mechanism": "Beta-irradiation to haematopoietic progenitors over multiple cycles",
         "manageable": True,
         "mitigation": "Cumulative dose limits; long-term FBC follow-up; patient life-expectancy consideration"},
    ],
    "oncolytic": [
        {"toxicity": "Flu-like systemic reactions",
         "severity": "mild-moderate", "frequency": "very common",
         "mechanism": "Viral replication + innate immune sensing → systemic interferon / cytokine release",
         "manageable": True,
         "mitigation": "Paracetamol; NSAIDs; resolves within 48–72 hours post-injection"},
        {"toxicity": "Local herpes reactivation (T-Vec / HSV-based)",
         "severity": "mild", "frequency": "uncommon",
         "mechanism": "Attenuated HSV-1 in T-Vec; reactivation of latent HSV at injection site",
         "manageable": True,
         "mitigation": "Acyclovir prophylaxis; barrier precautions for immunocompromised contacts"},
        {"toxicity": "Hepatotoxicity (systemic oncolytic virus dissemination)",
         "severity": "moderate (rare for local injection)", "frequency": "uncommon at approved IT dosing",
         "mechanism": "IV administration or systemic spread → viral hepatitis; much lower risk with intratumoral route",
         "manageable": True,
         "mitigation": "LFT monitoring; avoid IV route in liver disease; dose hold on grade ≥3 LFT"},
    ],
    "crispr": [
        {"toxicity": "Off-target DNA edits (DSB-mediated genotoxicity)",
         "severity": "severe (theoretical / detected by sequencing)", "frequency": "detectable by whole-genome sequencing; clinical significance uncertain",
         "mechanism": "SpCas9 cleaves off-target sequences with partial complementarity → indels, translocations",
         "manageable": True,
         "mitigation": "High-fidelity Cas9 variants (eSpCas9, HiFi-Cas9); genome-wide sequencing of product; "
                       "base/prime editing as alternative (no DSBs)"},
        {"toxicity": "p53 pathway activation (DNA damage response)",
         "severity": "theoretical genotoxic risk", "frequency": "documented in iPSC / HSC studies",
         "mechanism": "DSBs activate p53; cells with p53 mutations (common in cancer) may gain clonal selection advantage",
         "manageable": True,
         "mitigation": "Prefer base editing (avoids DSBs); p53 mutation screening of starting material; "
                       "clonal haematopoiesis monitoring by sequencing"},
        {"toxicity": "Pre-existing anti-Cas9 immunity (antibody + T-cell)",
         "severity": "moderate", "frequency": "anti-Cas9 antibodies in 5–58% of donors (S. aureus vs S. pyogenes)",
         "mechanism": "Cas9 is a bacterial protein; most humans have prior antibody/T-cell exposure through Staphylococcal colonisation",
         "manageable": True,
         "mitigation": "Cas9 serology screening before therapy; alternative Cas proteins (SaCas9, CjCas9); transient immunosuppression"},
    ],
    "aav_vector": [
        {"toxicity": "Acute hepatotoxicity (transaminase elevation)",
         "severity": "moderate-severe", "frequency": "common at high doses (zolgensma: dose-related)",
         "mechanism": "Immune T-cell response to AAV capsid proteins in transduced hepatocytes; peaks 2–6 weeks post-infusion",
         "manageable": True,
         "mitigation": "High-dose prednisolone (1 mg/kg) initiated pre-emptively; LFT monitoring weekly for 3 months"},
        {"toxicity": "Thrombotic microangiopathy (rare, fatal)",
         "severity": "life-threatening (rare)", "frequency": "rare but fatal cases reported (zolgensma)",
         "mechanism": "Unknown; complement activation by high-dose AAV; dose-threshold effect",
         "manageable": False,
         "mitigation": "Dose particle limits; complement monitoring; plasmapheresis attempted; incomplete understanding"},
        {"toxicity": "Pre-existing NAb exclusion (eligibility concern)",
         "severity": "eligibility concern", "frequency": "NAb prevalence: AAV9 ~30–50% of adults",
         "mechanism": "NAbs bind capsid and prevent transduction → therapy failure",
         "manageable": True,
         "mitigation": "NAb serology screening; exclude high-NAb patients; alternative serotypes; empty-capsid decoy approach"},
        {"toxicity": "Hepatocellular carcinoma (long-term integration risk)",
         "severity": "low-level theoretical (regulatory flag)", "frequency": "3 paediatric HCC cases post-AAV8 factor VIII therapy",
         "mechanism": "Rare integration events near proto-oncogenes; AAV is not designed to integrate but does at low frequency",
         "manageable": True,
         "mitigation": "Regulatory requirement: 15-year long-term follow-up; AFP monitoring; preferential integration away from oncogenes (not controllable)"},
    ],
    "galnac_rnai": [
        {"toxicity": "Injection site reactions",
         "severity": "mild", "frequency": "common (~50% any grade)",
         "mechanism": "Local innate immune response to SC oligonucleotide",
         "manageable": True,
         "mitigation": "Site rotation; topical hydrocortisone; class-effect of all GalNAc-siRNA"},
        {"toxicity": "Off-target silencing (seed-region mediated)",
         "severity": "theoretical", "frequency": "detectable in vitro; clinically significant events rare with modern design",
         "mechanism": "siRNA RISC loading can silence unintended mRNAs with partial 5' seed-region complementarity",
         "manageable": True,
         "mitigation": "Bioinformatic off-target prediction mandatory; 2'-OMe and PS backbone modifications reduce off-target loading"},
    ],
    "mrna": [
        {"toxicity": "Injection site pain / swelling / erythema",
         "severity": "mild", "frequency": "very common (~80%)",
         "mechanism": "Local innate immune activation by ionisable lipid + modified nucleoside mRNA",
         "manageable": True,
         "mitigation": "NSAIDs; local cooling; resolves within days"},
        {"toxicity": "Systemic reactogenicity (fever, myalgia, fatigue)",
         "severity": "mild-moderate", "frequency": "common (~50%)",
         "mechanism": "Innate immune activation by LNP components; modified nucleosides (Ψ-U) reduce but don't eliminate",
         "manageable": True,
         "mitigation": "Paracetamol; resolves within 48 hours; second dose typically more reactogenic"},
        {"toxicity": "Rare anaphylaxis (PEG-related IgE)",
         "severity": "severe (rare)", "frequency": "~1 per 100,000 doses (COVID mRNA vaccines)",
         "mechanism": "Pre-existing anti-PEG IgE from prior cosmetic/pharmaceutical PEG exposure",
         "manageable": True,
         "mitigation": "15-minute post-injection observation; epinephrine on standby; PEG-free LNP formulations in development"},
        {"toxicity": "Myocarditis (mRNA COVID vaccines, young males)",
         "severity": "moderate (rare)", "frequency": "~1–4 per 100,000 doses; higher in males aged 16–29",
         "mechanism": "Unknown; possibly immune-mediated cardiac inflammation; not seen with protein-subunit vaccines",
         "manageable": True,
         "mitigation": "Lower mRNA dose (mRNA-1273 50 μg vs 100 μg reduced incidence); rest; NSAIDs; self-limiting in most"},
    ],
    "protac": [
        {"toxicity": "Neo-substrate off-target degradation (E3-ligase dependent)",
         "severity": "serious theoretical concern", "frequency": "demonstrated in vitro (CRBN PROTACs degrade IKZF1/2)",
         "mechanism": "E3 ligase hijacking may recruit unintended neo-substrates; geometry of ternary complex determines selectivity",
         "manageable": True,
         "mitigation": "Proteome-wide degradation profiling (unbiased mass spec) before IND; E3 selectivity studies"},
        {"toxicity": "Potential teratogenicity (CRBN-based PROTACs)",
         "severity": "severe — black-box level concern", "frequency": "class concern — CRBN is the thalidomide target",
         "mechanism": "Cereblon (CRBN) mediates thalidomide teratogenicity via IKZF1/2 degradation in limb development; "
                      "any CRBN-based PROTAC carries theoretical same risk",
         "manageable": True,
         "mitigation": "Mandatory pregnancy prevention programme (REMS-like); also relevant for male patients via seminal fluid"},
        {"toxicity": "Hook effect at high concentrations",
         "severity": "pharmacological (loss of degradation)", "frequency": "in vitro; manage through PK/PD modelling",
         "mechanism": "At high PROTAC concentrations, binary complexes (no ternary) formed → substrate not degraded",
         "manageable": True,
         "mitigation": "PK-PD modelling to define optimal dose window; fractionated dosing"},
    ],
    "covalent_sm": [
        {"toxicity": "Off-target covalent adducts (idiosyncratic toxicity)",
         "severity": "variable", "frequency": "class concern; historical hesitancy",
         "mechanism": "Electrophilic warhead (acrylamide, chloroacetamide) can react with non-target abundant Cys-residue proteins",
         "manageable": True,
         "mitigation": "isoTOP-ABPP / Cys-SILAC selectivity profiling; lower-reactivity warheads; off-rate tuning"},
    ],
    "macrocycle": [
        {"toxicity": "Poor oral bioavailability",
         "severity": "PK challenge (not direct toxicity)", "frequency": "class challenge MW > 1000 Da",
         "mechanism": "Molecular size limits passive diffusion; efflux by P-gp and BCRP in GI wall",
         "manageable": True,
         "mitigation": "Formulation optimisation (prodrug; lipid SEDDS); cyclosporine validates oral macrocycles are achievable"},
        {"toxicity": "Drug-drug interactions (CYP3A4 / P-gp)",
         "severity": "mild-moderate", "frequency": "class-level (macrolide scaffold often CYP3A4 substrate/inhibitor)",
         "mechanism": "Macrocyclic scaffolds frequently interact with CYP3A4 or P-gp efflux transporters",
         "manageable": True,
         "mitigation": "Early CYP/transporter profiling; DDI studies mandatory before Phase 2"},
    ],
}


def assess_safety_profile(full_text: str, tech_classes: list[str], fmt_classes: list[str]) -> dict:
    """
    Returns an on-target/off-tumor and modality-inherent safety profile.

    {
      "target_risks":      list — per detected antigen: normal tissues, risk list, class_risk
      "modality_risks":    list — per detected format/tech: inherent toxicities with source
      "key_warnings":      list — critical one-liner alerts (e.g. EpCAM systemic warning)
      "overall_risk_tier": str  — "low" | "low-moderate" | "moderate" | "moderate-high" | "high"
    }
    """
    target_risks: list[dict] = []
    key_warnings: list[str] = []
    risk_tiers: list[str] = []

    for profile in TARGET_SAFETY_PROFILES:
        if profile["detect"].search(full_text):
            entry: dict = {
                "target":         profile["target"],
                "normal_tissues": profile["normal_tissues"],
                "risks":          profile["risks"],
                "class_risk":     profile["class_risk"],
            }
            if "key_warning" in profile:
                key_warnings.append(profile["key_warning"])
                entry["key_warning"] = profile["key_warning"]
            if "note" in profile:
                entry["note"] = profile["note"]
            target_risks.append(entry)
            risk_tiers.append(profile["class_risk"])

    modality_risks: list[dict] = []
    # Map tech_classes that have no dedicated fmt_class match to a canonical modality key
    _TECH_TO_MODALITY_KEY: dict[str, str] = {
        "car_cell":    "autologous_car",
        "crispr":      "crispr",
        "mrna":        "mrna",
        "radioligand": "radioligand",
        "rnai":        "galnac_rnai",
        "aav_vector":  "aav_vector",
        "oncolytic":   "oncolytic",
        "protac":      "protac",
    }
    all_modality_keys: set[str] = set(tech_classes + fmt_classes)
    for tc, mk in _TECH_TO_MODALITY_KEY.items():
        if tc in tech_classes and mk not in all_modality_keys:
            all_modality_keys.add(mk)
    for cls in all_modality_keys:
        for tox in MODALITY_TOXICITY.get(cls, []):
            modality_risks.append({"source": cls, **tox})
            if not tox.get("manageable", True) and "severe" in tox.get("severity", ""):
                risk_tiers.append("high")

    # Collapse to overall tier
    def _tier_weight(t: str) -> int:
        return {"high": 4, "moderate-high": 3, "moderate": 2, "low-moderate": 1, "low": 0}.get(t, 0)
    if risk_tiers:
        max_w = max(_tier_weight(t) for t in risk_tiers)
        overall = {4: "high", 3: "moderate-high", 2: "moderate", 1: "low-moderate", 0: "low"}.get(max_w, "moderate")
    else:
        overall = "low"

    return {
        "target_risks":      target_risks,
        "modality_risks":    modality_risks,
        "key_warnings":      key_warnings,
        "overall_risk_tier": overall,
    }


# ── Technology platform detection ─────────────────────────────────────────────
# Regex per technology class. Each entry catches key synonyms used in clinical
# trial registrations, press releases, and ChEMBL/FDA filings.
_TECH_PATTERNS: dict[str, re.Pattern] = {
    "adc":          re.compile(
        r"\b(ADC|antibody[- ]drug conjugate|payload[- ]linker|T-DM1|T-DXd|"
        r"trastuzumab deruxtecan|enfortumab|sacituzumab|Kadcyla|Enhertu)\b",
        re.IGNORECASE),
    "bispecific":   re.compile(
        r"\b(bispecific|bsAb|BiTE|DART|trispecific|multispecific|"
        r"dual[- ]targeting|dual[- ]specific|tandem\s+scFv)\b",
        re.IGNORECASE),
    "car_cell":     re.compile(
        r"\b(CAR-?T|CAR-?NK|TCR-?T|chimeric antigen receptor|"
        r"adoptive cell|TIL therapy|tumor[- ]infiltrating lymphocyte|"
        r"engineered\s+T[- ]cell)\b",
        re.IGNORECASE),
    "crispr":       re.compile(
        r"\b(CRISPR|Cas9|Cas12|Cas13|base edit(?:ing)?|prime edit(?:ing)?|"
        r"gene edit(?:ing)?|HDR|homology[- ]directed)\b",
        re.IGNORECASE),
    "mrna":         re.compile(
        r"\b(mRNA|messenger RNA|self[- ]amplifying RNA|saRNA|"
        r"lipid nanoparticle\s+(?:mRNA|vaccine)|LNP[- ]mRNA)\b",
        re.IGNORECASE),
    "protac":       re.compile(
        r"\b(PROTAC|molecular glue|protein degrader|targeted protein degradation|"
        r"TPD\b|bifunctional degrader|E3 ligase)\b",
        re.IGNORECASE),
    "radioligand":  re.compile(
        r"\b(radioligand|lutetium[- ]177|actinium[- ]225|Lu-?177|Ac-?225|"
        r"DOTATATE|PSMA[- ]\d|theranostic|Pluvicto|Lutathera)\b",
        re.IGNORECASE),
    "oncolytic":    re.compile(
        r"\b(oncolytic virus|oncolytic viral|T-Vec|talimogene|"
        r"oncolytic adenovirus|modified herpes)\b",
        re.IGNORECASE),
    "rnai":         re.compile(
        r"\b(siRNA|RNAi|short interfering|antisense oligonucleotide|ASO\b|"
        r"GalNAc|inclisiran|givosiran|patisiran|lumasiran)\b",
        re.IGNORECASE),
    "neoantigen":   re.compile(
        r"\b(neoantigen|personalised? vaccine|personalized vaccine|"
        r"tumor[- ]specific antigen|mRNA cancer vaccine|individualized vaccine)\b",
        re.IGNORECASE),
    "ai_designed":  re.compile(
        r"\b(AI[- ]designed|ML[- ]designed|de novo design(?:ed)?|"
        r"generative AI|AlphaFold|RFdiffusion|diffusion model|"
        r"machine[- ]learning[- ]designed)\b",
        re.IGNORECASE),
    "lnp":          re.compile(
        r"\b(lipid nanoparticle|LNP\b|nanoparticle deliver|lipid[- ]encapsulated)\b",
        re.IGNORECASE),
}

# Human-readable labels for each tech class
_TECH_LABELS: dict[str, str] = {
    "adc":         "Antibody-Drug Conjugate (ADC)",
    "bispecific":  "Bispecific / Multispecific Antibody",
    "car_cell":    "CAR-T / CAR-NK / Engineered Cell Therapy",
    "crispr":      "CRISPR / Gene Editing",
    "mrna":        "mRNA Therapeutics",
    "protac":      "PROTAC / Targeted Protein Degradation",
    "radioligand": "Radioligand Therapy",
    "oncolytic":   "Oncolytic Virus Therapy",
    "rnai":        "RNAi / siRNA / Antisense (ASO)",
    "neoantigen":  "Personalized Neoantigen Vaccine",
    "ai_designed": "AI / ML-Designed Drug",
    "lnp":         "Lipid Nanoparticle (LNP) Delivery",
}

# Which tech classes are considered "bleeding edge" (novel platform, limited approvals as a class)
_BLEEDING_EDGE = frozenset({
    "crispr", "protac", "neoantigen", "ai_designed",
    "car_cell",   # CAR-T is approved in blood cancers but frontier in solid tumors
    "oncolytic",  # Only T-Vec approved; rest is frontier
})

# Technology × indication fit table.
# score: 0–1 (1 = perfect fit, proven); is_clearcut: evidence-backed clear solution
# rationale: brief evidence statement shown in explain() output.
TECH_FIT: dict[tuple[str, str], tuple[float, bool, str]] = {
    # ADC — proven in oncology (T-DM1, T-DXd, EV, SG); exploratory elsewhere
    ("adc", "oncology"):       (0.85, True,  "Multiple FDA-approved ADCs in solid and hematologic tumors (Kadcyla, Enhertu, Trodelvy)."),
    ("adc", "immunology"):     (0.20, False, "ADCs in autoimmune disease are exploratory; no approved products."),
    ("adc", "rare_disease"):   (0.25, False, "ADC platform applied to rare disease is early-stage."),

    # Bispecifics — approved in oncology and immunology
    ("bispecific", "oncology"):    (0.75, True,  "Bispecific antibodies proven in haem-oncology (Hemlibra, Blincyto, teclistamab)."),
    ("bispecific", "immunology"):  (0.65, True,  "Bispecific anti-cytokine / co-stimulation blockade: emerging approvals (faricimab in ophthalmology)."),
    ("bispecific", "rare_disease"):(0.30, False, "Bispecifics in rare disease is novel, limited precedent."),

    # CAR-T — clear in B-cell/myeloma, very hard in solid tumors
    ("car_cell", "oncology"):      (0.55, False, "CAR-T approved for B-cell malignancies and myeloma; solid tumor CAR-T remains a major unsolved challenge."),
    ("car_cell", "immunology"):    (0.30, False, "CAR-Treg in autoimmune is promising but pre-approval."),
    ("car_cell", "rare_disease"):  (0.20, False, "CAR-cell therapy in rare disease is highly exploratory."),

    # CRISPR — clearest fit is monogenic rare disease
    ("crispr", "rare_disease"):    (0.90, True,  "CRISPR gene editing is a logical clear-cut solution for monogenic diseases (Casgevy approved 2023 for sickle cell / beta-thal)."),
    ("crispr", "oncology"):        (0.40, False, "Ex-vivo CRISPR-edited cell therapies are early-phase; in-vivo CRISPR in solid tumors unproven."),
    ("crispr", "infectious"):      (0.45, False, "CRISPR antivirals are in early clinical stage; no approvals."),
    ("crispr", "neurology"):       (0.35, False, "CNS delivery of CRISPR is a major barrier; early IND stage."),

    # mRNA — proven in infectious disease; emerging in oncology
    ("mrna", "infectious"):        (0.95, True,  "mRNA vaccines have FDA approval for COVID-19 (Comirnaty, Spikevax); RSV mRNA vaccines approved 2024."),
    ("mrna", "oncology"):          (0.50, False, "Personalized mRNA cancer vaccines (BNT111) show promise; no approvals yet."),
    ("mrna", "rare_disease"):      (0.60, True,  "mRNA protein-replacement for rare enzyme deficiencies: logical fit, early approvals pending."),
    ("mrna", "neurology"):         (0.25, False, "CNS delivery of mRNA remains a major barrier."),

    # PROTAC — promising but early clinical
    ("protac", "oncology"):        (0.55, False, "PROTACs targeting AR-v7, BCL-XL, and IRAK4 in oncology: Phase 1/2, no approvals yet."),
    ("protac", "immunology"):      (0.40, False, "PROTACs for IRAK, RIPK, BTK degradation in autoimmune: early clinical."),
    ("protac", "rare_disease"):    (0.30, False, "PROTAC in rare disease: exploratory."),

    # Radioligand — proven in prostate and NETs
    ("radioligand", "oncology"):   (0.85, True,  "Radioligand therapy proven: Pluvicto (PSMA, prostate, 2022), Lutathera (SSTR2, NETs, 2018)."),

    # Oncolytic — only T-Vec approved; limited
    ("oncolytic", "oncology"):     (0.40, False, "Only T-Vec (melanoma, 2015) approved; later programs have not replicated success."),

    # RNAi / siRNA / ASO — proven in liver and cardiovascular
    ("rnai", "cardiovascular"):    (0.85, True,  "Inclisiran (PCSK9 siRNA) approved for LDL lowering; strong liver-targeting precedent."),
    ("rnai", "rare_disease"):      (0.80, True,  "Multiple ASO/siRNA drugs approved for rare disease: patisiran (TTR amyloid), givosiran (AHP), lumasiran (PH1)."),
    ("rnai", "metabolic"):         (0.70, True,  "Liver-targeted GalNAc-siRNA well-characterized for metabolic targets."),
    ("rnai", "oncology"):          (0.30, False, "Tumor delivery of RNAi remains difficult; no oncology approvals."),
    ("rnai", "neurology"):         (0.45, False, "Intrathecal ASOs approved (nusinersen, tofersen); systemic CNS RNAi delivery still challenging."),

    # Neoantigen
    ("neoantigen", "oncology"):    (0.40, False, "Personalized neoantigen vaccines show early signal (BNT111, mRNA-4157) but no approvals."),

    # AI-designed
    ("ai_designed", "oncology"):   (0.45, False, "AI-designed drugs in clinical trials but none approved as a class yet (2024)."),
    ("ai_designed", "rare_disease"):(0.45, False, "AI drug design applied to rare disease: promising but pre-approval."),
    ("ai_designed", "infectious"): (0.45, False, "AI-designed antivirals: early clinical stage."),

    # LNP — a delivery platform, fits multiple indications
    ("lnp", "infectious"):         (0.85, True,  "LNP-mRNA delivery proven by COVID-19 vaccines."),
    ("lnp", "rare_disease"):       (0.60, True,  "LNP used in patisiran (Onpattro) — first approved RNAi/LNP drug (2018)."),
    ("lnp", "oncology"):           (0.40, False, "LNP delivery to tumors faces EPR-effect limitations; no solid-tumor LNP drug approved."),
}

# Default fit when (tech, indication) not in table
_DEFAULT_FIT = (0.35, False, "Limited clinical precedent for this technology-indication combination.")


def detect_technology(full_text: str) -> list[str]:
    """Return list of all detected technology class keys found in full_text."""
    found = [cls for cls, pat in _TECH_PATTERNS.items() if pat.search(full_text)]
    return found if found else ["unknown"]


def _best_tech_fit(
    tech_classes: list[str], ind_group: str
) -> tuple[str, float, bool, str]:
    """
    Given a list of detected tech classes and an indication group, return:
      (primary_tech_class, fit_score, is_clearcut, rationale)
    Selects the (tech, ind) pair with the highest fit score.
    """
    best = ("unknown", 0.35, False, _DEFAULT_FIT[2])
    for tc in tech_classes:
        if tc == "unknown":
            continue
        score, clearcut, rationale = TECH_FIT.get((tc, ind_group), _DEFAULT_FIT)
        if score > best[1]:
            best = (tc, score, clearcut, rationale)
    return best


def characterize(row: dict[str, Any]) -> dict[str, Any]:
    """
    Returns a human-readable characterization of a bioventure's technology
    and whether it is a clear-cut solution to the medical problem.

    Output dict:
    {
      "tech_classes":    list of detected tech class keys
      "tech_labels":     list of human-readable technology names
      "primary_tech":    the top-matched tech class
      "is_bleeding_edge":bool — uses novel platform with limited approvals as a class
      "tech_fit": {
          "score":       float 0–1
          "is_clearcut": bool
          "rationale":   str — supporting evidence statement
      }
      "target_status":   "validated" | "unvalidated" | "unknown"
      "detected_targets":list of matched target strings
      "signals": {
          "completion":  list[str] — positive outcome text matches
          "failure":     list[str] — termination/fail text matches
          "safety":      list[str] — safety concern text matches
      }
      "verdict":         str — one-sentence synthesis
    }
    """
    indication = row.get("indication", "")
    mechanism  = row.get("mechanism", "")
    raw_text   = row.get("raw_text", "") or ""
    title      = row.get("title", "") or ""
    full_text  = f"{indication} {mechanism} {title} {raw_text}"

    ind_group = _group_from_text(full_text, INDICATION_GROUPS)
    mech_group = _group_from_text(full_text, MECHANISM_GROUPS)

    # Technology detection
    tech_classes = detect_technology(full_text)
    primary_tech, fit_score, is_clearcut, fit_rationale = _best_tech_fit(tech_classes, ind_group)
    is_bleeding_edge = any(tc in _BLEEDING_EDGE for tc in tech_classes if tc != "unknown")
    tech_labels = [_TECH_LABELS[tc] for tc in tech_classes if tc in _TECH_LABELS]

    # Format / scaffold detection
    fmt_classes = detect_formats(full_text)
    fmt_labels  = [_FORMAT_LABELS[f] for f in fmt_classes if f in _FORMAT_LABELS]
    fmt_notes   = [{"format": _FORMAT_LABELS.get(f, f), "note": _FORMAT_NOTES[f]}
                   for f in fmt_classes if f in _FORMAT_NOTES]

    # Frontier technology landscape for this modality
    frontier = frontier_context(mech_group, tech_classes, fmt_classes, full_text)

    # On-target / off-tumor and modality inherent safety profile
    safety = assess_safety_profile(full_text, tech_classes, fmt_classes)

    # Target validation
    v_match = _VALIDATED_TARGETS.findall(full_text)
    u_match = _UNVALIDATED_TARGETS.findall(full_text)
    if v_match and not u_match:
        target_status = "validated"
    elif u_match and not v_match:
        target_status = "unvalidated"
    elif v_match and u_match:
        target_status = "mixed"
    else:
        target_status = "unknown"

    # Outcome signals
    completion_matches = _COMPLETION_SIGNAL.findall(full_text)
    failure_matches    = _FAILURE_SIGNAL.findall(full_text)
    safety_matches     = re.findall(
        r"\b(adverse|toxicity|hepatotoxic|dose.limiting|serious\s+AE|black.box|cardiotoxic)\b",
        full_text, re.IGNORECASE)

    # Build verdict
    parts: list[str] = []
    if primary_tech != "unknown":
        label = _TECH_LABELS.get(primary_tech, primary_tech)
        if is_clearcut:
            parts.append(f"{label} is a clear-cut, evidence-backed solution for {ind_group.replace('_',' ')}.")
        elif is_bleeding_edge:
            parts.append(f"{label} is a bleeding-edge platform applied to {ind_group.replace('_',' ')} — promising but unproven at scale.")
        else:
            parts.append(f"{label} has moderate fit for {ind_group.replace('_',' ')} with limited clinical precedent.")
    else:
        parts.append(f"No specific cutting-edge technology platform detected; standard modality for {ind_group.replace('_',' ')}.")

    if target_status == "validated":
        parts.append(f"Target(s) {v_match[:3]} are biologically validated with approved drugs.")
    elif target_status == "unvalidated":
        parts.append(f"Target(s) {u_match[:3]} lack approved drugs — elevated biological risk.")

    if completion_matches:
        parts.append("Positive outcome signals detected in text.")
    if failure_matches:
        parts.append("Termination / failure signals detected in text.")
    if safety_matches:
        parts.append("Safety / toxicity concerns noted.")

    return {
        "tech_classes":     tech_classes,
        "tech_labels":      tech_labels if tech_labels else ["Standard modality"],
        "primary_tech":     primary_tech,
        "is_bleeding_edge": is_bleeding_edge,
        "tech_fit": {
            "score":       round(fit_score, 2),
            "is_clearcut": is_clearcut,
            "rationale":   fit_rationale,
        },
        "formats": {
            "classes":     fmt_classes,
            "labels":      fmt_labels if fmt_labels else ["Not specified"],
            "notes":       fmt_notes,
        },
        "frontier":         frontier,
        "safety_profile":   safety,
        "target_status":    target_status,
        "detected_targets": list(set(v_match[:5] + u_match[:5])),
        "signals": {
            "completion": list(set(m.lower() for m in completion_matches)),
            "failure":    list(set(m.lower() for m in failure_matches)),
            "safety":     list(set(m.lower() for m in safety_matches)),
        },
        "verdict": "  ".join(parts),
    }


def _group_from_text(text: str, group_map: dict[str, list[str]]) -> str:
    lower = text.lower()
    for group, keywords in group_map.items():
        for kw in keywords:
            if kw in lower:
                return group
    return "other"


def extract_features(row: dict[str, Any]) -> dict[str, float]:
    """
    Convert a DB project row into a numeric feature dict.
    All values are floats suitable for ML input.

    Feature groups:
    1. Stage / prior — base rate for this indication × stage
    2. Target validation — is the molecular target known to produce approved drugs?
    3. Clinical signals — biomarker selection, safety flags, completion/failure text
    4. Drug type — indication one-hot, mechanism one-hot

    NOTE: investment_usd is excluded — in ChEMBL/FDA/EMA it is a proxy derived
    from the outcome, not an independent predictor.
    """
    stage = row.get("clinical_stage", "unknown")
    indication = row.get("indication", "")
    mechanism = row.get("mechanism", "")
    raw_text = row.get("raw_text", "") or ""
    title    = row.get("title", "") or ""
    full_text = f"{indication} {mechanism} {title} {raw_text}"

    stage_weight = STAGE_WEIGHTS.get(stage, 0.5)
    ind_group = _group_from_text(full_text, INDICATION_GROUPS)
    mech_group = _group_from_text(full_text, MECHANISM_GROUPS)

    # ── Technology platform ───────────────────────────────────────────────────
    tech_classes = detect_technology(full_text)
    primary_tech, fit_score, is_clearcut, _ = _best_tech_fit(tech_classes, ind_group)
    is_bleeding_edge = float(any(tc in _BLEEDING_EDGE for tc in tech_classes if tc != "unknown"))
    tech_is_clearcut = float(is_clearcut)

    # ── Target validation ─────────────────────────────────────────────────────
    has_validated   = float(bool(_VALIDATED_TARGETS.search(full_text)))
    has_unvalidated = float(bool(_UNVALIDATED_TARGETS.search(full_text)))
    target_score    = has_validated - has_unvalidated   # −1, 0, or +1

    # ── Biomarker / selection ─────────────────────────────────────────────────
    # Generic biomarker mention
    has_biomarker = float(bool(re.search(
        r"\bbiomarker|companion diagnostic|genomic|mutation|expression|"
        r"selected|enriched|biomarker.selected|patient.selection\b",
        full_text, re.IGNORECASE,
    )))

    # ── Safety / toxicity ─────────────────────────────────────────────────────
    has_safety_concern = float(bool(re.search(
        r"\badverse|safety concern|toxicity|hepatotoxic|dose.limiting|"
        r"serious\s+AE|black.box|warning|cardiotoxic\b",
        full_text, re.IGNORECASE,
    )))

    # ── Completion / failure text signals ─────────────────────────────────────
    has_completion_signal = float(bool(_COMPLETION_SIGNAL.search(full_text)))
    has_failure_signal    = float(bool(_FAILURE_SIGNAL.search(full_text)))

    # ── Rare / orphan ─────────────────────────────────────────────────────────
    is_rare = float("rare" in full_text.lower() or ind_group == "rare_disease")

    # ── Historical prior ──────────────────────────────────────────────────────
    ind_rates = INDICATION_ADJUSTMENTS.get(ind_group, {})
    base_rate = BASE_TRANSITION_RATES.get(stage, 0.35)
    prior_p = ind_rates.get(stage, base_rate)

    features = {
        # Stage / prior
        "stage_weight":           stage_weight,
        "prior_probability":      prior_p,
        # Technology platform
        "tech_fit_score":         fit_score,
        "tech_is_clearcut":       tech_is_clearcut,
        "is_bleeding_edge":       is_bleeding_edge,
        # Target biology
        "validated_target":       has_validated,
        "unvalidated_target":     has_unvalidated,
        "target_validation_score": target_score,
        # Clinical signals
        "has_biomarker":          has_biomarker,
        "has_safety_concern":     has_safety_concern,
        "has_completion_signal":  has_completion_signal,
        "has_failure_signal":     has_failure_signal,
        "is_rare_disease":        is_rare,
        # One-hot indication groups
        **{f"ind_{g}": float(ind_group == g) for g in INDICATION_GROUPS},
        # One-hot mechanism groups
        **{f"mech_{g}": float(mech_group == g) for g in MECHANISM_GROUPS},
    }
    return features


def label_for_training(row: dict[str, Any]) -> int | None:
    """
    Binary label: 1 = eventually progressed or approved, 0 = discontinued.
    Returns None if outcome is unknown/ongoing (exclude from supervised training).
    """
    outcome = row.get("outcome", "unknown")
    if outcome in ("approved", "ongoing"):
        return 1
    if "discontinued" in outcome:
        return 0
    return None
