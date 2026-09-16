from __future__ import annotations
from pathlib import Path
import json
import platform
from datetime import datetime
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from xgboost import XGBRegressor

from zhiyu_brain.common.config import PROJECT_ROOT, load_yaml


DEFAULT_FEATURES = [
    'current_pm25','current_voc','current_co2','current_temperature','current_humidity',
    'rolling_mean_pm25','rolling_mean_voc','rolling_mean_co2',
    'rolling_max_pm25','rolling_max_voc','rolling_max_co2',
    'rolling_std_pm25','rolling_std_voc','rolling_std_co2',
    'slope_pm25','slope_voc','slope_co2','abnormal_duration','neighbor_diff',
    'data_age','sample_interval','valid_flag','hour','time_period',
    'region_A','region_B','region_C','region_D'
]


def trend_labels(current: np.ndarray, future: np.ndarray, rising: float = 3.0, falling: float = -3.0) -> np.ndarray:
    d = future - current
    return np.where(d >= rising, 1, np.where(d <= falling, -1, 0))


class RiskModel:
    def __init__(self, model: XGBRegressor | None = None, feature_columns: list[str] | None = None):
        self.model = model or XGBRegressor(
            n_estimators=180, max_depth=5, learning_rate=0.05,
            subsample=0.9, colsample_bytree=0.9,
            objective='reg:squarederror', random_state=20260909, n_jobs=2,
        )
        self.feature_columns = feature_columns or list(DEFAULT_FEATURES)

    def fit(self, train: pd.DataFrame, val: pd.DataFrame | None = None) -> None:
        X = train[self.feature_columns]
        y = train['future_risk']
        self.model.fit(X, y, verbose=False)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        X = frame.reindex(columns=self.feature_columns, fill_value=0.0)
        return self.model.predict(X)

    def evaluate(self, frame: pd.DataFrame) -> dict[str, float]:
        pred = self.predict(frame)
        y = frame['future_risk'].to_numpy(float)
        current = frame['current_risk'].to_numpy(float)
        true_trend = trend_labels(current, y)
        pred_trend = trend_labels(current, pred)
        return {
            'mae': float(mean_absolute_error(y, pred)),
            'rmse': float(mean_squared_error(y, pred) ** 0.5),
            'r2': float(r2_score(y, pred)),
            'trend_accuracy': float(np.mean(true_trend == pred_trend)),
            'n_samples': int(len(frame)),
        }

    def save(self, out_dir: str | Path, metadata: dict | None = None) -> None:
        p = Path(out_dir)
        if not p.is_absolute(): p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        self.model.save_model(p / 'model.json')
        (p / 'feature_columns.json').write_text(json.dumps(self.feature_columns, ensure_ascii=False, indent=2), encoding='utf-8')
        meta = {
            'model_version': 'risk_xgb_v1',
            'feature_version': 'region_state_v1',
            'training_time': datetime.utcnow().isoformat(),
            'random_seed': 20260909,
            'python_version': platform.python_version(),
            'hyperparameters': self.model.get_params(),
        }
        if metadata: meta.update(metadata)
        (p / 'model_metadata.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    @classmethod
    def load(cls, model_dir: str | Path) -> 'RiskModel':
        p = Path(model_dir)
        if not p.is_absolute(): p = PROJECT_ROOT / p
        features = json.loads((p / 'feature_columns.json').read_text(encoding='utf-8'))
        model = XGBRegressor()
        model.load_model(p / 'model.json')
        return cls(model=model, feature_columns=features)
