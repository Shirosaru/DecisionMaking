"""
Review Board: coordinates specialist agents and synthesizes analyses for human decision.

The board's job is NOT to decide, but to surface multiple perspectives
and highlight where the experts align or diverge.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from .decision_agents import (
    AgentRole,
    AgentAnalysis,
    ClinicalAgent,
    FinancialAgent,
    RiskOfficer,
)


class DissensusPattern(str, Enum):
    """How do the three agents agree or disagree?"""
    ALIGNED = "aligned"  # All 3 rec same decision
    SPLIT = "split"  # 2 vs 1
    CONFLICTED = "conflicted"  # All different


@dataclass(frozen=True)
class DecisionBrief:
    """Human-readable briefing for decision maker."""

    project_id: str
    hypothesis: str
    
    # Agent analyses
    clinical_analysis: AgentAnalysis
    financial_analysis: AgentAnalysis
    risk_analysis: AgentAnalysis
    
    # Synthesis
    pattern: DissensusPattern
    consensus_recommendation: str  # or "MIXED"
    confidence_score: float  # avg of 3 agents
    
    # Key tensions (if split/conflicted)
    tensions: list[str]
    
    # Aligned questions (things all 3 agents flagged)
    critical_unknowns: list[str]
    
    # Next step suggestion
    suggested_next_action: str


class ReviewBoard:
    """Coordinates parallel agent analysis and synthesizes briefs."""

    def __init__(self):
        self.clinical = ClinicalAgent()
        self.financial = FinancialAgent()
        self.risk = RiskOfficer()

    def analyze_project(
        self,
        project_id: str,
        hypothesis: str,
        project_context: dict[str, Any],
        parallel: bool = True,
    ) -> DecisionBrief:
        """
        Run all three agents and synthesize result.
        
        Args:
            project_id: Like "PROJ-001"
            hypothesis: Project hypothesis/description
            project_context: Dict with project details (hypothesis, probability, evidence, etc.)
            parallel: If True, run agents concurrently; if False, sequentially
        
        Returns:
            DecisionBrief ready for human review
        """
        if parallel:
            analyses = self._analyze_parallel(project_context)
        else:
            analyses = self._analyze_sequential(project_context)

        return self._synthesize_brief(
            project_id=project_id,
            hypothesis=hypothesis,
            analyses=analyses,
        )

    def _analyze_parallel(self, project_context: dict[str, Any]) -> dict[AgentRole, AgentAnalysis]:
        """Run all agents concurrently."""
        analyses = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.clinical.analyze, project_context): AgentRole.CLINICAL,
                executor.submit(self.financial.analyze, project_context): AgentRole.FINANCIAL,
                executor.submit(self.risk.analyze, project_context): AgentRole.RISK,
            }
            for future in as_completed(futures):
                role = futures[future]
                analyses[role] = future.result()
        return analyses

    def _analyze_sequential(self, project_context: dict[str, Any]) -> dict[AgentRole, AgentAnalysis]:
        """Run agents one by one."""
        return {
            AgentRole.CLINICAL: self.clinical.analyze(project_context),
            AgentRole.FINANCIAL: self.financial.analyze(project_context),
            AgentRole.RISK: self.risk.analyze(project_context),
        }

    def _synthesize_brief(
        self,
        project_id: str,
        hypothesis: str,
        analyses: dict[AgentRole, AgentAnalysis],
    ) -> DecisionBrief:
        """Combine three analyses into a decision brief."""
        clinical = analyses[AgentRole.CLINICAL]
        financial = analyses[AgentRole.FINANCIAL]
        risk = analyses[AgentRole.RISK]

        # Determine alignment pattern
        recommendations = [clinical.recommendation, financial.recommendation, risk.recommendation]
        pattern = self._classify_pattern(recommendations)

        # Consensus recommendation
        consensus = self._compute_consensus(recommendations, pattern)

        # Average confidence
        avg_confidence = (clinical.confidence + financial.confidence + risk.confidence) / 3

        # Extract tensions (disagreements)
        tensions = self._extract_tensions(analyses, pattern)

        # Find commonly flagged concerns (unknowns)
        critical_unknowns = self._find_critical_unknowns(analyses)

        # Next action
        next_action = self._suggest_next_action(pattern, analyses)

        return DecisionBrief(
            project_id=project_id,
            hypothesis=hypothesis,
            clinical_analysis=clinical,
            financial_analysis=financial,
            risk_analysis=risk,
            pattern=pattern,
            consensus_recommendation=consensus,
            confidence_score=avg_confidence,
            tensions=tensions,
            critical_unknowns=critical_unknowns,
            suggested_next_action=next_action,
        )

    @staticmethod
    def _classify_pattern(recommendations: list[str]) -> DissensusPattern:
        """Classify agreement pattern."""
        unique = len(set(recommendations))
        if unique == 1:
            return DissensusPattern.ALIGNED
        elif unique == 2:
            return DissensusPattern.SPLIT
        else:
            return DissensusPattern.CONFLICTED

    @staticmethod
    def _compute_consensus(recommendations: list[str], pattern: DissensusPattern) -> str:
        """Determine consensus recommendation."""
        if pattern == DissensusPattern.ALIGNED:
            return recommendations[0]
        elif pattern == DissensusPattern.SPLIT:
            # Majority wins
            from collections import Counter
            counts = Counter(recommendations)
            return counts.most_common(1)[0][0]
        else:
            # All different
            return "MIXED"

    @staticmethod
    def _extract_tensions(
        analyses: dict[AgentRole, AgentAnalysis],
        pattern: DissensusPattern,
    ) -> list[str]:
        """List disagreements between agents."""
        if pattern == DissensusPattern.ALIGNED:
            return []

        tensions = []
        roles = list(analyses.keys())
        for i, role1 in enumerate(roles):
            for role2 in roles[i + 1 :]:
                ana1 = analyses[role1]
                ana2 = analyses[role2]
                if ana1.recommendation != ana2.recommendation:
                    tensions.append(
                        f"{role1.value} says {ana1.recommendation}, "
                        f"{role2.value} says {ana2.recommendation}"
                    )
        return tensions

    @staticmethod
    def _find_critical_unknowns(analyses: dict[AgentRole, AgentAnalysis]) -> list[str]:
        """Surface questions that multiple agents flagged."""
        unknowns_by_agent = {}
        for role, analysis in analyses.items():
            unknowns_by_agent[role] = set(analysis.next_critical_question)

        # For now, just collect all critical questions
        # In a real system, could do NLP to find semantic duplicates
        all_questions = [
            f"[{role.value}] {analysis.next_critical_question}"
            for role, analysis in analyses.items()
        ]
        return all_questions

    @staticmethod
    def _suggest_next_action(
        pattern: DissensusPattern,
        analyses: dict[AgentRole, AgentAnalysis],
    ) -> str:
        """Suggest what to do next based on alignment."""
        if pattern == DissensusPattern.ALIGNED:
            consensus = analyses[AgentRole.CLINICAL].recommendation
            if consensus == "INVEST":
                return "All experts agree: move to term sheet negotiation."
            elif consensus == "KILL":
                return "All experts agree: close project. Consider why opportunity was missed at screening."
            else:
                return "All experts recommend continued due diligence. Schedule deep dives on critical questions."
        elif pattern == DissensusPattern.SPLIT:
            return "Expert split detected. Schedule working session to reconcile assumptions between camps."
        else:
            return "No clear consensus. Need additional data or reframe assumptions. Consider external expert panel."
