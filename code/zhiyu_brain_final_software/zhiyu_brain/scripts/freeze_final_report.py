#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json, re
from scripts._paths import add_root_argument, resolve_cli_root


def read_json(root, rel):
    return json.loads((root/rel).read_text(encoding='utf-8'))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Materialize the existing final metrics summary.')
    add_root_argument(parser)
    args = parser.parse_args(argv)
    root = resolve_cli_root(args)
    risk=read_json(root, 'outputs/metrics/risk_metrics.json')
    rank=read_json(root, 'outputs/metrics/rank_metrics.json')
    demo=read_json(root, 'outputs/metrics/run_demo_result.json')
    headless=read_json(root, 'outputs/metrics/gui_headless_result.json')
    pytest_text=(root/'outputs/metrics/pytest_output.txt').read_text(encoding='utf-8')
    m=re.search(r'(\d+) passed',pytest_text)
    if not m:
        raise SystemExit('Could not parse pytest passed count')
    passed=int(m.group(1))
    rule=float(rank['mean_selected_utility_rule']); rank_u=float(rank['mean_selected_utility_ranker'])
    rel=(rank_u-rule)/rule*100.0
    out={
      'source_type':'SIMULATION / SOFTWARE-IN-THE-LOOP',
      'risk':{
        'mae':float(risk['mae']),'rmse':float(risk['rmse']),'r2':float(risk['r2']),
        'trend_accuracy':float(risk['trend_accuracy']),'test_samples':int(risk['n_samples'])
      },
      'ranker':{
        'ndcg_at_3':float(rank['ndcg@3']),'precision_at_3':float(rank['precision@3']),
        'top1_accuracy':float(rank['top1_accuracy']),'test_query_groups':int(rank['n_groups'])
      },
      'policy_comparison':{
        'ranker_mean_utility':rank_u,'rule_mean_utility':rule,'relative_improvement_percent':rel
      },
      'closed_loop':{
        'completed_cycles':int(demo['completed_cycles']),'final_state':demo['final_state']
      },
      'gui_headless':{
        'completed_cycles':int(headless['completed_cycles']),'final_state':headless['final_state']
      },
      'tests':{'passed':passed}
    }
    (root/'outputs/final_report_metrics.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
