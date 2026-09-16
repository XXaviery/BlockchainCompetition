from __future__ import annotations
from datetime import datetime
from air_governance.common.types import EnvSample, QualityCode

BOUNDS = {
    'pm25': (0.0, 1000.0),
    'voc': (0.0, 5000.0),
    'co2': (250.0, 10000.0),
    'temperature': (-20.0, 60.0),
    'humidity': (0.0, 100.0),
}


def validate_sample(sample: EnvSample) -> EnvSample:
    values = {
        'pm25': sample.pm25,
        'voc': sample.voc,
        'co2': sample.co2,
        'temperature': sample.temperature,
        'humidity': sample.humidity,
    }
    if any(v is None for v in values.values()):
        sample.valid_flag = False
        sample.quality_code = QualityCode.MISSING
        return sample
    for key, value in values.items():
        lo, hi = BOUNDS[key]
        if not lo <= float(value) <= hi:
            sample.valid_flag = False
            sample.quality_code = QualityCode.OUTLIER
            return sample
    sample.valid_flag = True
    if sample.quality_code not in {QualityCode.WARMUP, QualityCode.SENSOR_FAULT}:
        sample.quality_code = QualityCode.GOOD
    return sample
