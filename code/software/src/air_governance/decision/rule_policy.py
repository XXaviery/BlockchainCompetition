from __future__ import annotations
from zhiyu_brain.common.config import load_yaml
from zhiyu_brain.common.types import CandidateTask, Trend


class RuleBasedPolicy:
    def __init__(self, config: dict | None = None):
        self.cfg = config or load_yaml('config/rule_policy.yaml')

    def score(self, c: CandidateTask) -> float:
        w = self.cfg['weights']
        trend = 1.0 if c.trend == Trend.RISING else -0.4 if c.trend == Trend.FALLING else 0.0
        return (
            w['current_risk'] * c.current_risk / 100.0
            + w['predicted_risk'] * c.predicted_risk / 100.0
            + w['rising_trend'] * trend
            + w['waiting_time'] * min(c.waiting_time / 600.0, 1.0)
            + w['abnormal_duration'] * min(c.abnormal_duration / 600.0, 1.0)
            + w['distance'] * c.distance
            + w['estimated_energy'] * c.estimated_energy
            + w['switch_cost'] * c.switch_cost
        )

    def rank(self, candidates: list[CandidateTask]) -> list[CandidateTask]:
        for c in candidates:
            c.task_score = float(self.score(c))
        return sorted(candidates, key=lambda x: x.task_score, reverse=True)
