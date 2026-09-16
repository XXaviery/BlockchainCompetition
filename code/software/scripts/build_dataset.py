#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from datetime import datetime
import pandas as pd
import numpy as np
from sklearn.model_selection import GroupShuffleSplit

from scripts._paths import add_root_argument, resolve_cli_root
from air_governance.common.config import load_yaml
from air_governance.common.types import EnvSample, QualityCode, SourceType
from air_governance.state.region_state import RegionStateBuilder
from air_governance.labels.risk_teacher import RiskTeacher


def to_sample(r) -> EnvSample:
    return EnvSample(
        timestamp=datetime.fromisoformat(r.timestamp), region_id=r.region_id,
        pose_x=float(r.pose_x), pose_y=float(r.pose_y), pose_yaw=float(r.pose_yaw),
        pm25=None if pd.isna(r.pm25) else float(r.pm25), voc=None if pd.isna(r.voc) else float(r.voc),
        co2=None if pd.isna(r.co2) else float(r.co2), temperature=None if pd.isna(r.temperature) else float(r.temperature),
        humidity=None if pd.isna(r.humidity) else float(r.humidity), fan_level=int(r.fan_level),
        battery_soc=float(r.battery_soc), task_id=str(r.task_id) if not pd.isna(r.task_id) else '',
        decision_id=str(r.decision_id) if not pd.isna(r.decision_id) else '', robot_state=str(r.robot_state),
        valid_flag=bool(r.valid_flag), quality_code=QualityCode(str(r.quality_code)), source_type=SourceType(str(r.source_type)),
        episode_id=str(r.episode_id), scenario_id=str(r.scenario_id),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Build processed datasets from existing simulation data.')
    add_root_argument(parser)
    args = parser.parse_args(argv)
    root = resolve_cli_root(args)
    raw = pd.read_csv(root/'data/simulation/env_samples.csv')
    raw['timestamp'] = raw['timestamp'].astype(str)
    teacher = RiskTeacher()
    state_rows = []
    for episode_id, epdf in raw.groupby('episode_id', sort=False):
        builder = RegionStateBuilder()
        epdf = epdf.sort_values(['step_index','region_id'])
        for r in epdf.itertuples(index=False):
            st = builder.update(to_sample(r))
            row = st.feature_dict()
            row.update({
                'timestamp': st.timestamp.isoformat(), 'episode_id': st.episode_id, 'scenario_id': st.scenario_id,
                'step_index': int(r.step_index), 'region_id': st.region_id,
                'current_risk': teacher.current_risk(st), 'risk_confidence': teacher.confidence(st),
                'purifiable_fraction': teacher.purifiable_fraction(st),
            })
            state_rows.append(row)
    states = pd.DataFrame(state_rows)

    horizon_steps = int(load_yaml('config/system.yaml')['risk_horizon_seconds']) // int(load_yaml('config/system.yaml')['sample_interval_seconds'])
    states = states.sort_values(['episode_id','region_id','step_index']).reset_index(drop=True)
    states['future_risk'] = states.groupby(['episode_id','region_id'], sort=False)['current_risk'].shift(-horizon_steps)
    states = pd.get_dummies(states, columns=['region_id'], prefix='region', dtype=int)
    # Preserve canonical region id for joins.
    states['region_id'] = states[['region_A','region_B','region_C','region_D']].idxmax(axis=1).str.replace('region_','', regex=False)
    states = states.dropna(subset=['future_risk']).reset_index(drop=True)

    # Group split at episode level to prevent event leakage.
    episodes = states[['episode_id','scenario_id']].drop_duplicates().reset_index(drop=True)
    gss = GroupShuffleSplit(n_splits=1, train_size=0.70, random_state=20260909)
    tr_idx, rest_idx = next(gss.split(episodes, groups=episodes['episode_id']))
    train_eps = set(episodes.iloc[tr_idx]['episode_id'])
    rest = episodes.iloc[rest_idx].reset_index(drop=True)
    gss2 = GroupShuffleSplit(n_splits=1, train_size=0.5, random_state=20260910)
    va_idx, te_idx = next(gss2.split(rest, groups=rest['episode_id']))
    val_eps = set(rest.iloc[va_idx]['episode_id']); test_eps = set(rest.iloc[te_idx]['episode_id'])
    states['split'] = np.where(states.episode_id.isin(train_eps),'train',np.where(states.episode_id.isin(val_eps),'val','test'))

    out = root/'data/processed'; out.mkdir(parents=True, exist_ok=True)
    states.to_csv(out/'region_state_dataset.csv', index=False)
    states.to_csv(out/'risk_dataset.csv', index=False)
    manifest = {
        'dataset_version':'risk_dataset_v1', 'source':'SIMULATION', 'horizon_steps':horizon_steps,
        'train_episodes':sorted(train_eps), 'val_episodes':sorted(val_eps), 'test_episodes':sorted(test_eps),
        'n_rows':int(len(states)), 'split_counts':states['split'].value_counts().to_dict(),
        'leakage_rule':'episode_id is atomic across train/val/test'
    }
    (out/'risk_dataset_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(states['split'].value_counts().to_dict())

if __name__ == '__main__':
    main()
