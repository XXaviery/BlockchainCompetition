from __future__ import annotations
from zhiyu_brain.common.config import load_yaml
from zhiyu_brain.common.types import CandidateTask, RobotStatus, SafetyAction, SafetyDecision


class SafetySupervisor:
    def __init__(self, system_cfg: dict | None = None):
        self.cfg = system_cfg or load_yaml('config/system.yaml')

    def check(self, task: CandidateTask, robot: RobotStatus) -> SafetyDecision:
        if robot.emergency_stop or robot.manual_stop:
            return SafetyDecision(SafetyAction.SAFE_STOP, 'emergency_or_manual_stop')
        if robot.battery_soc < float(self.cfg['low_battery_threshold']):
            return SafetyDecision(SafetyAction.REJECT, 'low_battery')
        if not robot.localization_ok:
            return SafetyDecision(SafetyAction.FALLBACK, 'localization_unreliable')
        if robot.collision_risk:
            return SafetyDecision(SafetyAction.SAFE_STOP, 'collision_risk')
        if not robot.sensor_health or not robot.fan_health:
            return SafetyDecision(SafetyAction.FALLBACK, 'device_health_fault')
        if task.region_id in robot.forbidden_regions:
            return SafetyDecision(SafetyAction.REJECT, 'forbidden_zone')
        if not task.reachable:
            return SafetyDecision(SafetyAction.REJECT, 'unreachable')
        return SafetyDecision(SafetyAction.ALLOW, 'constraints_passed')
