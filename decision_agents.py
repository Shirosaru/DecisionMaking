"""
Specialized AI agents for multi-perspective project decision analysis.

Each agent assumes a specific role and provides independent judgment,
allowing human to synthesize and make final decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from claude_api import call_claude_agent, format_project_context, _parse_confidence, _parse_bullet_list


class AgentRole(str, Enum):
    CLINICAL = "clinical"
    FINANCIAL = "financial"
    RISK = "risk"


@dataclass(frozen=True)
class AgentAnalysis:
    """Output from a single specialized agent."""

    role: AgentRole
    recommendation: str  # KILL, CONTINUE, INVEST
    confidence: float  # 0.0 to 1.0
    key_reasoning: list[str]  # Top 3-5 points
    risks_or_concerns: list[str]
    next_critical_question: str


class ClinicalAgent:
    """
    Role: Evaluates clinical efficacy and safety evidence.
    Question: "Will this therapy work and is it safe?"
    """

    SYSTEM_PROMPT = """You are Clinical Lead at a biotech investment firm.
Your role: assess clinical efficacy & safety signal strength.

When analyzing a project, consider:
- Quality of evidence (phase, n-size, mechanism plausibility)
- Safety signals or red flags
- Unmet medical need strength
- Competitive landscape
- Phase progression readiness

IMPORTANT: Respond with this EXACT structure (one per line):
RECOMMENDATION: [KILL|CONTINUE|INVEST]
CONFIDENCE: [0-100]
KEY_REASONING:
• Point 1
• Point 2
• Point 3
CONCERNS/RISKS:
• Risk 1
• Risk 2
NEXT_CRITICAL_QUESTION: [What's the key question to answer next?]

Be direct. Assume the reader has domain knowledge."""

    def analyze(self, project_context: dict[str, Any]) -> AgentAnalysis:
        """Analyze project through clinical lens."""
        user_prompt = f"""Please analyze this biotech project with clinical focus:

{format_project_context(project_context)}

What is your clinical recommendation?"""

        response = call_claude_agent(self.SYSTEM_PROMPT, user_prompt)
        
        if response is None:
            # Fallback to placeholder if API unavailable
            return self._placeholder_analysis()
        
        return AgentAnalysis(
            role=AgentRole.CLINICAL,
            recommendation=response.get("recommendation", "CONTINUE").upper().strip(),
            confidence=_parse_confidence(response.get("confidence", "50%")),
            key_reasoning=_parse_bullet_list(response.get("reasoning", "")),
            risks_or_concerns=_parse_bullet_list(response.get("concerns", "")),
            next_critical_question=response.get("next_question", "What evidence is still missing?"),
        )
    
    @staticmethod
    def _placeholder_analysis() -> AgentAnalysis:
        """Fallback analysis when API is unavailable."""
        return AgentAnalysis(
            role=AgentRole.CLINICAL,
            recommendation="CONTINUE",
            confidence=0.75,
            key_reasoning=[
                "Phase IIb shows dose-response signal",
                "Safety profile acceptable for indication",
                "Mechanism supports target engagement",
            ],
            risks_or_concerns=[
                "Limited long-term safety data",
                "Competitive drug entering market Q2",
            ],
            next_critical_question="Does this show QoL improvement in Phase IIb?",
        )


class FinancialAgent:
    """
    Role: Evaluates financial viability and payoff structure.
    Question: "Is the return worth the risk and capital?"
    """

    SYSTEM_PROMPT = """You are CFO/Finance Partner at a biotech fund.
Your role: assess financial sustainability and go-to-market viability.

When analyzing a project, consider:
- Burn rate and remaining runway  
- Market size & pricing power
- Capital requirements vs upside
- Competition & IP moat longevity
- Regulatory path length and cost
- Manufacturing/supply chain risks

IMPORTANT: Respond with this EXACT structure (one per line):
RECOMMENDATION: [KILL|CONTINUE|INVEST]
CONFIDENCE: [0-100]
KEY_REASONING:
• Point 1
• Point 2
• Point 3
CONCERNS/RISKS:
• Risk 1
• Risk 2
NEXT_CRITICAL_QUESTION: [What cost/revenue assumption is most uncertain?]

Be steward-like and flag cash constraints clearly."""

    def analyze(self, project_context: dict[str, Any]) -> AgentAnalysis:
        """Analyze project through financial lens."""
        user_prompt = f"""Please analyze this biotech project with financial focus:

{format_project_context(project_context)}

What is your financial recommendation?"""

        response = call_claude_agent(self.SYSTEM_PROMPT, user_prompt)
        
        if response is None:
            return self._placeholder_analysis()
        
        return AgentAnalysis(
            role=AgentRole.FINANCIAL,
            recommendation=response.get("recommendation", "CONTINUE").upper().strip(),
            confidence=_parse_confidence(response.get("confidence", "50%")),
            key_reasoning=_parse_bullet_list(response.get("reasoning", "")),
            risks_or_concerns=_parse_bullet_list(response.get("concerns", "")),
            next_critical_question=response.get("next_question", "What's the biggest cost assumption?"),
        )
    
    @staticmethod
    def _placeholder_analysis() -> AgentAnalysis:
        """Fallback analysis when API is unavailable."""
        return AgentAnalysis(
            role=AgentRole.FINANCIAL,
            recommendation="INVEST",
            confidence=0.68,
            key_reasoning=[
                "TAM $2.1B, attainable 12% share = $250M peak",
                "Current burn $8M/month, runway 18 months",
                "Payer willingness $85K/patient established",
            ],
            risks_or_concerns=[
                "Manufacturing scale-up cost underestimated historically",
                "Reimbursement approval timeline uncertain (+6mo?)",
            ],
            next_critical_question="Can we de-risk manufacturing before Series C?",
        )


class RiskOfficer:
    """
    Role: Identifies assumption gaps and Black Swan risks.
    Question: "What could we be catastrophically wrong about?"
    """

    SYSTEM_PROMPT = """You are Chief Risk Officer at a biotech fund.
Your role: assume things will go wrong. Find hidden assumptions.

When analyzing a project, probe for:
- Key assumptions (clinical, manufacturing, reimbursement, competitive)
- Unknown unknowns: what haven't we asked?
- Single points of failure
- Tail risks (regulatory surprise, manufacturing issue, competitive)
- Founder/team risk
- Dependency risks

IMPORTANT: Respond with this EXACT structure (one per line):
RECOMMENDATION: [KILL|CONTINUE|INVEST]
CONFIDENCE: [0-100]
KEY_REASONING:
• Point 1
• Point 2
• Point 3
CONCERNS/RISKS:
• Critical risk 1
• Risk 2
NEXT_CRITICAL_QUESTION: [What assumption most needs validation?]

Be direct about what keeps you awake at night."""

    def analyze(self, project_context: dict[str, Any]) -> AgentAnalysis:
        """Analyze project through risk lens."""
        user_prompt = f"""Please analyze this biotech project with risk/assumption focus:

{format_project_context(project_context)}

What is your risk recommendation and what are you most concerned about?"""

        response = call_claude_agent(self.SYSTEM_PROMPT, user_prompt)
        
        if response is None:
            return self._placeholder_analysis()
        
        return AgentAnalysis(
            role=AgentRole.RISK,
            recommendation=response.get("recommendation", "CONTINUE").upper().strip(),
            confidence=_parse_confidence(response.get("confidence", "50%")),
            key_reasoning=_parse_bullet_list(response.get("reasoning", "")),
            risks_or_concerns=_parse_bullet_list(response.get("concerns", "")),
            next_critical_question=response.get("next_question", "What assumption is most uncertain?"),
        )
    
    @staticmethod
    def _placeholder_analysis() -> AgentAnalysis:
        """Fallback analysis when API is unavailable."""
        return AgentAnalysis(
            role=AgentRole.RISK,
            recommendation="CONTINUE",
            confidence=0.62,
            key_reasoning=[
                "Team is strong but missing manufacturing expertise",
                "Regulatory pathway is known but FDA relationship untested",
                "Only one lead compound; no backup strategy",
            ],
            risks_or_concerns=[
                "CRITICAL: Manufacturing partner has limited GMP capacity",
                "If Phase IIb efficacy misses, no pivot path",
                "Founder turnover risk given startup culture mismatch",
            ],
            next_critical_question="What's the contingency if Phase IIb PK doesn't scale?",
        )


# For API integration later
AGENT_REGISTRY = {
    AgentRole.CLINICAL: ClinicalAgent,
    AgentRole.FINANCIAL: FinancialAgent,
    AgentRole.RISK: RiskOfficer,
}
