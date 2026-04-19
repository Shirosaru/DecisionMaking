from __future__ import annotations

import json
from pathlib import Path

from pharma_decision_engine import (
    DecisionEngine,
    DecisionPolicy,
    Evidence,
    ProjectState,
    run_project,
    to_dict,
)


def load_projects(dataset_path: Path) -> list[dict]:
    with dataset_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("projects", [])


def run_one_project(project: dict) -> None:
    policy_data = project.get("policy", {})
    engine = DecisionEngine(
        payoff_if_success=float(project["payoff_if_success"]),
        policy=DecisionPolicy(
            kill_threshold=float(policy_data.get("kill_threshold", 0.2)),
            invest_threshold=float(policy_data.get("invest_threshold", 0.7)),
            min_expected_value=float(policy_data.get("min_expected_value", 0)),
        ),
    )

    initial = ProjectState(
        hypothesis=project["hypothesis"],
        probability=float(project["prior_probability"]),
        assumptions=tuple(project.get("assumptions", [])),
    )

    evidences = [
        Evidence(
            name=item["name"],
            bayes_factor=float(item["bayes_factor"]),
            run_cost=float(item.get("run_cost", 0)),
            notes=item.get("notes", ""),
        )
        for item in project.get("evidences", [])
    ]

    final_state, logs = run_project(initial_state=initial, evidences=evidences, engine=engine)

    print(f"\n=== {project['project_id']} ===")
    print("Hypothesis:", project["hypothesis"])
    for log in logs:
        print(to_dict(log))

    final_decision = logs[-1].decision.value if logs else "NO_DATA"
    print("Final decision:", final_decision)
    print("Final probability:", round(final_state.probability, 4))
    print("Final cumulative cost:", f"${final_state.cumulative_cost:,.0f}")


def main() -> None:
    dataset_path = Path("data/synthetic_poc.json")
    projects = load_projects(dataset_path)

    print("Loaded synthetic projects:", len(projects))
    for project in projects:
        run_one_project(project)


if __name__ == "__main__":
    main()
