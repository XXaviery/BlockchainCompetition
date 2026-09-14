from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import copy
import numpy as np
import pandas as pd

from zhiyu_brain.adapters.mock_navigation import MockNavigationAdapter
from zhiyu_brain.adapters.mock_purification import MockPurificationAdapter
from zhiyu_brain.common.config import load_yaml, resolve_project_root, set_active_project_root
from zhiyu_brain.common.types import (
    CandidateTask, RobotStatus, SystemSnapshot, TaskState, Trend, SafetyAction,
)
from zhiyu_brain.decision.candidate import CandidateGenerator
from zhiyu_brain.decision.rule_policy import RuleBasedPolicy
from zhiyu_brain.decision.task_ranker import rank_candidates, candidate_frame
from zhiyu_brain.labels.risk_teacher import RiskTeacher
from zhiyu_brain.logging.store import BrainLogStore
from zhiyu_brain.models.ranker_model import RankerModel
from zhiyu_brain.models.risk_model import RiskModel
from zhiyu_brain.safety.supervisor import SafetySupervisor
from zhiyu_brain.simulation.environment import IndoorEnvironmentSimulator
from zhiyu_brain.state.region_state import RegionStateBuilder
from zhiyu_brain.task.manager import TaskManager


STATE_SEQUENCE = [
    TaskState.SENSING, TaskState.DECIDING, TaskState.NAVIGATING,
    TaskState.ARRIVED, TaskState.PURIFYING, TaskState.RECHECKING,
    TaskState.REDECIDING,
]
SAFETY_TYPES = {'NORMAL', 'LOW_BATTERY', 'LOCALIZATION_ERROR', 'FORBIDDEN_ZONE', 'MODEL_ERROR'}


def risk_feature_frame(
    states: dict[str, Any], feature_columns: list[str], root: str | Path | None = None
) -> pd.DataFrame:
    rows = []
    region_ids = list(load_yaml('config/system.yaml', root=root)['regions'])
    for rid, st in states.items():
        row = st.feature_dict()
        row.update({f'region_{r}': int(r == rid) for r in region_ids})
        rows.append(row)
    return pd.DataFrame(rows).reindex(columns=feature_columns, fill_value=0.0)


class UnifiedBrainRuntime:
    """Single software-in-the-loop runtime shared by CLI demo and GUI.

    Core behavior delegates to the Phase-1 domain types, state builder, XGBoost
    models, candidate generator, rule policy, SafetySupervisor, TaskManager,
    adapters, simulator and BrainLogStore. This class only coordinates staged UI
    execution and presentation state.
    """

    def __init__(self, root: str | Path | None = None, db_path: str | Path | None = None, seed: int = 20260999):
        self.root = resolve_project_root(root)
        set_active_project_root(self.root)
        self.system = load_yaml('config/system.yaml', root=self.root)
        self.seed = int(seed)
        self.store = BrainLogStore(db_path=db_path, root=self.root)
        self.risk_model = RiskModel.load(self.root / 'models/risk')
        self.rank_model = RankerModel.load(self.root / 'models/ranker')
        self.rule_policy = RuleBasedPolicy()
        self.safety = SafetySupervisor()
        self.generator = CandidateGenerator()
        self.teacher = RiskTeacher()
        self.nav = MockNavigationAdapter()
        self.purifier = MockPurificationAdapter()
        self.manager = TaskManager(self.nav, self.purifier, self.safety, self.store)
        self.policy_mode = 'ranker'
        self.pending_safety = 'NORMAL'
        self.model_error = False
        self.paused = False
        self.state_index = -1
        self.cycle = 1
        self.decision_counter = 0
        self.candidates: list[CandidateTask] = []
        self.selected_task: CandidateTask | None = None
        self.rule_selection: str | None = None
        self.ranker_selection: str | None = None
        self.before_after_cycles: list[dict[str, Any]] = []
        self.last_predictions: dict[str, float] = {}
        self._pre_purify_risk = 0.0
        self.reset(clear_db=True)

    @property
    def db(self) -> BrainLogStore:
        return self.store

    @property
    def models(self) -> 'UnifiedBrainRuntime':
        # Compatibility surface for GUI: the models are still the Phase-1 model objects.
        return self

    @property
    def status(self) -> str:
        return 'READY' if not self.model_error else 'MODEL_ERROR'

    @property
    def risk_path(self) -> Path:
        return self.root / 'models/risk/model.json'

    @property
    def ranker_path(self) -> Path:
        return self.root / 'models/ranker/model.json'

    @property
    def completed_cycles(self) -> int:
        return self.manager.completed_cycles

    @property
    def robot(self) -> RobotStatus:
        return self._robot

    @property
    def region_states(self):
        return self.builder.get_all()

    @property
    def region_defs(self) -> dict[str, dict[str, Any]]:
        return self.system['regions']

    @property
    def scene(self) -> dict[str, Any]:
        regions = []
        for rid, d in self.system['regions'].items():
            x, y = float(d['x']), float(d['y'])
            regions.append({'region_id': rid, 'name': d['name'], 'x': x - 0.9, 'y': y - 0.9, 'w': 1.8, 'h': 1.8, 'cx': x, 'cy': y})
        return {'regions': regions}

    def reset(self, clear_db: bool = True) -> SystemSnapshot:
        if clear_db:
            self.store.reset()
        self.sim = IndoorEnvironmentSimulator('scenario_3_multi_region', seed=self.seed)
        self.builder = RegionStateBuilder()
        self.nav = MockNavigationAdapter()
        self.purifier = MockPurificationAdapter()
        self.safety = SafetySupervisor()
        self.manager = TaskManager(self.nav, self.purifier, self.safety, self.store)
        self.generator = CandidateGenerator()
        self._robot = RobotStatus(
            pose_x=0.0, pose_y=0.0, pose_yaw=0.0, battery_soc=78.0, current_region='A',
            timestamp=datetime.now(timezone.utc).isoformat(), state=TaskState.IDLE.value,
            model_status='READY', navigation_status='IDLE', purification_status='IDLE',
            safety_status='NORMAL', policy_mode='ranker',
        )
        self.policy_mode = 'ranker'
        self.pending_safety = 'NORMAL'
        self.model_error = False
        self.paused = False
        self.state_index = -1
        self.cycle = 1
        self.decision_counter = 0
        self.candidates = []
        self.selected_task = None
        self.rule_selection = None
        self.ranker_selection = None
        self.before_after_cycles = []
        self.last_predictions = {}
        self._pre_purify_risk = 0.0
        # Same warm-up used by the original Phase-1 demo, but do not persist it as
        # report log evidence. This only initializes rolling state.
        self._ingest(34, log=False)
        self._annotate_states()
        return self.snapshot()

    def pause(self) -> None:
        self.paused = True
        self._robot.state = TaskState.PAUSED.value

    def resume(self) -> None:
        self.paused = False
        if self.manager.sm.state == TaskState.PAUSED:
            self.manager.sm.state = TaskState.IDLE
        if self._robot.state == TaskState.PAUSED.value:
            self._robot.state = self.manager.sm.state.value

    def set_policy(self, mode: str) -> None:
        if mode not in {'ranker', 'rule'}:
            raise ValueError('policy must be ranker or rule')
        self.policy_mode = mode
        self._robot.policy_mode = mode

    def inject_safety_event(self, event_type: str) -> None:
        if event_type not in SAFETY_TYPES:
            raise ValueError(f'unsupported safety event: {event_type}')
        self.pending_safety = event_type
        if event_type == 'MODEL_ERROR':
            self.model_error = True
            self._robot.model_status = 'MODEL_ERROR'
        elif event_type == 'LOW_BATTERY':
            self._robot.battery_soc = min(self._robot.battery_soc, 12.0)
        elif event_type == 'NORMAL':
            self.model_error = False
            self._robot.model_status = 'READY'
            self._robot.safety_status = 'NORMAL'
            self._robot.localization_ok = True
            self._robot.forbidden_regions.clear()

    def _ingest(self, steps: int, log: bool = True) -> None:
        episode = 'unified_gui_demo'
        for _ in range(int(steps)):
            samples = self.sim.samples(
                episode, robot_region=self._robot.current_region, battery_soc=self._robot.battery_soc,
                task_id=self._robot.task_id, decision_id=self._robot.decision_id,
            )
            for sample in samples:
                if log:
                    self.store.log('env_samples', sample.to_dict(), event_key=sample.region_id, timestamp=sample.timestamp)
                st = self.builder.update(sample)
                if log:
                    self.store.log('region_states', {
                        **st.feature_dict(), 'timestamp': st.timestamp.isoformat(),
                        'region_id': st.region_id, 'decision_id': self._robot.decision_id,
                    }, event_key=st.region_id, timestamp=st.timestamp)
            self.sim.step()
            self._robot.battery_soc = max(0.0, self._robot.battery_soc - 0.03)

    def _annotate_states(self) -> None:
        states = self.builder.get_all()
        if not states:
            return
        X = risk_feature_frame(states, self.risk_model.feature_columns, root=self.root)
        pred = self.risk_model.predict(X)
        self.last_predictions = {rid: float(p) for rid, p in zip(states, pred)}
        # CandidateGenerator remains the canonical source of current risk/trend/cost annotations.
        base = self.generator.generate(
            self._robot.decision_id or 'preview', states, self.last_predictions,
            self._robot, current_region=self.manager.current_region,
        )
        by_region = {c.region_id: c for c in base}
        for rid, st in states.items():
            c = by_region[rid]
            st.current_risk = c.current_risk
            st.predicted_risk = c.predicted_risk
            st.trend = c.trend
            st.waiting_time = c.waiting_time
            st.distance = c.distance
            st.estimated_move_time = c.estimated_move_time
            st.estimated_energy = c.estimated_energy
            st.switch_cost = c.switch_cost

    def _copy_candidates(self, candidates: list[CandidateTask]) -> list[CandidateTask]:
        return [replace(c) for c in candidates]

    def _configure_safety_inputs(self, event: str) -> None:
        self._robot.localization_ok = True
        self._robot.collision_risk = False
        self._robot.sensor_health = True
        self._robot.fan_health = True
        self._robot.emergency_stop = False
        self._robot.manual_stop = False
        self._robot.forbidden_regions.clear()
        if event == 'LOCALIZATION_ERROR':
            self._robot.localization_ok = False
        elif event == 'FORBIDDEN_ZONE':
            self._robot.forbidden_regions.add('C')
        elif event == 'LOW_BATTERY':
            self._robot.battery_soc = min(self._robot.battery_soc, 12.0)

    def _prepare_decision(self) -> None:
        self.decision_counter += 1
        did = f'demo_decision_{self.decision_counter:03d}'
        tid = f'task_{self.cycle:03d}'
        self._robot.decision_id = did
        self._robot.task_id = tid
        self._annotate_states()
        states = self.builder.get_all()
        for rid, p in self.last_predictions.items():
            st = states[rid]
            self.store.log('risk_predictions', {
                'decision_id': did, 'region_id': rid, 'current_risk': st.current_risk,
                'predicted_risk': p, 'trend': st.trend.value, 'source_type': 'SIMULATION',
            }, event_key=did)

        base = self.generator.generate(did, states, self.last_predictions, self._robot, current_region=self.manager.current_region)
        ranker_ranked = rank_candidates(self.rank_model, self._copy_candidates(base))
        rule_ranked = self.rule_policy.rank(self._copy_candidates(base))
        self.ranker_selection = ranker_ranked[0].region_id if ranker_ranked else None
        self.rule_selection = rule_ranked[0].region_id if rule_ranked else None

        event = self.pending_safety
        self._configure_safety_inputs(event)
        if self.model_error or event == 'MODEL_ERROR' or self.policy_mode == 'rule':
            ranked = rule_ranked
            active_policy = 'rule_fallback' if (self.model_error or event == 'MODEL_ERROR') else 'rule'
        else:
            ranked = ranker_ranked
            active_policy = 'ranker'

        # Phase-1 TaskManager delegates candidate checks to Phase-1 SafetySupervisor.
        selected, reason = self.manager.select_safe_task(ranked, self._robot)
        self.selected_task = selected
        self.candidates = ranked
        for c in self.candidates:
            c.task_id = tid
            c.selected = int(selected is not None and c.region_id == selected.region_id)
            decision = self.safety.check(c, self._robot)
            c.valid_flag = int(decision.action == SafetyAction.ALLOW)
            c.safety_result = decision.reason if event != 'MODEL_ERROR' else 'MODEL_ERROR→RULE_FALLBACK'
        if selected:
            self._robot.target_region = selected.region_id
        else:
            self._robot.target_region = self._robot.current_region if event == 'LOCALIZATION_ERROR' else ''
        self._robot.policy_mode = active_policy
        self._robot.safety_status = event

        for pos, c in enumerate(self.candidates, 1):
            self.store.log('candidate_tasks', {**c.to_dict(), 'policy': active_policy, 'rank_position': pos}, event_key=did)
            self.store.log('rank_results', {
                'decision_id': did, 'region_id': c.region_id, 'task_score': c.task_score,
                'rank_position': pos, 'selected': c.selected, 'policy': active_policy,
            }, event_key=did)
        self.store.log('safety_events', {
            'decision_id': did, 'task_id': tid,
            'region_id': selected.region_id if selected else self._robot.current_region,
            'event_type': event,
            'input_task': (ranked[0].region_id if ranked else ''),
            'result': 'PASS' if selected else ('FALLBACK' if event == 'MODEL_ERROR' else 'REJECT_MOVEMENT'),
            'reason': reason if event != 'MODEL_ERROR' else 'ranker unavailable / injected model error',
            'fallback': 'RuleBasedPolicy' if event == 'MODEL_ERROR' else ('none' if selected else 'hold / reject unsafe task'),
        }, event_key=did)
        self.pending_safety = 'NORMAL'

    def _transition(self, state: TaskState, reason: str) -> None:
        self.manager.sm.transition(
            state, reason, self._robot.task_id,
            decision_id=self._robot.decision_id, region_id=self._robot.target_region or self._robot.current_region,
            cycle=self.cycle,
        )
        self._robot.state = state.value
        self._robot.timestamp = datetime.now(timezone.utc).isoformat()

    def step(self, force: bool = False) -> SystemSnapshot:
        if self.paused and not force:
            return self.snapshot()
        if self.manager.sm.state == TaskState.COMPLETED:
            return self.snapshot()
        self.state_index = (self.state_index + 1) % len(STATE_SEQUENCE)
        state = STATE_SEQUENCE[self.state_index]

        if state == TaskState.SENSING:
            self._transition(state, 'environment_sample_and_region_state_update')
            self._ingest(1, log=True)
            self._annotate_states()
            self._robot.navigation_status = 'IDLE'
            self._robot.purification_status = 'IDLE'
            self._robot.fan_level = 0
        elif state == TaskState.DECIDING:
            self._prepare_decision()
            self._transition(state, f'candidate_tasks_ranked_policy={self._robot.policy_mode}')
        elif state == TaskState.NAVIGATING:
            self._transition(state, 'navigation_adapter_execute')
            if self.selected_task:
                pose = self.manager._target_pose(self.selected_task.region_id)
                ok = self.nav.navigate_to(self.selected_task.region_id, pose)
                self._robot.navigation_status = 'MOCK_NAVIGATING' if ok else 'FAILED'
                self.store.log('navigation_events', {
                    'decision_id': self._robot.decision_id, 'task_id': self._robot.task_id,
                    'target_region': self.selected_task.region_id, 'target_pose': pose,
                    'event': 'NAVIGATE', 'status': self._robot.navigation_status,
                    'success': ok, 'arrived': self.nav.is_arrived(), 'source_type': 'SIMULATION',
                }, event_key=self._robot.task_id)
            else:
                self._robot.navigation_status = 'HELD_BY_SAFETY'
        elif state == TaskState.ARRIVED:
            self._transition(state, 'navigation_arrival_confirmation')
            if self.selected_task and self.nav.is_arrived():
                self._robot.pose_x, self._robot.pose_y, self._robot.pose_yaw = self.nav.get_pose()
                self._robot.current_region = self.selected_task.region_id
                self.manager.current_region = self.selected_task.region_id
                self._robot.navigation_status = 'MOCK_ARRIVED'
                self.store.log('navigation_events', {
                    'decision_id': self._robot.decision_id, 'task_id': self._robot.task_id,
                    'region_id': self._robot.current_region, 'event': 'ARRIVED',
                    'status': 'MOCK_ARRIVED', 'success': 1, 'arrived': 1, 'source_type': 'SIMULATION',
                }, event_key=self._robot.task_id)
        elif state == TaskState.PURIFYING:
            self._transition(state, 'purification_adapter_execute')
            if self.selected_task:
                fan_level = 3 if self.selected_task.predicted_risk >= 60 else 2 if self.selected_task.predicted_risk >= 35 else 1
                self._robot.fan_level = fan_level
                self._robot.purification_status = 'MOCK_PURIFYING'
                self._pre_purify_risk = float(self.selected_task.predicted_risk)
                self.purifier.set_fan_level(fan_level)
                self.purifier.start_purification()
                self.sim.set_purification(self.selected_task.region_id, fan_level)
                self._ingest(8, log=True)
                self.sim.set_purification(None, 0)
                self.purifier.stop_purification()
                self._annotate_states()
                post = float(self.builder.get_all()[self.selected_task.region_id].predicted_risk)
                self.before_after_cycles.append({'cycle': self.cycle, 'region_id': self.selected_task.region_id, 'pre_risk': self._pre_purify_risk, 'post_risk': post})
                self.store.log('purification_events', {
                    'decision_id': self._robot.decision_id, 'task_id': self._robot.task_id,
                    'region_id': self.selected_task.region_id, 'event': 'SIMULATED_PURIFICATION',
                    'fan_level': fan_level, 'pre_risk': self._pre_purify_risk, 'post_risk': post,
                    'source_type': 'SIMULATION',
                }, event_key=self._robot.task_id)
                self._robot.battery_soc = max(0.0, self._robot.battery_soc - 1.2)
        elif state == TaskState.RECHECKING:
            self._transition(state, 'post_treatment_recheck')
            self._robot.fan_level = 0
            self._robot.purification_status = 'RECHECKING'
            self._ingest(2, log=True)
            self._annotate_states()
        elif state == TaskState.REDECIDING:
            self._transition(state, 'state_updated_after_recheck')
            self.manager.completed_cycles += 1
            if self.manager.completed_cycles >= 3:
                self._transition(TaskState.COMPLETED, 'three_software_in_the_loop_cycles_completed')
                self._robot.navigation_status = 'IDLE'
                self._robot.purification_status = 'IDLE'
                self._robot.fan_level = 0
            else:
                self.cycle += 1
                self._robot.safety_status = 'NORMAL'
                # Safety injection lasts one decision only.
                self._robot.localization_ok = True
                self._robot.forbidden_regions.clear()
                if self._robot.battery_soc <= 12.0:
                    self._robot.battery_soc = 60.0
        return self.snapshot()

    def run_demo(self, cycles: int = 3, max_steps: int = 200) -> SystemSnapshot:
        target = max(3, int(cycles))
        steps = 0
        while self.manager.completed_cycles < target and self.manager.sm.state != TaskState.COMPLETED and steps < max_steps:
            self.step(force=True)
            steps += 1
        return self.snapshot()

    def snapshot(self) -> SystemSnapshot:
        states = self.builder.get_all()
        return SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            robot_state=self.manager.sm.state.value,
            robot_pose=self.nav.get_pose(),
            battery=self._robot.battery_soc,
            fan_level=self._robot.fan_level,
            regions=states,
            candidates=list(self.candidates),
            selected_task=replace(self.selected_task) if self.selected_task else None,
            safety_state=self._robot.safety_status,
            task_history=list(self.manager.sm.history),
            robot=replace(self._robot),
            completed_cycles=self.manager.completed_cycles,
            final_state=self.manager.sm.state.value,
            source_type='SIMULATION / SOFTWARE-IN-THE-LOOP',
        )

    def candidate_rows(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.candidates]

    def policy_comparison(self) -> dict[str, Any]:
        return {
            'decision_id': self._robot.decision_id or None,
            'rule_selection': self.rule_selection,
            'ranker_selection': self.ranker_selection,
            'different': bool(self.rule_selection and self.ranker_selection and self.rule_selection != self.ranker_selection),
            'active_policy': self._robot.policy_mode,
        }

    def feature_importance(self) -> dict[str, float]:
        vals = self.rank_model.model.feature_importances_
        return {k: float(v) for k, v in zip(self.rank_model.feature_columns, vals)}

    def local_explanation(self) -> dict[str, list[tuple[str, float]]]:
        if not self.selected_task:
            return {'positive': [], 'negative': []}
        import xgboost as xgb
        row = candidate_frame([self.selected_task]).reindex(columns=self.rank_model.feature_columns, fill_value=0.0)
        booster = self.rank_model.model.get_booster()
        dm = xgb.DMatrix(row, feature_names=self.rank_model.feature_columns)
        contrib = booster.predict(dm, pred_contribs=True)[0][:-1]
        pairs = list(zip(self.rank_model.feature_columns, map(float, contrib)))
        pos = sorted([p for p in pairs if p[1] >= 0], key=lambda kv: kv[1], reverse=True)[:5]
        neg = sorted([p for p in pairs if p[1] < 0], key=lambda kv: kv[1])[:5]
        return {'positive': pos, 'negative': neg}
