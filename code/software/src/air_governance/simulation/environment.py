from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
import copy
import math
import numpy as np

from air_governance.common.config import load_yaml
from air_governance.common.types import EnvSample, QualityCode, SourceType
from air_governance.simulation.scenarios import SCENARIOS


@dataclass
class Pollutants:
    pm25: float
    voc: float
    co2: float
    temperature: float
    humidity: float


class IndoorEnvironmentSimulator:
    def __init__(self, scenario_id: str, seed: int = 0, config: dict | None = None, start_time: datetime | None = None):
        if scenario_id not in SCENARIOS:
            raise KeyError(f'Unknown scenario: {scenario_id}')
        self.cfg = config or load_yaml('config/simulation.yaml')
        self.system = load_yaml('config/system.yaml')
        self.scenario_id = scenario_id
        self.scenario = SCENARIOS[scenario_id]
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.step_index = 0
        self.start_time = start_time or datetime(2026, 8, 1, 9, 0, 0)
        self.current_time = self.start_time
        self.regions = list(self.system['regions'])
        self.state: dict[str, Pollutants] = {}
        for rid in self.regions:
            self.state[rid] = Pollutants(
                pm25=float(self.rng.uniform(8, 16)),
                voc=float(self.rng.uniform(70, 130)),
                co2=float(self.rng.uniform(500, 700)),
                temperature=float(self.rng.uniform(22, 25)),
                humidity=float(self.rng.uniform(45, 60)),
            )
        self.purifying_region: str | None = None
        self.fan_level = 0

    def clone(self) -> 'IndoorEnvironmentSimulator':
        return copy.deepcopy(self)

    def set_purification(self, region_id: str | None, fan_level: int = 0) -> None:
        self.purifying_region = region_id
        self.fan_level = int(fan_level)

    def _apply_events(self) -> None:
        for e in self.scenario['events']:
            if e['start'] <= self.step_index <= e['end']:
                p = self.state[e['region']]
                setattr(p, e['pollutant'], getattr(p, e['pollutant']) + float(e['rate']) * float(self.rng.uniform(0.85, 1.15)))

    def _diffuse_and_decay(self) -> None:
        diff = float(self.cfg['diffusion_rate'])
        means = {
            'pm25': np.mean([p.pm25 for p in self.state.values()]),
            'voc': np.mean([p.voc for p in self.state.values()]),
            'co2': np.mean([p.co2 for p in self.state.values()]),
        }
        nd = self.cfg['natural_decay']
        pd = self.cfg['purification_decay']
        for rid, p in self.state.items():
            for field in ('pm25', 'voc', 'co2'):
                x = getattr(p, field)
                baseline = {'pm25': 8.0, 'voc': 60.0, 'co2': 450.0}[field]
                x += diff * (means[field] - x)
                x -= float(nd[field]) * max(0.0, x - baseline)
                if self.purifying_region == rid and self.fan_level > 0:
                    strength = self.fan_level / 3.0
                    x -= strength * float(pd[field]) * max(0.0, x - baseline)
                setattr(p, field, max(baseline, x))
            p.temperature += float(self.rng.normal(0, 0.02))
            p.humidity += float(self.rng.normal(0, 0.04))

    def step(self) -> None:
        self._apply_events()
        self._diffuse_and_decay()
        self.step_index += 1
        self.current_time += timedelta(seconds=int(self.cfg['sample_interval_seconds']))

    def _measure(self, rid: str) -> tuple[dict[str, float | None], bool, QualityCode]:
        p = self.state[rid]
        noise = self.cfg['sensor_noise']
        values: dict[str, float | None] = {
            'pm25': max(0.0, p.pm25 + self.rng.normal(0, noise['pm25_sd'])),
            'voc': max(0.0, p.voc + self.rng.normal(0, noise['voc_sd'])),
            'co2': max(250.0, p.co2 + self.rng.normal(0, noise['co2_sd'])),
            'temperature': p.temperature + self.rng.normal(0, noise['temperature_sd']),
            'humidity': p.humidity + self.rng.normal(0, noise['humidity_sd']),
        }
        q = QualityCode.GOOD
        valid = True
        if self.rng.random() < float(self.cfg['missing_probability']):
            field = self.rng.choice(list(values.keys()))
            values[str(field)] = None
            valid = False
            q = QualityCode.MISSING
        elif self.rng.random() < float(self.cfg['abnormal_probability']):
            values['pm25'] = float(values['pm25'] or 0.0) + 500.0
            valid = False
            q = QualityCode.OUTLIER
        return values, valid, q

    def samples(self, episode_id: str, robot_region: str = 'A', battery_soc: float = 100.0, task_id: str = '', decision_id: str = '') -> list[EnvSample]:
        robot = self.system['regions'][robot_region]
        out: list[EnvSample] = []
        for rid in self.regions:
            v, valid, q = self._measure(rid)
            loc = self.system['regions'][rid]
            out.append(EnvSample(
                timestamp=self.current_time,
                region_id=rid,
                pose_x=float(loc['x']),
                pose_y=float(loc['y']),
                pose_yaw=0.0,
                pm25=v['pm25'], voc=v['voc'], co2=v['co2'],
                temperature=v['temperature'], humidity=v['humidity'],
                fan_level=self.fan_level if self.purifying_region == rid else 0,
                battery_soc=float(battery_soc), task_id=task_id, decision_id=decision_id,
                robot_state='SIMULATION', valid_flag=valid, quality_code=q,
                source_type=SourceType.SIMULATION, episode_id=episode_id, scenario_id=self.scenario_id,
            ))
        return out

    def counterfactual_risk_gain(self, target_region: str, horizon_steps: int = 20, fan_level: int = 3) -> float:
        no_action = self.clone()
        action = self.clone()
        action.set_purification(target_region, fan_level)
        total_no = 0.0
        total_action = 0.0
        for _ in range(horizon_steps):
            for sim, key in ((no_action, 'no'), (action, 'yes')):
                sim.step()
            for rid in self.regions:
                # Physically actionable proxy intentionally excludes CO2 removal.
                pn = no_action.state[rid]
                pa = action.state[rid]
                total_no += max(0.0, pn.pm25 - 8.0) * 0.6 + max(0.0, pn.voc - 60.0) * 0.04
                total_action += max(0.0, pa.pm25 - 8.0) * 0.6 + max(0.0, pa.voc - 60.0) * 0.04
        return max(0.0, total_no - total_action)
