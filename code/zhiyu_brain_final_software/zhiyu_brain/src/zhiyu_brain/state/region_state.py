from __future__ import annotations
from collections import defaultdict, deque
from datetime import datetime
from typing import Iterable
import math
import numpy as np

from zhiyu_brain.common.types import EnvSample, RegionState, QualityCode
from zhiyu_brain.common.config import load_yaml


class RegionStateBuilder:
    def __init__(self, system_config: dict | None = None):
        self.cfg = system_config or load_yaml('config/system.yaml')
        self.window_seconds = int(self.cfg['rolling_window_seconds'])
        self.sample_interval = int(self.cfg['sample_interval_seconds'])
        maxlen = max(3, self.window_seconds // self.sample_interval + 3)
        self.buffers: dict[str, deque[EnvSample]] = defaultdict(lambda: deque(maxlen=maxlen))
        self.last_valid: dict[str, EnvSample] = {}
        self.abnormal_start: dict[str, datetime | None] = defaultdict(lambda: None)
        self.latest_states: dict[str, RegionState] = {}

    @staticmethod
    def _slope(samples: list[EnvSample], field: str) -> float:
        valid = [(i, getattr(s, field)) for i, s in enumerate(samples) if getattr(s, field) is not None and s.valid_flag]
        if len(valid) < 2:
            return 0.0
        x = np.asarray([i for i, _ in valid], dtype=float)
        y = np.asarray([float(v) for _, v in valid], dtype=float)
        if np.allclose(y, y[0]):
            return 0.0
        return float(np.polyfit(x, y, 1)[0])

    @staticmethod
    def _stats(samples: list[EnvSample], field: str) -> tuple[float, float, float]:
        vals = [float(getattr(s, field)) for s in samples if s.valid_flag and getattr(s, field) is not None]
        if not vals:
            return 0.0, 0.0, 0.0
        a = np.asarray(vals, dtype=float)
        return float(a.mean()), float(a.max()), float(a.std(ddof=0))

    @staticmethod
    def _abnormal(sample: EnvSample) -> bool:
        if not sample.valid_flag:
            return False
        return bool((sample.pm25 or 0) > 35 or (sample.voc or 0) > 350 or (sample.co2 or 0) > 1000)

    def update(self, sample: EnvSample) -> RegionState:
        buf = self.buffers[sample.region_id]
        buf.append(sample)
        if sample.valid_flag:
            self.last_valid[sample.region_id] = sample
        effective = sample if sample.valid_flag else self.last_valid.get(sample.region_id, sample)
        samples = list(buf)

        if self._abnormal(sample):
            if self.abnormal_start[sample.region_id] is None:
                self.abnormal_start[sample.region_id] = sample.timestamp
        else:
            self.abnormal_start[sample.region_id] = None
        start = self.abnormal_start[sample.region_id]
        abnormal_duration = 0.0 if start is None else max(0.0, (sample.timestamp - start).total_seconds())

        pm_mean, pm_max, pm_std = self._stats(samples, 'pm25')
        voc_mean, voc_max, voc_std = self._stats(samples, 'voc')
        co2_mean, co2_max, co2_std = self._stats(samples, 'co2')
        pm_slope = self._slope(samples, 'pm25')
        voc_slope = self._slope(samples, 'voc')
        co2_slope = self._slope(samples, 'co2')

        last = self.last_valid.get(sample.region_id)
        data_age = 1e9 if last is None else max(0.0, (sample.timestamp - last.timestamp).total_seconds())
        quality = sample.quality_code.value
        if data_age > 180 and sample.valid_flag:
            quality = QualityCode.STALE.value

        neighbor_vals = [
            st.current_pm25 for rid, st in self.latest_states.items() if rid != sample.region_id
        ]
        current_pm = float(effective.pm25 or 0.0)
        neighbor_diff = current_pm - (float(np.mean(neighbor_vals)) if neighbor_vals else current_pm)

        hour = sample.timestamp.hour
        state = RegionState(
            timestamp=sample.timestamp,
            region_id=sample.region_id,
            current_pm25=float(effective.pm25 or 0.0),
            current_voc=float(effective.voc or 0.0),
            current_co2=float(effective.co2 or 0.0),
            current_temperature=float(effective.temperature or 0.0),
            current_humidity=float(effective.humidity or 0.0),
            rolling_mean_pm25=pm_mean,
            rolling_mean_voc=voc_mean,
            rolling_mean_co2=co2_mean,
            rolling_max_pm25=pm_max,
            rolling_max_voc=voc_max,
            rolling_max_co2=co2_max,
            rolling_std_pm25=pm_std,
            rolling_std_voc=voc_std,
            rolling_std_co2=co2_std,
            slope_pm25=pm_slope,
            slope_voc=voc_slope,
            slope_co2=co2_slope,
            abnormal_duration=abnormal_duration,
            neighbor_diff=neighbor_diff,
            data_age=data_age,
            sample_interval=float(self.sample_interval),
            valid_flag=sample.valid_flag,
            quality_code=quality,
            last_update=effective.timestamp,
            battery_soc=sample.battery_soc,
            hour=hour,
            time_period=0 if hour < 6 else 1 if hour < 12 else 2 if hour < 18 else 3,
            episode_id=sample.episode_id,
            scenario_id=sample.scenario_id,
        )
        self.latest_states[sample.region_id] = state
        return state

    def get_all(self) -> dict[str, RegionState]:
        return dict(self.latest_states)
