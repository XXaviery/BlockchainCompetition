from __future__ import annotations

from pathlib import Path

from zhiyu_brain.common.config import load_yaml, project_path, resolve_project_root


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_explicit_root_resolves_only_from_project_markers():
    assert resolve_project_root(PROJECT_ROOT) == PROJECT_ROOT
    assert project_path('config/system.yaml', root=PROJECT_ROOT).is_file()


def test_yaml_loading_accepts_the_moved_root_explicitly():
    system = load_yaml('config/system.yaml', root=PROJECT_ROOT)
    logging = load_yaml('config/logging.yaml', root=PROJECT_ROOT)
    assert system['project_name'] == 'zhiyu_brain'
    assert logging['database'] == 'outputs/logs/brain.db'
