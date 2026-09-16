"""Public ``air_governance`` package.

The eight Phase-1 algorithm/adapter modules are byte-frozen.  They still
contain their historical import spelling, so this package installs an
in-memory compatibility alias before loading them.  No legacy package
directory is shipped and all editable modules, commands and tests use the
``air_governance`` namespace.
"""

from __future__ import annotations

import importlib
import sys


_LEGACY_ROOT = "zhiyu_brain"
_FROZEN_MODULES = (
    "adapters.mock_navigation",
    "adapters.mock_purification",
    "decision.rule_policy",
    "models.ranker_model",
    "models.risk_model",
    "safety.supervisor",
    "task.manager",
    "task.state_machine",
)
_SHARED_MODULES = (
    "adapters.base",
    "common.config",
    "common.types",
    "logging.store",
)
_SUBPACKAGES = (
    "adapters",
    "common",
    "decision",
    "interfaces",
    "labels",
    "logging",
    "models",
    "safety",
    "sensing",
    "simulation",
    "state",
    "task",
)


def _install_frozen_import_aliases() -> None:
    """Keep frozen module bytes working without publishing a second package."""
    sys.modules.setdefault(_LEGACY_ROOT, sys.modules[__name__])
    for name in _SUBPACKAGES:
        module = importlib.import_module(f"{__name__}.{name}")
        sys.modules.setdefault(f"{_LEGACY_ROOT}.{name}", module)
    for name in _SHARED_MODULES + _FROZEN_MODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        sys.modules.setdefault(f"{_LEGACY_ROOT}.{name}", module)


_install_frozen_import_aliases()

__all__ = []
