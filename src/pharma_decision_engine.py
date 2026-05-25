from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Decision(str, Enum):
    KILL = "KILL"
    CONTINUE = "CONTINUE"
    INVEST = "INVEST"


@dataclass(frozen=True)
class Evidence:
    """A single experimental outcome represented as a Bayes factor."""

    name: str
    bayes_factor: float
    run_cost: float = 0.0
    notes: str = ""


@dataclass(frozen=True)
class ProjectState:
    """State carried between decision steps."""

    hypothesis: str
    probability: float
    assumptions: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    cumulative_cost: float = 0.0


@dataclass(frozen=True)
class DecisionPolicy:
    kill_threshold: float = 0.20
    invest_threshold: float = 0.70
    min_expected_value: float = 0.0


@dataclass(frozen=True)
class DecisionLogEntry:
    timestamp: str
    step: int
    decision: Decision
    probability_before: float
    probability_after: float
    expected_value: float
    cumulative_cost: float
    evidence_name: str
    evidence_notes: str


@dataclass
class DecisionEngine:
    """Decision engine inspired by Coefficient Bio-style process design."""

    payoff_if_success: float
    policy: DecisionPolicy = field(default_factory=DecisionPolicy)

    @staticmethod
    def _clamp_probability(value: float) -> float:
        return min(max(value, 1e-9), 1 - 1e-9)

    @classmethod
    def bayesian_update(cls, prior: float, bayes_factor: float) -> float:
        """
        Update P(H) from prior odds and Bayes factor:
            posterior_odds = prior_odds * bayes_factor
        """
        if bayes_factor <= 0:
            raise ValueError("bayes_factor must be > 0")

        prior = cls._clamp_probability(prior)
        prior_odds = prior / (1 - prior)
        posterior_odds = prior_odds * bayes_factor
        posterior = posterior_odds / (1 + posterior_odds)
        return cls._clamp_probability(posterior)

    def expected_value(self, p_success: float, cumulative_cost: float) -> float:
        return p_success * self.payoff_if_success - cumulative_cost

    def decide(self, p_success: float, expected_value: float) -> Decision:
        if p_success < self.policy.kill_threshold or expected_value < self.policy.min_expected_value:
            return Decision.KILL
        if p_success > self.policy.invest_threshold and expected_value >= self.policy.min_expected_value:
            return Decision.INVEST
        return Decision.CONTINUE

    def step(self, state: ProjectState, new_evidence: Evidence, step: int) -> tuple[ProjectState, Decision, DecisionLogEntry]:
        p_before = state.probability
        p_after = self.bayesian_update(p_before, new_evidence.bayes_factor)
        next_cost = state.cumulative_cost + new_evidence.run_cost
        ev = self.expected_value(p_after, next_cost)
        decision = self.decide(p_after, ev)

        next_state = ProjectState(
            hypothesis=state.hypothesis,
            probability=p_after,
            assumptions=state.assumptions,
            evidence=(*state.evidence, new_evidence),
            cumulative_cost=next_cost,
        )

        log_entry = DecisionLogEntry(
            timestamp=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            step=step,
            decision=decision,
            probability_before=p_before,
            probability_after=p_after,
            expected_value=ev,
            cumulative_cost=next_cost,
            evidence_name=new_evidence.name,
            evidence_notes=new_evidence.notes,
        )
        return next_state, decision, log_entry


def run_project(
    initial_state: ProjectState,
    evidences: list[Evidence],
    engine: DecisionEngine,
) -> tuple[ProjectState, list[DecisionLogEntry]]:
    state = initial_state
    logs: list[DecisionLogEntry] = []

    for idx, ev in enumerate(evidences, start=1):
        state, decision, log = engine.step(state=state, new_evidence=ev, step=idx)
        logs.append(log)

        if decision in (Decision.KILL, Decision.INVEST):
            break

    return state, logs


def to_dict(log: DecisionLogEntry) -> dict[str, Any]:
    return {
        "timestamp": log.timestamp,
        "step": log.step,
        "decision": log.decision.value,
        "p_before": round(log.probability_before, 4),
        "p_after": round(log.probability_after, 4),
        "expected_value": round(log.expected_value, 2),
        "cumulative_cost": round(log.cumulative_cost, 2),
        "evidence": log.evidence_name,
        "notes": log.evidence_notes,
    }


def demo() -> None:
    initial = ProjectState(
        hypothesis="Drug X achieves clinically meaningful efficacy",
        probability=0.35,
        assumptions=(
            "Mechanism translates from preclinical model",
            "Safety profile remains manageable in dose escalation",
        ),
    )

    experiments = [
        Evidence("Biomarker signal", bayes_factor=1.8, run_cost=1_000_000, notes="Stronger than baseline"),
        Evidence("Dose expansion", bayes_factor=0.7, run_cost=2_500_000, notes="Mixed response by cohort"),
        Evidence("Interim efficacy", bayes_factor=2.2, run_cost=5_000_000, notes="Primary endpoint trend positive"),
    ]

    engine = DecisionEngine(
        payoff_if_success=25_000_000,
        policy=DecisionPolicy(kill_threshold=0.18, invest_threshold=0.72, min_expected_value=0),
    )

    final_state, logs = run_project(initial_state=initial, evidences=experiments, engine=engine)

    print("=== Decision Log ===")
    for item in logs:
        print(to_dict(item))

    if logs:
        print("\nFinal decision:", logs[-1].decision.value)
    print("Final probability:", round(final_state.probability, 4))
    print("Final cumulative cost:", f"${final_state.cumulative_cost:,.0f}")


if __name__ == "__main__":
    demo()
