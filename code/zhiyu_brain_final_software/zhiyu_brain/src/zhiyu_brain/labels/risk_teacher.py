from __future__ import annotations
import math
from zhiyu_brain.common.config import load_yaml
from zhiyu_brain.common.types import RegionState


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _linear_norm(x: float, low: float, high: float) -> float:
    if high <= low:
        raise ValueError('high must be greater than low')
    return _clip01((x - low) / (high - low))


def _comfort_penalty(x: float, ideal_low: float, ideal_high: float, hard_low: float, hard_high: float) -> float:
    if ideal_low <= x <= ideal_high:
        return 0.0
    if x < ideal_low:
        return _clip01((ideal_low - x) / max(1e-9, ideal_low - hard_low))
    return _clip01((x - ideal_high) / max(1e-9, hard_high - ideal_high))


class RiskTeacher:
    def __init__(self, config: dict | None = None):
        self.cfg = config or load_yaml('config/risk.yaml')

    def current_risk(self, state: RegionState) -> float:
        n = self.cfg['normalization']
        pw = self.cfg['pollution_weights']
        w = self.cfg['weights']
        pm = _linear_norm(state.current_pm25, **n['pm25'])
        voc = _linear_norm(state.current_voc, **n['voc'])
        co2 = _linear_norm(state.current_co2, **n['co2'])
        pollution = pw['pm25'] * pm + pw['voc'] * voc + pw['co2'] * co2
        positive_slope = max(0.0, state.slope_pm25 / 5.0) + max(0.0, state.slope_voc / 25.0) + max(0.0, state.slope_co2 / 40.0)
        trend = _clip01(positive_slope / max(1e-9, self.cfg['trend']['saturation_slope']))
        duration = 1.0 - math.exp(-max(0.0, state.abnormal_duration) / float(self.cfg['duration']['tau_seconds']))
        tc = _comfort_penalty(state.current_temperature, **n['temperature'])
        hc = _comfort_penalty(state.current_humidity, **n['humidity'])
        comfort = 0.5 * (tc + hc)
        risk = 100.0 * _clip01(w['pollution'] * pollution + w['trend'] * trend + w['duration'] * duration + w['comfort'] * comfort)
        return float(risk)

    def confidence(self, state: RegionState) -> float:
        q = 1.0 if state.valid_flag else 0.25
        if state.quality_code in {'OUTLIER', 'MISSING', 'SENSOR_FAULT'}:
            q *= 0.4
        stale = float(self.cfg['quality']['stale_seconds'])
        freshness = max(0.0, 1.0 - state.data_age / max(stale, 1.0))
        return _clip01(q * freshness)

    def purifiable_fraction(self, state: RegionState) -> float:
        """Only PM2.5 and VOCs contribute to direct filter treatment benefit; CO2 does not."""
        n = self.cfg['normalization']
        pm = _linear_norm(state.current_pm25, **n['pm25'])
        voc = _linear_norm(state.current_voc, **n['voc'])
        return _clip01(0.58 * pm + 0.42 * voc)
