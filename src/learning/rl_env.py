from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

# ── Action space ──────────────────────────────────────────────────────────────
ACTIONS = ["kill", "continue", "invest_next_phase"]

# ── Stage progression map ─────────────────────────────────────────────────────
STAGE_PROGRESSION = {
    "preclinical": "ind_filing",
    "ind_filing": "phase1",
    "phase1": "phase2",
    "phase2": "phase3",
    "phase3": "nda_submitted",
    "nda_submitted": "approved",
}

# ── Typical cost per stage (USD) ──────────────────────────────────────────────
STAGE_COSTS = {
    "preclinical":  2_000_000,
    "ind_filing":   3_000_000,
    "phase1":       8_000_000,
    "phase2":      30_000_000,
    "phase3":     120_000_000,
    "nda_submitted": 25_000_000,
}

APPROVAL_PAYOFF = 200_000_000


@dataclass
class ProjectEpisode:
    """A single drug development episode used as RL environment state."""

    project_id: str
    hypothesis: str
    indication: str
    true_success: bool                      # hidden ground truth (from DB outcome)
    stage: str = "preclinical"
    probability: float = 0.35              # agent's current belief
    cumulative_cost: float = 0.0
    step: int = 0
    terminated: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_state_vector(self) -> list[float]:
        """Compact numeric state for the RL policy."""
        stage_keys = list(STAGE_COSTS.keys())
        n_stages   = len(stage_keys)
        stage_idx  = stage_keys.index(self.stage) if self.stage in STAGE_COSTS else 0
        return [
            stage_idx / max(n_stages - 1, 1),
            self.probability,
            math.log1p(self.cumulative_cost / 1e6),
            float(self.step),
        ]


@dataclass
class StepResult:
    state: list[float]
    reward: float
    done: bool
    info: dict[str, Any]


class BioVentureEnv:
    """
    Sequential decision environment for one drug development project.

    Observation: [stage_norm, probability, cost_log, step]
    Actions:     0=kill, 1=continue, 2=invest_next_phase
    Reward:      cost-penalized expected value at each step;
                 +approval_payoff at end if successful, -sunk_cost if failed
    """

    def __init__(self, episodes: list[ProjectEpisode], noise_std: float = 0.10) -> None:
        """
        episodes: pre-built list of ProjectEpisode from DB records
        noise_std: standard deviation of Gaussian noise added to probability updates
        """
        self.episodes = episodes
        self.noise_std = noise_std
        self._current: ProjectEpisode | None = None
        self._episode_idx = 0

    @classmethod
    def from_records(cls, rows: list[dict[str, Any]], noise_std: float = 0.10) -> "BioVentureEnv":
        episodes: list[ProjectEpisode] = []
        for row in rows:
            outcome = row.get("outcome", "unknown")
            if outcome in ("approved",):
                true_success = True
            elif "discontinued" in outcome:
                true_success = False
            else:
                # unknown/ongoing: sample from prior
                prior = float(row.get("prior_probability", 0.35))
                true_success = random.random() < prior

            # Map outcome stage → starting stage (one stage back)
            stage_map = {v: k for k, v in STAGE_PROGRESSION.items()}
            current_stage = stage_map.get(row.get("clinical_stage", "phase1"), "preclinical")

            episodes.append(ProjectEpisode(
                project_id=str(row.get("id", "")),
                hypothesis=row.get("title", "")[:80],
                indication=row.get("indication", "unknown"),
                true_success=true_success,
                stage=current_stage,
                probability=float(row.get("feat_prior", 0.35)),
            ))

        return cls(episodes, noise_std)

    def reset(self, episode_idx: int | None = None) -> list[float]:
        if episode_idx is not None:
            self._episode_idx = episode_idx % len(self.episodes)
        else:
            self._episode_idx = random.randrange(len(self.episodes))

        ep = self.episodes[self._episode_idx]
        self._current = ProjectEpisode(
            project_id=ep.project_id,
            hypothesis=ep.hypothesis,
            indication=ep.indication,
            true_success=ep.true_success,
            stage=ep.stage,
            probability=ep.probability,
        )
        return self._current.to_state_vector()

    def step(self, action: int) -> StepResult:
        ep = self._current
        assert ep is not None, "Call reset() before step()"

        action_name = ACTIONS[action]
        stage_cost = STAGE_COSTS.get(ep.stage, 0)
        reward = 0.0
        done = False

        if action_name == "kill":
            # Immediate kill: reward is 0 (saved future cost), penalize if success
            reward = 5_000_000 * ep.probability if not ep.true_success else -10_000_000
            done = True
            ep.terminated = True

        elif action_name == "continue":
            # Run current stage, update probability with noisy signal
            ep.cumulative_cost += stage_cost
            evidence_signal = float(ep.true_success) + random.gauss(0, self.noise_std)
            evidence_signal = max(0.01, min(0.99, evidence_signal))

            # Bayesian-style update
            prior_odds = ep.probability / (1 - ep.probability + 1e-9)
            bayes_factor = (evidence_signal / (1 - evidence_signal + 1e-9)) / \
                           (ep.probability / (1 - ep.probability + 1e-9) + 1e-9)
            bayes_factor = max(0.1, min(bayes_factor, 10.0))
            posterior_odds = prior_odds * bayes_factor
            ep.probability = posterior_odds / (1 + posterior_odds)
            ep.probability = float(min(max(ep.probability, 0.01), 0.99))

            # Step reward: incremental expected value
            ev = ep.probability * APPROVAL_PAYOFF - ep.cumulative_cost
            reward = ev * 0.01   # scaled

            # Check if we reached terminal stage
            next_stage = STAGE_PROGRESSION.get(ep.stage)
            if next_stage is None:
                done = True
                reward += APPROVAL_PAYOFF if ep.true_success else -ep.cumulative_cost
                ep.terminated = True
            else:
                ep.stage = next_stage
                ep.step += 1

        elif action_name == "invest_next_phase":
            # Fast-track to next phase with larger investment
            next_stage = STAGE_PROGRESSION.get(ep.stage)
            if next_stage is None:
                done = True
                reward = APPROVAL_PAYOFF if ep.true_success else -ep.cumulative_cost
                ep.terminated = True
            else:
                ep.cumulative_cost += stage_cost * 1.5
                ep.stage = next_stage
                ep.step += 1

                ev = ep.probability * APPROVAL_PAYOFF - ep.cumulative_cost
                reward = ev * 0.012

        ep.history.append({
            "step": ep.step,
            "action": action_name,
            "stage": ep.stage,
            "probability": round(ep.probability, 4),
            "reward": round(reward, 2),
            "cost": ep.cumulative_cost,
        })

        return StepResult(
            state=ep.to_state_vector(),
            reward=reward,
            done=done,
            info={
                "stage": ep.stage,
                "probability": ep.probability,
                "cumulative_cost": ep.cumulative_cost,
                "true_success": ep.true_success,
                "action": action_name,
            },
        )

    def episode_summary(self) -> dict[str, Any]:
        ep = self._current
        if ep is None:
            return {}
        return {
            "project_id": ep.project_id,
            "hypothesis": ep.hypothesis,
            "final_stage": ep.stage,
            "final_probability": round(ep.probability, 4),
            "cumulative_cost": ep.cumulative_cost,
            "true_success": ep.true_success,
            "steps": ep.step,
            "history": ep.history,
        }


# ── Simple Q-learning policy for demonstration ───────────────────────────────

class TabularQAgent:
    """
    Lightweight tabular Q-learning agent — usable as a baseline
    before plugging in a deep RL framework.
    State is discretised into (stage_bin, probability_bin).
    """

    def __init__(self, lr: float = 0.1, gamma: float = 0.95, epsilon: float = 0.2) -> None:
        self.lr = lr
        self.gamma = gamma
        self.epsilon = epsilon
        # Initialise Q-table with slight bias toward "continue" to avoid
        # premature kill collapse during early greedy eval
        self.q_table: dict[str, list[float]] = {}
        self._default_q = [0.0, 500_000.0, 100_000.0]  # kill, continue, invest_next_phase

    def _state_key(self, state: list[float]) -> str:
        stage_bin = int(state[0] * 3)
        prob_bin = int(state[1] * 5)
        return f"{stage_bin}:{prob_bin}"

    def _get_q(self, key: str) -> list[float]:
        if key not in self.q_table:
            self.q_table[key] = list(self._default_q)
        return self.q_table[key]

    def select_action(self, state: list[float], greedy: bool = False) -> int:
        if not greedy and random.random() < self.epsilon:
            return random.randrange(len(ACTIONS))
        key = self._state_key(state)
        q_vals = self._get_q(key)
        return int(q_vals.index(max(q_vals)))

    def update(self, state: list[float], action: int, reward: float,
               next_state: list[float], done: bool) -> None:
        key = self._state_key(state)
        next_key = self._state_key(next_state)
        q = self._get_q(key)
        next_max = max(self._get_q(next_key)) if not done else 0.0
        q[action] += self.lr * (reward + self.gamma * next_max - q[action])

    def train(self, env: BioVentureEnv, episodes: int = 500) -> list[float]:
        """Run training loop, return per-episode total rewards."""
        episode_rewards: list[float] = []
        for _ in range(episodes):
            state = env.reset()
            total_reward = 0.0
            for _ in range(10):   # max steps per episode
                action = self.select_action(state)
                result = env.step(action)
                self.update(state, action, result.reward, result.state, result.done)
                total_reward += result.reward
                state = result.state
                if result.done:
                    break
            episode_rewards.append(total_reward)
        return episode_rewards


def run_greedy_episode(env: BioVentureEnv, agent: TabularQAgent, episode_idx: int = 0) -> dict[str, Any]:
    """Run one greedy (no exploration) episode and return summary."""
    state = env.reset(episode_idx)
    for _ in range(10):
        action = agent.select_action(state, greedy=True)
        result = env.step(action)
        state = result.state
        if result.done:
            break
    return env.episode_summary()
