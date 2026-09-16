#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json, shutil
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scripts._paths import add_root_argument, resolve_cli_root
from air_governance.common.config import load_yaml, resolve_project_root, set_active_project_root
from air_governance.models.risk_model import RiskModel
from air_governance.models.ranker_model import RankerModel, RANK_FEATURES
from air_governance.decision.rule_policy import RuleBasedPolicy
from air_governance.common.types import CandidateTask, Trend


def build_rank_dataset(root: str | Path | None = None) -> pd.DataFrame:
    root = resolve_project_root(root)
    set_active_project_root(root)
    states = pd.read_csv(root/'data/processed/region_state_dataset.csv')
    gt = pd.read_csv(root/'data/simulation/candidate_ground_truth.csv')
    risk_model = RiskModel.load(root/'models/risk')
    states['predicted_risk'] = risk_model.predict(states)
    cols = ['episode_id','scenario_id','step_index','region_id','current_risk','predicted_risk','abnormal_duration','data_age']
    merged = gt.merge(states[cols], on=['episode_id','scenario_id','step_index','region_id'], how='inner')
    # Waiting time approximates how long current risk has remained above task threshold within an episode/region.
    merged = merged.sort_values(['episode_id','region_id','step_index'])
    threshold = float(load_yaml('config/system.yaml')['min_task_risk'])
    waits = []
    counters = {}
    dt = float(load_yaml('config/system.yaml')['decision_interval_seconds']) * 3.0
    for r in merged.itertuples(index=False):
        key = (r.episode_id, r.region_id)
        counters[key] = counters.get(key, 0.0) + dt if r.current_risk >= threshold else 0.0
        waits.append(counters[key])
    merged['waiting_time'] = waits
    delta = merged['predicted_risk'] - merged['current_risk']
    tc = load_yaml('config/risk.yaml')['trend_classification']
    merged['trend_code'] = np.where(delta >= tc['rising_delta'], 1, np.where(delta <= tc['falling_delta'], -1, 0))

    rcfg = load_yaml('config/ranker.yaml')['utility']
    # Normalize counterfactual gain within each decision group; costs are also normalized to comparable scales.
    merged['risk_gain_norm'] = merged.groupby('decision_id')['counterfactual_risk_gain'].transform(
        lambda s: (s - s.min()) / (s.max() - s.min() + 1e-9))
    merged['response_cost'] = merged['estimated_move_time'] / (merged['estimated_move_time'].max() + 1e-9)
    merged['energy_cost'] = merged['estimated_energy'] / (merged['estimated_energy'].max() + 1e-9)
    merged['ground_truth_utility'] = (
        rcfg['alpha_risk_gain'] * merged['risk_gain_norm']
        - rcfg['beta_response_cost'] * merged['response_cost']
        - rcfg['gamma_energy_cost'] * merged['energy_cost']
        - rcfg['delta_switch_cost'] * merged['switch_cost']
    )
    # Grade by utility order inside the query group. Best=4, then 3,2,1; non-positive may become 0.
    def grades(g):
        order = g['ground_truth_utility'].rank(method='min', ascending=False).astype(int)
        grade = (5 - order).clip(lower=1)
        grade = np.where(g['ground_truth_utility'] <= 0, 0, grade)
        return pd.Series(grade, index=g.index)
    merged['relevance_grade'] = merged.groupby('decision_id', group_keys=False).apply(grades, include_groups=False).sort_index()

    # Use exactly the episode split from the risk dataset to prevent cross-stage leakage.
    split_map = states[['episode_id','split']].drop_duplicates().set_index('episode_id')['split'].to_dict()
    merged['split'] = merged['episode_id'].map(split_map)
    keep = ['decision_id','episode_id','scenario_id','step_index','region_id','split',
            'current_risk','predicted_risk','trend_code','abnormal_duration','waiting_time','data_age',
            'distance','estimated_move_time','estimated_energy','battery_soc','switch_cost','scene_weight','reachable',
            'counterfactual_risk_gain','ground_truth_utility','relevance_grade']
    merged = merged[keep].sort_values(['decision_id','region_id']).reset_index(drop=True)
    merged.to_csv(root/'data/processed/rank_dataset.csv', index=False)
    return merged


def rule_top_utility(frame: pd.DataFrame) -> float:
    policy = RuleBasedPolicy()
    chosen = []
    for _, g in frame.groupby('decision_id', sort=False):
        candidates=[]
        for r in g.itertuples(index=False):
            candidates.append(CandidateTask(
                decision_id=r.decision_id, region_id=r.region_id, current_risk=float(r.current_risk),
                predicted_risk=float(r.predicted_risk), trend=Trend.RISING if r.trend_code>0 else Trend.FALLING if r.trend_code<0 else Trend.STABLE,
                abnormal_duration=float(r.abnormal_duration), waiting_time=float(r.waiting_time), data_age=float(r.data_age),
                distance=float(r.distance), estimated_move_time=float(r.estimated_move_time), estimated_energy=float(r.estimated_energy),
                battery_soc=float(r.battery_soc), switch_cost=float(r.switch_cost), scene_weight=float(r.scene_weight), reachable=bool(r.reachable),
                ground_truth_utility=float(r.ground_truth_utility), relevance_grade=int(r.relevance_grade)
            ))
        top = policy.rank(candidates)[0]
        chosen.append(top.ground_truth_utility)
    return float(np.mean(chosen)) if chosen else 0.0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Train the ranker from the existing processed datasets.')
    add_root_argument(parser)
    args = parser.parse_args(argv)
    root = resolve_cli_root(args)
    df = build_rank_dataset(root)
    train, test = df[df.split=='train'].copy(), df[df.split=='test'].copy()
    model = RankerModel(); model.fit(train)
    metrics = model.evaluate(test)
    scores = model.predict(test); test['task_score'] = scores
    ranker_utility=[]
    for _,g in test.groupby('decision_id', sort=False):
        ranker_utility.append(float(g.loc[g.task_score.idxmax(),'ground_truth_utility']))
    metrics['mean_selected_utility_ranker'] = float(np.mean(ranker_utility))
    metrics['mean_selected_utility_rule'] = rule_top_utility(test)
    rule_utility = float(metrics['mean_selected_utility_rule'])
    rank_utility = float(metrics['mean_selected_utility_ranker'])
    metrics['relative_improvement_percent'] = ((rank_utility - rule_utility) / rule_utility * 100.0) if rule_utility != 0 else None
    model.save(root/'models/ranker', {'dataset_version':'rank_dataset_v1','metrics':metrics})
    shutil.copy2(root/'config/ranker.yaml', root/'models/ranker/config.yaml')
    (root/'outputs/metrics/rank_metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')

    # Trace all groups for report/replay.
    traces=[]
    for did,g in test.groupby('decision_id', sort=False):
        gg=g.sort_values('task_score',ascending=False)
        truth=g.sort_values('ground_truth_utility',ascending=False)
        traces.append({'decision_id':did,'ground_truth_order':truth.region_id.tolist(),'predicted_order':gg.region_id.tolist(),
                       'task_score':dict(zip(gg.region_id,gg.task_score.astype(float)))})
    (root/'outputs/decisions/rank_results.json').write_text(json.dumps(traces,ensure_ascii=False,indent=2),encoding='utf-8')

    # Ranking example.
    if len(traces):
        did=traces[0]['decision_id']; g=test[test.decision_id==did].sort_values('region_id')
        fig,ax=plt.subplots(figsize=(7,4)); x=np.arange(len(g)); width=0.36
        ax.bar(x-width/2,g['ground_truth_utility'],width,label='Ground-truth utility')
        ax.bar(x+width/2,g['task_score'],width,label='Ranker score')
        ax.set_xticks(x,g['region_id']); ax.set_xlabel('Region'); ax.set_title(f'Ranking Example: {did}'); ax.legend()
        fig.tight_layout(); fig.savefig(root/'outputs/figures/ranking_example.png',dpi=160); plt.close(fig)

    # Rule vs Ranker.
    fig,ax=plt.subplots(figsize=(5,4))
    ax.bar(['Rule baseline','XGBoost Ranker'],[metrics['mean_selected_utility_rule'],metrics['mean_selected_utility_ranker']])
    ax.set_ylabel('Mean ground-truth utility of selected task'); ax.set_title('Rule vs Ranker (Simulation Test Split)')
    fig.tight_layout(); fig.savefig(root/'outputs/figures/rule_vs_ranker.png',dpi=160); plt.close(fig)
    print(json.dumps(metrics,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
