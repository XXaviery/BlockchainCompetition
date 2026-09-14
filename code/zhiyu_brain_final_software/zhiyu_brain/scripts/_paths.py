"""Shared command-line path handling for the installed project scripts."""

from __future__ import annotations

import argparse
from pathlib import Path

from zhiyu_brain.common.config import resolve_project_root, set_active_project_root


def add_root_argument(parser: argparse.ArgumentParser) -> None:
    """Add the common optional project-root override to a CLI parser."""
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Zhiyu Brain asset root; defaults to automatic movable-root discovery.",
    )


def resolve_cli_root(args: argparse.Namespace) -> Path:
    """Resolve and validate the root selected by a command-line entry point."""
    return set_active_project_root(resolve_project_root(args.root))


def portable_path(path: Path, root: Path) -> str:
    """Serialize an in-project path without leaking the host checkout path."""
    resolved_path = Path(path).resolve()
    resolved_root = Path(root).resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError:
        # A deliberately external deployment path is not rewritten or guessed.
        return resolved_path.as_posix()
