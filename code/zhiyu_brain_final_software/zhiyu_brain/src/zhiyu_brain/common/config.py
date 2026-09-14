from __future__ import annotations
from contextvars import ContextVar
import os
from pathlib import Path
from typing import Any, Iterator
import yaml

PROJECT_ROOT_ENV = 'ZHIYU_BRAIN_ROOT'
_ROOT_MARKERS = (
    Path('config/system.yaml'),
    Path('models/risk/model.json'),
    Path('models/ranker/model.json'),
)
_ACTIVE_PROJECT_ROOT: ContextVar[Path | None] = ContextVar(
    'zhiyu_brain_active_project_root', default=None
)


def _is_project_root(path: Path) -> bool:
    return path.is_dir() and all((path / marker).is_file() for marker in _ROOT_MARKERS)


def _validated_root(root: str | Path, label: str = 'Zhiyu Brain root') -> Path:
    resolved = Path(root).expanduser().resolve()
    if not _is_project_root(resolved):
        raise FileNotFoundError(f'Invalid {label}: {resolved}')
    return resolved


def _candidate_roots(anchor: Path) -> Iterator[Path]:
    current = anchor.resolve()
    if current.is_file():
        current = current.parent
    yield current
    yield from current.parents


def set_active_project_root(root: str | Path) -> Path:
    """Set the process-local asset root used by config and runtime helpers.

    The active root lets a caller run a copied checkout while the package is
    already imported. It changes path resolution only; it does not alter any
    model, policy, safety, state-machine, or adapter behavior.
    """
    resolved = _validated_root(root)
    _ACTIVE_PROJECT_ROOT.set(resolved)
    return resolved


def resolve_project_root(root: str | Path | None = None) -> Path:
    """Resolve the movable runtime-asset root without relying on the launch cwd."""
    if root is not None:
        return _validated_root(root)

    active = _ACTIVE_PROJECT_ROOT.get()
    if active is not None:
        return active

    configured = os.environ.get(PROJECT_ROOT_ENV)
    if configured:
        return _validated_root(configured, f'{PROJECT_ROOT_ENV} target')

    seen: set[Path] = set()
    for anchor in (Path(__file__), Path.cwd()):
        for candidate in _candidate_roots(anchor):
            if candidate in seen:
                continue
            seen.add(candidate)
            if _is_project_root(candidate):
                return candidate
    raise RuntimeError(
        f'Cannot locate the Zhiyu Brain project root; set {PROJECT_ROOT_ENV}.'
    )


def _import_time_root() -> Path:
    """Keep imports usable before a CLI has parsed its explicit ``--root``."""
    try:
        return resolve_project_root()
    except RuntimeError:
        # Runtime entry points validate and activate their real root before
        # loading assets.  This fallback only keeps installed modules
        # importable from a foreign working directory.
        return Path.cwd().resolve()


PROJECT_ROOT = _import_time_root()


def project_path(path: str | Path, root: str | Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return resolve_project_root(root) / candidate


def load_yaml(path: str | Path, root: str | Path | None = None) -> dict[str, Any]:
    p = project_path(path, root=root)
    with p.open('r', encoding='utf-8') as f:
        return yaml.safe_load(f)
