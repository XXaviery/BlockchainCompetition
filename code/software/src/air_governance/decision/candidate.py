from __future__ import annotations
import math
from typing import Iterable

from air_governance.common.config import load_yaml
from air_governance.common.types import CandidateTask, RegionState, RobotStatus, Trend
from air_governance.labels.risk_teacher import RiskTeacher


def euclidean(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class CandidateGenerator:
    def __init__(self, system_cfg: dict | None = None, risk_cfg: dict | None = None):
        self.system = system_cfg or load_yaml('config/system.yaml')
        self.risk_cfg = risk_cfg or load_yaml('config/risk.yaml')
        self.teacher = RiskTeacher(self.risk_cfg)
        self.waiting: dict[str, float] = {rid: 0.0 for rid in self.system['regions']}

    def trend_from_delta(self, delta: float) -> Trend:
        tc = self.risk_cfg['trend_classification']
        if delta >= float(tc['rising_delta']):
            return Trend.RISING
        if delta <= float(tc['falling_delta']):
            return Trend.FALLING
        return Trend.STABLE

    def generate(self, decision_id: str, states: dict[str, RegionState], predicted: dict[str, float], robot: RobotStatus, current_region: str | None = None) -> list[CandidateTask]:
        result: list[CandidateTask] = []
        step = float(self.system['decision_interval_seconds'])
        for rid, st in states.items():
            current_risk = self.teacher.current_risk(st)
            pred = float(predicted.get(rid, current_risk))
            delta = pred - current_risk
            trend = self.trend_from_delta(delta)
            loc = self.system['regions'][rid]
            distance = euclidean((robot.pose_x, robot.pose_y), (float(loc['x']), float(loc['y'])))
            move_time = distance / 0.45
            energy = 0.12 * distance + 0.02 * move_time
            if current_risk >= float(self.system['min_task_risk']):
                self.waiting[rid] += step
            else:
                self.waiting[rid] = 0.0
            result.append(CandidateTask(
                decision_id=decision_id,
                region_id=rid,
                current_risk=current_risk,
                predicted_risk=pred,
                trend=trend,
                abnormal_duration=st.abnormal_duration,
                waiting_time=self.waiting[rid],
                data_age=st.data_age,
                distance=distance,
                estimated_move_time=move_time,
                estimated_energy=energy,
                battery_soc=robot.battery_soc,
                switch_cost=0.0 if current_region in (None, rid) else 1.0,
                scene_weight=1.0,
                reachable=rid not in robot.forbidden_regions,
            ))
        return result
