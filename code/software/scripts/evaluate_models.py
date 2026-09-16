#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
import pandas as pd
from scripts._paths import add_root_argument, ensure_runtime_dirs, resolve_cli_root
from air_governance.models.risk_model import RiskModel
from air_governance.models.ranker_model import RankerModel


def main(argv: list[str] | None = None):
    parser=argparse.ArgumentParser(description='Evaluate the frozen Air Governance models without training.')
    add_root_argument(parser)
    args=parser.parse_args(argv)
    root=resolve_cli_root(args)
    ensure_runtime_dirs(root)
    risk_df=pd.read_csv(root/'data/processed/risk_dataset.csv'); risk_test=risk_df[risk_df.split=='test']
    rank_df=pd.read_csv(root/'data/processed/rank_dataset.csv'); rank_test=rank_df[rank_df.split=='test']
    risk=RiskModel.load(root/'models/risk'); ranker=RankerModel.load(root/'models/ranker')
    out={'risk':risk.evaluate(risk_test),'ranker':ranker.evaluate(rank_test)}
    (root/'outputs/metrics/evaluation_summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
