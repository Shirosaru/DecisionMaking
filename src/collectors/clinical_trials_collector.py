from __future__ import annotations

import logging
import re
from typing import Any

from .base_collector import BaseCollector, RawRecord

logger = logging.getLogger(__name__)

_CT_API = "https://clinicaltrials.gov/api/v2/studies"

# Map NCT status to our vocabulary
_STATUS_MAP = {
    "COMPLETED": "ongoing",
    "TERMINATED": "discontinued_p2",
    "WITHDRAWN": "discontinued_p1",
    "SUSPENDED": "discontinued_p2",
    "ACTIVE_NOT_RECRUITING": "ongoing",
    "RECRUITING": "ongoing",
    "APPROVED_FOR_MARKETING": "approved",
}

_PHASE_MAP = {
    "PHASE1": "phase1",
    "PHASE2": "phase2",
    "PHASE3": "phase3",
    "PHASE4": "approved",
    "EARLY_PHASE1": "phase1",
    "NA": "preclinical",
}


def _extract_text(study: dict[str, Any]) -> str:
    proto = study.get("protocolSection", {})
    parts: list[str] = []
    for section in ("identificationModule", "descriptionModule", "conditionsModule",
                    "interventionsModule", "outcomesModule", "eligibilityModule"):
        sec = proto.get(section, {})
        if isinstance(sec, dict):
            parts.append(str(sec))
    return " ".join(parts)


def _first_sponsor(study: dict[str, Any]) -> str:
    sponsors = (
        study.get("protocolSection", {})
             .get("sponsorCollaboratorsModule", {})
             .get("leadSponsor", {})
    )
    return sponsors.get("name", "")


class ClinicalTrialsCollector(BaseCollector):
    """
    Pulls industry-sponsored clinical studies from ClinicalTrials.gov API v2.

    Two query strategies combined:
      1. Disease-area queries — broad coverage across all therapeutic areas
      2. Pharma-sponsor queries — targeted terminated/withdrawn trial pulls
         for 20 major pharma companies (Pfizer, Roche, Novartis, Merck, etc.)

    This captures:
      - Go decisions (completed/ongoing trials)
      - No-go decisions (terminated/withdrawn = killed programs)
      - The full spectrum from preclinical-adjacent (Phase 1) to NDA stage
    """

    name = "clinicaltrials"
    rate_limit_seconds = 0.4

    # Disease area queries (broad coverage)
    _DISEASE_QUERIES = [
        "cancer OR oncology OR tumor OR leukemia OR lymphoma OR sarcoma",
        "rare disease OR orphan drug OR inherited OR genetic disorder",
        "autoimmune OR immunology OR rheumatoid OR lupus OR crohn OR colitis",
        "neurology OR alzheimer OR parkinson OR ALS OR multiple sclerosis",
        "cardiovascular OR heart failure OR hypertension OR coronary",
        "metabolic OR diabetes OR obesity OR NASH OR fatty liver",
        "infectious disease OR HIV OR hepatitis OR antiviral OR antibacterial",
        "gene therapy OR cell therapy OR CAR-T OR CRISPR OR mRNA",
        # ── Masking / conditional activation (Probody, tumor-protease, ISAC) ──
        "probody OR masked antibody OR conditionally active OR tumor-activated OR"
        " protease-cleavable OR zipalertinib OR CX-2009 OR CX-2029 OR CX-072 OR CX-904",
        # ── Radioligand therapy / radiopharmaceuticals ──
        "radioligand therapy OR radiopharmaceutical OR lutetium-177 OR actinium-225 OR"
        " PSMA radioligand OR DOTATATE OR DOTATOC OR radioconjugate OR targeted alpha therapy"
        " OR Pluvicto OR FPI-2265 OR PNT2002",
        # ── Antibody-drug conjugates (comprehensive) ──
        "antibody-drug conjugate OR ADC oncology OR TROP-2 ADC OR HER2 ADC OR"
        " nectin-4 ADC OR claudin ADC OR DXd OR sacituzumab OR enfortumab OR"
        " trastuzumab deruxtecan OR mirvetuximab OR ifinatamab",
        # ── Bispecific antibodies / T-cell engagers ──
        "bispecific antibody oncology OR T-cell engager OR TCE oncology OR"
        " PSMA-CD3 OR EpCAM-CD3 OR DLL3-CD3 OR FLT3-CD3 OR MAGE-A4 OR EGFR-CD3 OR"
        " tarlatamab OR blinatumomab OR mosunetuzumab OR glofitamab",
        # ── KRAS and RAS-pathway selective inhibitors ──
        "KRAS G12C OR KRAS G12D OR KRAS G12V OR sotorasib OR adagrasib OR"
        " MRTX1133 OR RMC-6236 OR SOS1 inhibitor OR SHP2 inhibitor OR"
        " KRASG12C OR pan-KRAS OR RAS-MAPK oncology",
        # ── Synthetic lethality / DNA damage response (DDR) oncology ──
        "synthetic lethality oncology OR PARP inhibitor cancer OR ATR inhibitor OR"
        " ATM inhibitor OR WEE1 inhibitor OR adavosertib OR ceralasertib OR"
        " AZD6738 OR elimusertib OR camonsertib OR talazoparib OR niraparib",
        # ── mRNA cancer vaccines / personalized neoantigen vaccines ──
        "mRNA cancer vaccine OR neoantigen vaccine OR personalized cancer vaccine OR"
        " tumor neoantigen OR mRNA-4157 OR BNT111 OR BNT112 OR BNT113 OR V940 OR"
        " autogene cevumeran OR MAGE-A3 vaccine OR KRAS vaccine",
        # ── Oncolytic viruses / STING agonists / innate immune oncology ──
        "oncolytic virus OR STING agonist oncology OR cGAS-STING cancer OR"
        " innate immune oncology OR oncolytic virotherapy OR T-VEC OR"
        " ADU-S100 OR MK-1454 OR diABZI OR SB11285 OR SNX281",
        # ── Targeted protein degradation (PROTAC / molecular glue) in oncology ──
        "PROTAC oncology OR molecular glue cancer OR targeted protein degradation cancer OR"
        " ARV-471 OR ARV-766 OR AC682 OR CC-92480 OR mezigdomide OR CFT8634 OR"
        " BRD4 degrader OR androgen receptor degrader OR ER degrader PROTAC",
        # ── Tumor microenvironment: adenosine, CD47/SIRPa, TGF-beta ──
        "CD47 SIRPalpha cancer OR adenosine pathway cancer OR CD73 inhibitor OR"
        " A2AR antagonist oncology OR magrolimab OR evorpacept OR TGF-beta cancer OR"
        " bintrafusp OR M7824 OR LY3200882",
        # ── Next-gen checkpoint: TIGIT, LAG-3, TIM-3 ──
        "TIGIT inhibitor OR tiragolumab OR vibostolimab OR domvanalimab OR"
        " LAG-3 inhibitor cancer OR relatlimab OR ieramilimab OR"
        " TIM-3 inhibitor OR cobolimab OR sabatolimab OR LY3321367",
        # ── MMP-regulated / tumor-protease-activated therapeutics ──
        # Matrix metalloproteinase (MMP-2, MMP-9, MMP-14/MT1-MMP, MMP-7) cleavage
        # sequences are overexpressed in solid tumors; drugs use them as switches
        "MMP-cleavable OR MMP-activatable OR matrix metalloproteinase prodrug OR"
        " protease-activated prodrug OR tumor protease OR MT1-MMP OR MMP-14 OR"
        " GPLGIAGQ OR PLGLAG OR gelatinase-activated OR MMP-responsive nanoparticle",
        # ── Conditionally active biologics / masked cytokines / pro-cytokines ──
        # Xilio (Pfizer), masked IL-2, masked IL-12, masked IL-18, masked IFN-gamma
        "masked cytokine OR conditional IL-2 OR pro-cytokine tumor OR"
        " masked interleukin prodrug OR XTX101 OR XTX301 OR XTX202 OR masked IL-12 OR"
        " masked IFN OR masked IL-18 OR WTX-124 OR WTX-330 OR NKTR-358 OR MDNA11",
        # ── Protein nanocages / VLPs / capsid-based drug delivery ──
        # PDB-derived structures: ferritin (1FHA), vault (2ZUO), P22 capsid,
        # Hsp16.5, lumazine synthase (I53-50), E2 pyruvate dehydrogenase
        "protein nanocage cancer OR ferritin nanoparticle drug delivery OR"
        " virus-like particle oncology OR VLP cancer therapy OR"
        " vault nanoparticle tumor OR bacteriophage capsid drug OR"
        " self-assembling protein nanoparticle cancer OR Hsp cage drug delivery OR"
        " computationally designed nanoparticle cancer",
        # ── Bicycle peptides / cyclic peptide-toxin conjugates (tumor protease) ──
        # Bicycle Therapeutics BTCs — bicyclic peptides, MMP/uPA-activated
        "bicyclic peptide cancer OR bicycle toxin conjugate OR BTC oncology OR"
        " BT-8009 OR BT-001 OR BT-6030 OR Bicycle Therapeutics OR"
        " cyclic peptide drug conjugate OR peptide-drug conjugate tumor",
        # ── Logic-gated / Boolean-sensing cell therapy and gene therapy ──
        # A2 Biotherapeutics Tmod, SynNotch logic gates, LINK platform
        "logic-gated CAR-T OR Tmod cell therapy OR SynNotch oncology OR"
        " Boolean cell therapy OR NOT-gate CAR OR antigen-sensing CAR OR"
        " A2 Biotherapeutics OR tumor-versus-normal discrimination cell therapy",
        # ── Tumor-penetrating peptides / iRGD / CendR pathway ──
        # iRGD (CRGDKGPDC) is cleaved by MMP/uPA exposing the CendR motif
        # (RXXXR) which activates neuropilin-1-mediated tumor penetration
        "tumor penetrating peptide OR iRGD cancer OR CendR pathway OR"
        " neuropilin-1 drug delivery OR CEND-1 OR tumor homing peptide OR"
        " NGR peptide drug OR RGD conjugate oncology OR CRGD nanoparticle",
        # ── DNA / RNA origami and structural nucleic acid nanostructures ──
        "DNA origami drug delivery OR RNA origami cancer OR nucleic acid nanostructure"
        " tumor OR DNA tetrahedron drug carrier OR DNA nanocage cancer therapy OR"
        " structural DNA nanotechnology oncology",
        # ── Academic structural biology translation: computationally designed proteins ──
        # David Baker lab (I53-50, I3-01, etc.), Rosetta design, AlphaFold-guided
        "computationally designed protein cancer OR de novo protein design oncology OR"
        " Rosetta drug design cancer OR AlphaFold drug target OR"
        " designed ankyrin repeat protein cancer OR DARPin oncology OR"
        " affibody cancer OR monobody cancer OR alphaBody",
        # ── Anellovirus / non-integrating capsid gene therapy ──
        # Ring Therapeutics, Flagship Pioneering platforms
        "anellovirus gene therapy OR AAV capsid engineering cancer OR"
        " Ring Therapeutics OR engineered capsid oncology OR capsid-based delivery tumor OR"
        " non-viral capsid cancer therapy",
    ]
    _PER_DISEASE_QUERY = 300

    # Major pharma sponsors — targeted kill-decision capture
    # These queries pull TERMINATED/WITHDRAWN trials from mega-pharma
    # which represent the most valuable research decision signals
    _PHARMA_SPONSORS = [
        "Pfizer",
        "Roche",
        "Novartis",
        "Merck",
        "Johnson and Johnson",
        "AbbVie",
        "Bristol-Myers Squibb",
        "AstraZeneca",
        "GlaxoSmithKline",
        "Eli Lilly",
        "Sanofi",
        "Bayer",
        "Boehringer Ingelheim",
        "Amgen",
        "Gilead Sciences",
        "Takeda",
        "Biogen",
        "Regeneron",
        "Vertex Pharmaceuticals",
        "Moderna",
    ]
    _PER_PHARMA_QUERY = 150

    def collect(self, max_records: int = 2000) -> list[RawRecord]:
        records: list[RawRecord] = []
        seen_ids: set[str] = set()

        # --- Part 1: Disease-area queries ---
        per_query = min(self._PER_DISEASE_QUERY, max_records // len(self._DISEASE_QUERIES) + 50)
        for query_term in self._DISEASE_QUERIES:
            if len(records) >= max_records:
                break
            for rec in self._fetch_query_term(query_term, per_query, sponsor_filter=None):
                if rec.source_id not in seen_ids:
                    seen_ids.add(rec.source_id)
                    records.append(rec)
                if len(records) >= max_records:
                    break

        # --- Part 2: Pharma-sponsor terminated/withdrawn queries ---
        # Focus on TERMINATED = kill decisions (most valuable for training)
        pharma_budget = max_records - len(records)
        per_pharma = min(self._PER_PHARMA_QUERY, pharma_budget // max(len(self._PHARMA_SPONSORS), 1) + 20)

        for sponsor in self._PHARMA_SPONSORS:
            if len(records) >= max_records:
                break
            for rec in self._fetch_query_term(
                query_term=None,
                target=per_pharma,
                sponsor_filter=sponsor,
                status_filter="TERMINATED,WITHDRAWN,SUSPENDED",
            ):
                if rec.source_id not in seen_ids:
                    seen_ids.add(rec.source_id)
                    records.append(rec)
                if len(records) >= max_records:
                    break

        logger.info("ClinicalTrials: collected %d records", len(records))
        return records[:max_records]

    def _fetch_query_term(
        self,
        query_term: str | None,
        target: int,
        sponsor_filter: str | None = None,
        status_filter: str = "TERMINATED,COMPLETED,WITHDRAWN,SUSPENDED,ACTIVE_NOT_RECRUITING,RECRUITING",
    ) -> list[RawRecord]:
        records: list[RawRecord] = []
        next_page_token: str | None = None

        while len(records) < target:
            batch_size = min(100, target - len(records))
            params: dict[str, Any] = {
                "filter.overallStatus": status_filter,
                "pageSize": batch_size,
                "format": "json",
            }
            if query_term:
                params["query.term"] = query_term
                params["query.spons"] = "INDUSTRY"
            if sponsor_filter:
                params["query.spons"] = sponsor_filter
            if next_page_token:
                params["pageToken"] = next_page_token

            try:
                resp = self._get(_CT_API, params=params, accept_json=True)
                data = resp.json()
            except Exception as exc:
                logger.error("ClinicalTrials fetch error: %s", exc)
                break

            studies = data.get("studies", [])
            if not studies:
                break

            for study in studies:
                record = self._parse_study(study)
                if record:
                    records.append(record)

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        return records

    def _parse_study(self, study: dict[str, Any]) -> RawRecord | None:
        proto = study.get("protocolSection", {})
        id_mod = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design_mod = proto.get("designModule", {})
        cond_mod = proto.get("conditionsModule", {})
        interv_mod = proto.get("interventionsModule", {})
        desc_mod = proto.get("descriptionModule", {})

        nct_id = id_mod.get("nctId", "")
        if not nct_id:
            return None

        phases = design_mod.get("phases", ["NA"])
        phase_raw = phases[0] if phases else "NA"
        stage = _PHASE_MAP.get(phase_raw, "phase1")

        status_raw = status_mod.get("overallStatus", "")
        outcome = _STATUS_MAP.get(status_raw, "unknown")

        # Determine outcome with stage context (TERMINATED in Phase3 = discontinued_p3, etc.)
        if status_raw == "TERMINATED":
            outcome = f"discontinued_{stage}"

        conditions = cond_mod.get("conditions", [])
        indication = conditions[0] if conditions else "unknown"

        interventions = interv_mod.get("interventions", [])
        mechanism = interventions[0].get("name", "unknown") if interventions else "unknown"

        brief = desc_mod.get("briefSummary", "")
        title = id_mod.get("briefTitle", "")

        # Decision: TERMINATED = no-go, COMPLETED = go (moved forward or ended planned)
        decision = "no-go" if status_raw in ("TERMINATED", "WITHDRAWN", "SUSPENDED") else "go"

        return RawRecord(
            source=self.name,
            source_id=nct_id,
            url=f"https://clinicaltrials.gov/study/{nct_id}",
            title=title,
            indication=indication,
            mechanism=mechanism,
            clinical_stage=stage,
            decision=decision,
            outcome=outcome,
            investment_usd=0.0,
            raw_text=f"{title}. {brief}",
            extra={
                "sponsor": _first_sponsor(study),
                "status_raw": status_raw,
                "phase_raw": phase_raw,
            },
        )
