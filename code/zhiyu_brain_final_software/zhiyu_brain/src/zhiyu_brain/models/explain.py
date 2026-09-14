from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import shap

from zhiyu_brain.common.config import project_path


def save_feature_importance(model, feature_names: list[str], csv_path: str | Path, png_path: str | Path) -> pd.DataFrame:
    imp = np.asarray(model.feature_importances_, dtype=float)
    df = pd.DataFrame({'feature': feature_names, 'importance': imp}).sort_values('importance', ascending=False)
    cp = project_path(csv_path); pp = project_path(png_path)
    cp.parent.mkdir(parents=True, exist_ok=True); pp.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cp, index=False)
    top = df.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(top['feature'], top['importance'])
    ax.set_xlabel('Importance')
    ax.set_title('XGBoost Feature Importance')
    fig.tight_layout(); fig.savefig(pp, dpi=160); plt.close(fig)
    return df


def save_shap_summary(model, X: pd.DataFrame, out_path: str | Path, max_samples: int = 400) -> None:
    p = project_path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    xs = X.sample(min(max_samples, len(X)), random_state=20260909) if len(X) else X
    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(xs)
    shap.summary_plot(values, xs, show=False, max_display=15)
    plt.tight_layout(); plt.savefig(p, dpi=160, bbox_inches='tight'); plt.close()


def decision_explanation(ranker_model, row: pd.DataFrame, selected_region: str, decision_id: str, out_path: str | Path) -> dict:
    features = list(row.columns)
    explainer = shap.TreeExplainer(ranker_model)
    vals = np.asarray(explainer.shap_values(row)).reshape(-1)
    pairs = sorted(zip(features, vals), key=lambda x: abs(x[1]), reverse=True)
    positive = [{'feature': k, 'contribution': float(v)} for k,v in pairs if v > 0][:5]
    negative = [{'feature': k, 'contribution': float(v)} for k,v in pairs if v < 0][:5]
    result = {'decision_id': decision_id, 'selected_region': selected_region, 'positive_factors': positive, 'negative_factors': negative}
    p = project_path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
