from __future__ import annotations
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from zhiyu_brain.adapters.base import NavigationAdapter, PurificationAdapter
from zhiyu_brain.common.config import load_yaml
from zhiyu_brain.common.types import CandidateTask, RobotStatus, SafetyAction, TaskState
from zhiyu_brain.decision.rule_policy import RuleBasedPolicy
from zhiyu_brain.logging.store import BrainLogStore
from zhiyu_brain.safety.supervisor import SafetySupervisor
from zhiyu_brain.task.state_machine import TaskStateMachine


class TaskManager:
    """High-level coordinator. It never drives motors or PWM directly."""

    def __init__(
        self,
        navigation: NavigationAdapter,
        purification: PurificationAdapter,
        safety: SafetySupervisor,
        store: BrainLogStore | None = None,
    ):
        self.system = load_yaml('config/system.yaml')
        self.navigation = navigation
        self.purification = purification
        self.safety = safety
        self.store = store
        self.sm = TaskStateMachine(store)
        self.rule_fallback = RuleBasedPolicy()
        self.current_region: str | None = None
        self.completed_cycles = 0

    def _target_pose(self, region: str) -> tuple[float, float, float]:
        loc = self.system['regions'][region]
        return float(loc['x']), float(loc['y']), 0.0

    def select_safe_task(self, ranked: list[CandidateTask], robot: RobotStatus) -> tuple[CandidateTask | None, str]:
        for task in ranked:
            if task.current_risk < float(self.system['min_task_risk']) and task.predicted_risk < float(self.system['min_task_risk']):
                continue
            decision = self.safety.check(task, robot)
            if self.store:
                self.store.log('safety_events', {
                    'decision_id': task.decision_id,
                    'region_id': task.region_id,
                    'action': decision.action.value,
                    'reason': decision.reason,
                }, task.decision_id)
            if decision.action == SafetyAction.ALLOW:
                return task, decision.reason
            if decision.action == SafetyAction.SAFE_STOP:
                self.sm.transition(TaskState.SAFE_STOP, decision.reason)
                return None, decision.reason
        return None, 'no_safe_candidate'

    def execute_cycle(
        self,
        ranked: list[CandidateTask],
        robot: RobotStatus,
        on_purify: Callable[[str, int], None] | None = None,
        on_recheck: Callable[[str], None] | None = None,
    ) -> CandidateTask | None:
        task_id = f'task_{self.completed_cycles + 1:03d}'
        self.sm.transition(TaskState.DECIDING, 'rank_candidates', task_id)
        task, reason = self.select_safe_task(ranked, robot)
        if task is None:
            self.sm.transition(TaskState.COMPLETED, reason, task_id)
            return None

        self.sm.transition(TaskState.NAVIGATING, f'navigate_to_{task.region_id}', task_id)
        pose = self._target_pose(task.region_id)
        nav_ok = self.navigation.navigate_to(task.region_id, pose)
        if self.store:
            self.store.log('navigation_events', {
                'task_id': task_id, 'target_region': task.region_id, 'target_pose': pose,
                'success': nav_ok, 'arrived': self.navigation.is_arrived()
            }, task_id)
        if not nav_ok or not self.navigation.is_arrived():
            self.sm.transition(TaskState.LOCALIZATION_ERROR, 'navigation_failed', task_id)
            return None

        self.sm.transition(TaskState.ARRIVED, 'target_reached', task_id)
        robot.pose_x, robot.pose_y, robot.pose_yaw = self.navigation.get_pose()
        robot.current_region = task.region_id
        self.current_region = task.region_id

        self.sm.transition(TaskState.PURIFYING, 'start_purification', task_id)
        fan_level = 3 if task.predicted_risk >= 60 else 2 if task.predicted_risk >= 35 else 1
        self.purification.set_fan_level(fan_level)
        self.purification.start_purification()
        if on_purify:
            on_purify(task.region_id, fan_level)
        if self.store:
            self.store.log('purification_events', {
                'task_id': task_id, 'region_id': task.region_id,
                'fan_level': fan_level, 'event': 'start'
            }, task_id)

        # In the mock environment the purification interval is represented by simulator steps.
        self.purification.stop_purification()
        if self.store:
            self.store.log('purification_events', {
                'task_id': task_id, 'region_id': task.region_id,
                'fan_level': 0, 'event': 'stop'
            }, task_id)

        self.sm.transition(TaskState.RECHECKING, 'purification_complete_recheck', task_id)
        if on_recheck:
            on_recheck(task.region_id)
        self.sm.transition(TaskState.REDECIDING, 'state_updated_after_recheck', task_id)
        self.completed_cycles += 1
        return task
