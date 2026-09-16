from __future__ import annotations
from pathlib import Path
from datetime import datetime
import json
import platform
import numpy as np
import pandas as pd
from sklearn.metrics import ndcg_score
from xgboost import XGBRanker

from zhiyu_brain.common.config import PROJECT_ROOT, load_yaml


RANK_FEATURES = [
    'current_risk','predicted_risk','trend_code','abnormal_duration','waiting_time','data_age',
    'distance','estimated_move_time','estimated_energy','battery_soc','switch_cost','scene_weight','reachable'
]


def _prepare_grouped(frame: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    ordered = frame.sort_values(['decision_id','region_id']).reset_index(drop=True)
    sizes = ordered.groupby('decision_id', sort=False).size().to_numpy(int)
    return ordered, sizes


def ranking_metrics(frame: pd.DataFrame, scores: np.ndarray, relevant_threshold: int = 3) -> dict[str, float]:
    tmp = frame.copy().reset_index(drop=True)
    tmp['pred_score'] = np.asarray(scores)
    ndcgs, precisions, top1 = [], [], []
    for _, g in tmp.groupby('decision_id', sort=False):
        y = g['relevance_grade'].to_numpy(float)
        s = g['pred_score'].to_numpy(float)
        if len(g) >= 2:
            ndcgs.append(float(ndcg_score([y], [s], k=min(3, len(g)))))
        order = np.argsort(-s)[:min(3, len(g))]
        true_rel = y >= relevant_threshold
        precisions.append(float(np.mean(true_rel[order])))
        pred_best_idx = int(np.argmax(s))
        max_grade = float(np.max(y))
        top1.append(float(y[pred_best_idx] == max_grade))
    return {
        'ndcg@3': float(np.mean(ndcgs)) if ndcgs else 0.0,
        'precision@3': float(np.mean(precisions)) if precisions else 0.0,
        'top1_accuracy': float(np.mean(top1)) if top1 else 0.0,
        'n_groups': int(tmp['decision_id'].nunique()),
    }


class RankerModel:
    def __init__(self, model: XGBRanker | None = None, feature_columns: list[str] | None = None):
        cfg = load_yaml('config/ranker.yaml')
        self.model = model or XGBRanker(
            objective=cfg['objective'],
            n_estimators=int(cfg['n_estimators']), max_depth=int(cfg['max_depth']),
            learning_rate=float(cfg['learning_rate']), subsample=float(cfg['subsample']),
            colsample_bytree=float(cfg['colsample_bytree']), random_state=int(cfg['random_seed']),
            lambdarank_pair_method='topk', lambdarank_num_pair_per_sample=4, n_jobs=2,
        )
        self.feature_columns = feature_columns or list(RANK_FEATURES)

    def fit(self, frame: pd.DataFrame) -> None:
        ordered, sizes = _prepare_grouped(frame)
        self.model.fit(ordered[self.feature_columns], ordered['relevance_grade'], group=sizes, verbose=False)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        X = frame.reindex(columns=self.feature_columns, fill_value=0.0)
        return self.model.predict(X)

    def evaluate(self, frame: pd.DataFrame) -> dict[str, float]:
        return ranking_metrics(frame, self.predict(frame), int(load_yaml('config/ranker.yaml')['relevance']['relevant_threshold']))

    def save(self, out_dir: str | Path, metadata: dict | None = None) -> None:
        p = Path(out_dir)
        if not p.is_absolute(): p = PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        self.model.save_model(p / 'model.json')
        (p / 'feature_columns.json').write_text(json.dumps(self.feature_columns, ensure_ascii=False, indent=2), encoding='utf-8')
        meta = {
            'model_version': 'ranker_xgb_v1', 'feature_version': 'candidate_v1',
            'training_time': datetime.utcnow().isoformat(), 'random_seed': 20260909,
            'python_version': platform.python_version(), 'hyperparameters': self.model.get_params(),
        }
        if metadata: meta.update(metadata)
        (p / 'model_metadata.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    @classmethod
    def load(cls, model_dir: str | Path) -> 'RankerModel':
        p = Path(model_dir)
        if not p.is_absolute(): p = PROJECT_ROOT / p
        features = json.loads((p / 'feature_columns.json').read_text(encoding='utf-8'))
        model = XGBRanker()
        model.load_model(p / 'model.json')
        return cls(model=model, feature_columns=features)
