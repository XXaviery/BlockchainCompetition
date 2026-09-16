from __future__ import annotations

import json
from pathlib import Path

from air_governance.common.config import resolve_project_root
from air_governance.runtime.software_loop import UnifiedBrainRuntime


class GuiBackend(UnifiedBrainRuntime):
    """GUI/service facade over the final unified Phase-1 core runtime."""

    def __init__(self, root: str | Path | None = None, db_path: str | Path | None = None, seed: int = 20260999):
        super().__init__(root=resolve_project_root(root), db_path=db_path, seed=seed)
        self.evidence = {'phase1_metrics': self._load_gui_metrics()}

    def _load_gui_metrics(self) -> dict:
        risk_path = self.root / 'outputs/metrics/risk_metrics.json'
        rank_path = self.root / 'outputs/metrics/rank_metrics.json'
        risk = json.loads(risk_path.read_text(encoding='utf-8'))
        rank = json.loads(rank_path.read_text(encoding='utf-8'))
        return {
            'risk_mae': float(risk['mae']),
            'risk_rmse': float(risk['rmse']),
            'risk_r2': float(risk['r2']),
            'trend_accuracy': float(risk['trend_accuracy']),
            'risk_test_samples': int(risk.get('n_samples', risk.get('test_samples', 0))),
            'ranker_ndcg_at_3': float(rank['ndcg@3']),
            'ranker_precision_at_3': float(rank['precision@3']),
            'ranker_top1_accuracy': float(rank['top1_accuracy']),
            'ranker_test_query_groups': int(rank['n_groups']),
            'ranker_mean_utility': float(rank['mean_selected_utility_ranker']),
            'rule_mean_utility': float(rank['mean_selected_utility_rule']),
        }
