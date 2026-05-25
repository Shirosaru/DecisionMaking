"""
Execute multi-agent review board analysis and log human decision.

This is the next-level workflow:
1. Load project
2. Run 3 agents in parallel
3. Present brief to human
4. Log human decision + reasoning
5. Compare decision vs agent recommendation for learning
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.pharma_decision_engine import ProjectState, Evidence, DecisionEngine
from src.review_board import ReviewBoard, DecisionBrief


@dataclass(frozen=True)
class HumanDecisionLog:
    """Record of what human decided vs what agents recommended."""
    timestamp: str
    project_id: str
    agent_consensus: str
    agent_confidence: float
    agent_tensions: list[str]
    
    human_decision: str
    human_reasoning: str
    
    aligned: bool  # True if human aligned with consensus
    override_reason: str | None  # Why human overruled agents (if applicable)


def format_brief_for_review(brief: DecisionBrief) -> str:
    """Pretty-print a brief for human review."""
    lines = [
        "=" * 70,
        f"DECISION BRIEF: {brief.project_id}",
        "=" * 70,
        "",
        f"Hypothesis: {brief.hypothesis}",
        "",
        "─" * 70,
        "AGENT ANALYSIS",
        "─" * 70,
        "",
    ]

    # Clinical
    lines.append(f"🔬 CLINICAL LEAD")
    lines.append(f"   Recommendation: {brief.clinical_analysis.recommendation} (confidence: {brief.clinical_analysis.confidence:.0%})")
    lines.append(f"   Key reasoning:")
    for point in brief.clinical_analysis.key_reasoning:
        lines.append(f"     • {point}")
    lines.append(f"   Concerns:")
    for concern in brief.clinical_analysis.risks_or_concerns:
        lines.append(f"     ⚠ {concern}")
    lines.append(f"   Next question: {brief.clinical_analysis.next_critical_question}")
    lines.append("")

    # Financial
    lines.append(f"💰 CFO / FINANCIAL PARTNER")
    lines.append(f"   Recommendation: {brief.financial_analysis.recommendation} (confidence: {brief.financial_analysis.confidence:.0%})")
    lines.append(f"   Key reasoning:")
    for point in brief.financial_analysis.key_reasoning:
        lines.append(f"     • {point}")
    lines.append(f"   Concerns:")
    for concern in brief.financial_analysis.risks_or_concerns:
        lines.append(f"     ⚠ {concern}")
    lines.append(f"   Next question: {brief.financial_analysis.next_critical_question}")
    lines.append("")

    # Risk
    lines.append(f"⚡ RISK OFFICER")
    lines.append(f"   Recommendation: {brief.risk_analysis.recommendation} (confidence: {brief.risk_analysis.confidence:.0%})")
    lines.append(f"   Key reasoning:")
    for point in brief.risk_analysis.key_reasoning:
        lines.append(f"     • {point}")
    lines.append(f"   Concerns:")
    for concern in brief.risk_analysis.risks_or_concerns:
        lines.append(f"     ⚠ {concern}")
    lines.append(f"   Next question: {brief.risk_analysis.next_critical_question}")
    lines.append("")

    # Synthesis
    lines.append("─" * 70)
    lines.append("SYNTHESIS")
    lines.append("─" * 70)
    lines.append(f"Pattern: {brief.pattern.value.upper()}")
    lines.append(f"Consensus: {brief.consensus_recommendation}")
    lines.append(f"Avg Confidence: {brief.confidence_score:.0%}")
    lines.append("")
    
    if brief.tensions:
        lines.append("Tensions:")
        for tension in brief.tensions:
            lines.append(f"  ⚔ {tension}")
        lines.append("")

    lines.append("Critical Unknowns:")
    for unknown in brief.critical_unknowns:
        lines.append(f"  ❓ {unknown}")
    lines.append("")

    lines.append(f"Suggested Next Action: {brief.suggested_next_action}")
    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


def interactive_decision_flow(brief: DecisionBrief, auto_align: bool = False) -> HumanDecisionLog:
    """Guide user through decision with the brief.
    
    Args:
        brief: The decision brief from review board
        auto_align: If True, auto-align with agent consensus (for testing)
    """
    print(format_brief_for_review(brief))
    print("\n")
    print("Your turn as Decision Maker.")
    print(f"Agent consensus: {brief.consensus_recommendation} (Confidence: {brief.confidence_score:.0%})")
    print("")
    
    if auto_align:
        # For testing/demo: automatically align with consensus
        decision = brief.consensus_recommendation
        reasoning = f"Aligned with agent consensus ({brief.pattern.value})"
        override_reason = None
    else:
        decision = input("Your decision (KILL/CONTINUE/INVEST): ").strip().upper()
        while decision not in ("KILL", "CONTINUE", "INVEST"):
            decision = input("Invalid. Try again (KILL/CONTINUE/INVEST): ").strip().upper()
        
        reasoning = input("Your reasoning (one sentence): ").strip()
        
        override_reason = None
        if decision != brief.consensus_recommendation:
            override_reason = input("Why do you override the agent consensus? ").strip()
    
    aligned = decision == brief.consensus_recommendation
    
    return HumanDecisionLog(
        timestamp=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        project_id=brief.project_id,
        agent_consensus=brief.consensus_recommendation,
        agent_confidence=brief.confidence_score,
        agent_tensions=brief.tensions,
        human_decision=decision,
        human_reasoning=reasoning,
        aligned=aligned,
        override_reason=override_reason,
    )


def save_decision_log(log: HumanDecisionLog, log_file: Path = _ROOT / "data" / "logs" / "decision_log.jsonl") -> None:
    """Append decision to log file for future learning."""
    log_dict = {
        "timestamp": log.timestamp,
        "project_id": log.project_id,
        "agent_consensus": log.agent_consensus,
        "agent_confidence": round(log.agent_confidence, 3),
        "human_decision": log.human_decision,
        "human_reasoning": log.human_reasoning,
        "aligned": log.aligned,
        "override_reason": log.override_reason,
        "tensions_count": len(log.agent_tensions),
    }
    with log_file.open("a") as f:
        f.write(json.dumps(log_dict) + "\n")
    print(f"\n✓ Decision logged to {log_file}")


def main() -> None:
    """Demo: run review board on a synthetic project."""
    # Load synthetic data
    dataset_path = _ROOT / "data" / "synthetic_poc.json"
    with dataset_path.open() as f:
        data = json.load(f)
    
    projects = data.get("projects", [])
    if not projects:
        print("No projects found in synthetic data.")
        return
    
    # Just use first project for demo
    project = projects[0]
    
    # Convert to ProjectState for context
    state = ProjectState(
        hypothesis=project["hypothesis"],
        probability=float(project["prior_probability"]),
        assumptions=tuple(project.get("assumptions", [])),
    )
    
    # Build project context dict
    project_context = {
        "hypothesis": project["hypothesis"],
        "prior_probability": state.probability,
        "assumptions": project.get("assumptions", []),
        "evidences": project.get("evidences", []),
        "payoff": float(project.get("payoff_if_success", 100_000_000)),
    }
    
    # Run review board
    board = ReviewBoard()
    brief = board.analyze_project(
        project_id=project["project_id"],
        hypothesis=project["hypothesis"],
        project_context=project_context,
        parallel=True,
    )
    
    # Interactive decision (use auto_align=True for demo/testing)
    log = interactive_decision_flow(brief, auto_align=True)
    save_decision_log(log)
    
    # Summary
    print("\n")
    print("─" * 70)
    print("DECISION ACCEPTED")
    print("─" * 70)
    print(f"You voted: {log.human_decision}")
    print(f"Agents recommended: {log.agent_consensus}")
    print(f"Aligned: {'YES ✓' if log.aligned else 'NO ⚠ (override logged)'}")


if __name__ == "__main__":
    main()
