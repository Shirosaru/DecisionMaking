"""
Claude API integration for agent analysis.

Each agent calls Claude with its specific system prompt and project context,
getting back structured analysis.
"""

from __future__ import annotations

import os
import json
from typing import Any

# Import after checking for anthropic library
try:
    import anthropic
except ImportError:
    anthropic = None


def get_claude_client() -> anthropic.Anthropic | None:
    """Initialize Claude client if API key is available."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


def format_project_context(project_context: dict[str, Any]) -> str:
    """Convert project context to readable prompt."""
    lines = [
        f"Project Hypothesis: {project_context.get('hypothesis', 'N/A')}",
        f"Prior Success Probability: {project_context.get('prior_probability', 0.5):.0%}",
    ]
    
    if project_context.get("assumptions"):
        lines.append("Key Assumptions:")
        for assumption in project_context["assumptions"]:
            lines.append(f"  • {assumption}")
    
    if project_context.get("evidences"):
        lines.append("Current Evidence:")
        for evidence in project_context["evidences"]:
            lines.append(f"  • {evidence.get('name', 'unnamed')}: BF={evidence.get('bayes_factor', '?')}")
    
    if project_context.get("payoff"):
        lines.append(f"Estimated Payoff on Success: ${project_context['payoff']:,.0f}")
    
    return "\n".join(lines)


def call_claude_agent(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
) -> dict[str, Any] | None:
    """
    Call Claude with a system + user prompt.
    
    Returns:
        Dict with keys: recommendation, confidence, reasoning, concerns, next_question
        Or None if API call fails/unavailable.
    """
    client = get_claude_client()
    if not client:
        return None
    
    try:
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )
        
        # Parse response
        response_text = message.content[0].text if message.content else ""
        
        # Try to extract structured response
        # Claude should return something like:
        # RECOMMENDATION: INVEST
        # CONFIDENCE: 75%
        # KEY_REASONING: ...
        # CONCERNS: ...
        # NEXT_QUESTION: ...
        
        lines = response_text.split("\n")
        result = {}
        current_key = None
        current_value = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if line.startswith("RECOMMENDATION:"):
                if current_key:
                    result[current_key] = "\n".join(current_value).strip()
                current_key = "recommendation"
                current_value = [line.replace("RECOMMENDATION:", "").strip()]
            elif line.startswith("CONFIDENCE:"):
                if current_key:
                    result[current_key] = "\n".join(current_value).strip()
                current_key = "confidence"
                current_value = [line.replace("CONFIDENCE:", "").strip()]
            elif line.startswith("KEY_REASONING:"):
                if current_key:
                    result[current_key] = "\n".join(current_value).strip()
                current_key = "reasoning"
                current_value = [line.replace("KEY_REASONING:", "").strip()]
            elif line.startswith("CONCERNS:") or line.startswith("CONCERNS/RISKS:"):
                if current_key:
                    result[current_key] = "\n".join(current_value).strip()
                current_key = "concerns"
                current_value = [line.replace("CONCERNS:", "").replace("CONCERNS/RISKS:", "").strip()]
            elif line.startswith("NEXT_QUESTION:") or line.startswith("NEXT_CRITICAL_QUESTION:"):
                if current_key:
                    result[current_key] = "\n".join(current_value).strip()
                current_key = "next_question"
                current_value = [line.replace("NEXT_QUESTION:", "").replace("NEXT_CRITICAL_QUESTION:", "").strip()]
            else:
                if current_key and current_value:  # If we're in a section
                    current_value.append(line)
        
        # Finalize last section
        if current_key:
            result[current_key] = "\n".join(current_value).strip()
        
        return result
    
    except Exception as e:
        print(f"⚠ Claude API error: {e}")
        return None


def _parse_confidence(conf_str: str) -> float:
    """Extract confidence percentage from string like '75%' -> 0.75"""
    conf_str = conf_str.strip().rstrip("%").strip()
    try:
        return float(conf_str) / 100
    except:
        return 0.5


def _parse_bullet_list(text: str) -> list[str]:
    """Parse bullet-pointed list."""
    lines = text.split("\n")
    items = []
    for line in lines:
        line = line.strip()
        if line.startswith("•") or line.startswith("-") or line.startswith("*"):
            item = line.lstrip("•-* ").strip()
            if item:
                items.append(item)
        elif line and items:  # Continuation of previous item
            items[-1] += " " + line
    return items or ["No specific concerns noted"]
