#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json, shutil
import pandas as pd
import matplotlib.pyplot as plt
from scripts._paths import add_root_argument, resolve_cli_root
from air_governance.models.risk_model import RiskModel
from air_governance.models.explain import save_feature_importance, save_shap_summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Train the risk model from the existing dataset.')
    add_root_argument(parser)
    args = parser.parse_args(argv)
    root = resolve_cli_root(args)
    df = pd.read_csv(root/'data/processed/risk_dataset.csv')
    train, val, test = [df[df.split == s].copy() for s in ('train','val','test')]
    model = RiskModel()
    model.fit(train, val)
    metrics = model.evaluate(test)
    meta = {'dataset_version':'risk_dataset_v1','metrics':metrics}
    model.save(root/'models/risk', meta)
    shutil.copy2(root/'config/risk.yaml', root/'models/risk/config.yaml')
    (root/'outputs/metrics/risk_metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')

    pred = model.predict(test)
    fig, ax = plt.subplots(figsize=(8,5))
    n = min(350, len(test))
    ax.plot(range(n), test['future_risk'].to_numpy()[:n], label='Ground truth')
    ax.plot(range(n), pred[:n], label='Prediction')
    ax.set_xlabel('Test sample'); ax.set_ylabel('Risk'); ax.set_title('Short-horizon Risk Prediction')
    ax.legend(); fig.tight_layout(); fig.savefig(root/'outputs/figures/risk_prediction.png', dpi=160); plt.close(fig)

    save_feature_importance(model.model, model.feature_columns,
        root/'outputs/figures/feature_importance.csv', root/'outputs/figures/feature_importance.png')
    save_shap_summary(model.model, test[model.feature_columns], root/'outputs/figures/shap_summary.png')
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
