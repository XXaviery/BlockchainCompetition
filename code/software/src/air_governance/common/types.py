from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from typing import Any


class SourceType(str, Enum):
    REAL = 'REAL'
    SIMULATION = 'SIMULATION'
    SYNTHETIC = 'SYNTHETIC'
    REPLAY = 'REPLAY'


class QualityCode(str, Enum):
    GOOD = 'GOOD'
    WARMUP = 'WARMUP'
    MISSING = 'MISSING'
    OUTLIER = 'OUTLIER'
    STALE = 'STALE'
    SENSOR_FAULT = 'SENSOR_FAULT'


class Trend(str, Enum):
    RISING = 'RISING'
    STABLE = 'STABLE'
    FALLING = 'FALLING'


class TaskState(str, Enum):
    IDLE = 'IDLE'
    SENSING = 'SENSING'
    DECIDING = 'DECIDING'
    NAVIGATING = 'NAVIGATING'
    ARRIVED = 'ARRIVED'
    PURIFYING = 'PURIFYING'
    RECHECKING = 'RECHECKING'
    REDECIDING = 'REDECIDING'
    COMPLETED = 'COMPLETED'
    PAUSED = 'PAUSED'
    SAFE_STOP = 'SAFE_STOP'
    LOW_BATTERY = 'LOW_BATTERY'
    LOCALIZATION_ERROR = 'LOCALIZATION_ERROR'
    DEVICE_FAULT = 'DEVICE_FAULT'
    MANUAL_CONTROL = 'MANUAL_CONTROL'


class SafetyAction(str, Enum):
    ALLOW = 'ALLOW'
    REJECT = 'REJECT'
    SAFE_STOP = 'SAFE_STOP'
    FALLBACK = 'FALLBACK'


@dataclass(slots=True)
class EnvSample:
    timestamp: datetime
    region_id: str
    pose_x: float
    pose_y: float
    pose_yaw: float
    pm25: float | None
    voc: float | None
    co2: float | None
    temperature: float | None
    humidity: float | None
    fan_level: int
    battery_soc: float
    task_id: str
    decision_id: str
    robot_state: str
    valid_flag: bool
    quality_code: QualityCode
    source_type: SourceType
    episode_id: str = ''
    scenario_id: str = ''

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['timestamp'] = self.timestamp.isoformat()
        d['quality_code'] = self.quality_code.value
        d['source_type'] = self.source_type.value
        return d


@dataclass(slots=True)
class RegionState:
    timestamp: datetime
    region_id: str
    current_pm25: float
    current_voc: float
    current_co2: float
    current_temperature: float
    current_humidity: float
    rolling_mean_pm25: float
    rolling_mean_voc: float
    rolling_mean_co2: float
    rolling_max_pm25: float
    rolling_max_voc: float
    rolling_max_co2: float
    rolling_std_pm25: float
    rolling_std_voc: float
    rolling_std_co2: float
    slope_pm25: float
    slope_voc: float
    slope_co2: float
    abnormal_duration: float
    neighbor_diff: float
    data_age: float
    sample_interval: float
    valid_flag: bool
    quality_code: str
    last_update: datetime
    battery_soc: float
    hour: int
    time_period: int
    episode_id: str = ''
    scenario_id: str = ''
    # GUI/runtime annotations. They are produced from the Phase-1 risk/candidate
    # services and do not replace the raw regional state fields above.
    current_risk: float = 0.0
    predicted_risk: float = 0.0
    trend: Trend = Trend.STABLE
    waiting_time: float = 0.0
    distance: float = 0.0
    estimated_move_time: float = 0.0
    estimated_energy: float = 0.0
    switch_cost: float = 0.0

    @property
    def current_values(self) -> dict[str, float]:
        return {
            'pm25': self.current_pm25,
            'voc': self.current_voc,
            'co2': self.current_co2,
            'temperature': self.current_temperature,
            'humidity': self.current_humidity,
        }

    def feature_dict(self) -> dict[str, float | int | str]:
        return {
            'region_id': self.region_id,
            'current_pm25': self.current_pm25,
            'current_voc': self.current_voc,
            'current_co2': self.current_co2,
            'current_temperature': self.current_temperature,
            'current_humidity': self.current_humidity,
            'rolling_mean_pm25': self.rolling_mean_pm25,
            'rolling_mean_voc': self.rolling_mean_voc,
            'rolling_mean_co2': self.rolling_mean_co2,
            'rolling_max_pm25': self.rolling_max_pm25,
            'rolling_max_voc': self.rolling_max_voc,
            'rolling_max_co2': self.rolling_max_co2,
            'rolling_std_pm25': self.rolling_std_pm25,
            'rolling_std_voc': self.rolling_std_voc,
            'rolling_std_co2': self.rolling_std_co2,
            'slope_pm25': self.slope_pm25,
            'slope_voc': self.slope_voc,
            'slope_co2': self.slope_co2,
            'abnormal_duration': self.abnormal_duration,
            'neighbor_diff': self.neighbor_diff,
            'data_age': self.data_age,
            'sample_interval': self.sample_interval,
            'valid_flag': int(self.valid_flag),
            'hour': self.hour,
            'time_period': self.time_period,
        }


@dataclass(slots=True)
class CandidateTask:
    decision_id: str
    region_id: str
    current_risk: float
    predicted_risk: float
    trend: Trend
    abnormal_duration: float
    waiting_time: float
    data_age: float
    distance: float
    estimated_move_time: float
    estimated_energy: float
    battery_soc: float
    switch_cost: float
    scene_weight: float
    reachable: bool
    task_score: float = 0.0
    ground_truth_utility: float = 0.0
    relevance_grade: int = 0
    task_id: str = ''
    valid_flag: int = 1
    safety_result: str = 'NORMAL'
    selected: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['trend'] = self.trend.value
        return d


@dataclass(slots=True)
class RobotStatus:
    pose_x: float = 0.0
    pose_y: float = 0.0
    pose_yaw: float = 0.0
    battery_soc: float = 100.0
    localization_ok: bool = True
    collision_risk: bool = False
    sensor_health: bool = True
    fan_health: bool = True
    emergency_stop: bool = False
    manual_stop: bool = False
    forbidden_regions: set[str] = field(default_factory=set)
    current_region: str = 'A'
    # GUI-facing operational annotations; the safety flags above remain the
    # canonical inputs to Phase-1 SafetySupervisor.
    timestamp: str = ''
    target_region: str = ''
    state: str = TaskState.IDLE.value
    fan_level: int = 0
    model_status: str = 'READY'
    navigation_status: str = 'IDLE'
    purification_status: str = 'IDLE'
    safety_status: str = 'NORMAL'
    decision_id: str = ''
    task_id: str = ''
    policy_mode: str = 'ranker'

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['forbidden_regions'] = sorted(self.forbidden_regions)
        return d


@dataclass(slots=True)
class SafetyDecision:
    action: SafetyAction
    reason: str


@dataclass(slots=True)
class SystemSnapshot:
    timestamp: datetime
    robot_state: str
    robot_pose: tuple[float, float, float]
    battery: float
    fan_level: int
    regions: dict[str, Any]
    candidates: list[Any]
    selected_task: Any | None
    safety_state: str
    task_history: list[dict[str, Any]]
    # GUI/runtime fields. Existing Phase-1 constructors remain valid because
    # these fields are optional and appended.
    robot: RobotStatus | None = None
    completed_cycles: int = 0
    final_state: str = ''
    source_type: str = 'SIMULATION / SOFTWARE-IN-THE-LOOP'

    def to_dict(self) -> dict[str, Any]:
        return {
            'timestamp': self.timestamp.isoformat(),
            'robot_state': self.robot_state,
            'robot_pose': self.robot_pose,
            'battery': self.battery,
            'fan_level': self.fan_level,
            'regions': {
                k: (asdict(v) if hasattr(v, '__dataclass_fields__') else v)
                for k, v in self.regions.items()
            },
            'candidates': [c.to_dict() if hasattr(c, 'to_dict') else c for c in self.candidates],
            'selected_task': self.selected_task.to_dict() if hasattr(self.selected_task, 'to_dict') else self.selected_task,
            'safety_state': self.safety_state,
            'task_history': self.task_history,
            'robot': self.robot.to_dict() if self.robot else None,
            'completed_cycles': self.completed_cycles,
            'final_state': self.final_state or self.robot_state,
            'source_type': self.source_type,
        }
