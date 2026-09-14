from __future__ import annotations
import pandas as pd
from zhiyu_brain.common.types import CandidateTask, Trend
from zhiyu_brain.models.ranker_model import RankerModel


def candidate_frame(candidates: list[CandidateTask]) -> pd.DataFrame:
    rows=[]
    for c in candidates:
        rows.append({
            'current_risk':c.current_risk,'predicted_risk':c.predicted_risk,
            'trend_code':1 if c.trend==Trend.RISING else -1 if c.trend==Trend.FALLING else 0,
            'abnormal_duration':c.abnormal_duration,'waiting_time':c.waiting_time,'data_age':c.data_age,
            'distance':c.distance,'estimated_move_time':c.estimated_move_time,'estimated_energy':c.estimated_energy,
            'battery_soc':c.battery_soc,'switch_cost':c.switch_cost,'scene_weight':c.scene_weight,
            'reachable':int(c.reachable),
        })
    return pd.DataFrame(rows)


def rank_candidates(model: RankerModel, candidates: list[CandidateTask]) -> list[CandidateTask]:
    if not candidates:
        return []
    scores=model.predict(candidate_frame(candidates))
    for c,s in zip(candidates,scores): c.task_score=float(s)
    return sorted(candidates,key=lambda x:x.task_score,reverse=True)
