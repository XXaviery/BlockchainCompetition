#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from scripts._paths import add_root_argument, resolve_cli_root
from services.gui_backend import GuiBackend
from services.report_exporter import ReportExporter


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description='Export report evidence from the existing runtime assets.')
    add_root_argument(parser)
    args=parser.parse_args(argv)
    root=resolve_cli_root(args)
    # Build report evidence through the same unified backend. Inject one MODEL_ERROR
    # decision to produce an actual logged fallback trace, then restore NORMAL.
    backend=GuiBackend(root, db_path=root/'outputs/logs/report_evidence.db', seed=20260999)
    backend.inject_safety_event('MODEL_ERROR')
    backend.step(force=True)  # SENSING
    backend.step(force=True)  # DECIDING -> RuleBasedPolicy fallback, logged
    backend.inject_safety_event('NORMAL')
    while backend.snapshot().final_state != 'COMPLETED':
        backend.step(force=True)
    result=ReportExporter(root,backend).export_all()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    backend.db.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
