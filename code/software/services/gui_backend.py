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
        risk = self._read_metrics(risk_path, self.root / 'models/risk/model_metadata.json')
        rank = self._read_metrics(rank_path, self.root / 'models/ranker/model_metadata.json')
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

    @staticmethod
    def _read_metrics(output_path: Path, metadata_path: Path) -> dict:
        """Use run outputs when present, otherwise the frozen model metadata.

        Output directories are intentionally ignored from Git and ZIP packages.
        A clean clone must still be able to open the GUI, so the fallback reads
        the metrics already stored alongside the immutable model assets without
        recomputing or changing any metric value.
        """
        path = output_path if output_path.is_file() else metadata_path
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload.get('metrics', payload)
