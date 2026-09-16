#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from scripts._paths import add_root_argument, ensure_runtime_dirs, portable_path, resolve_cli_root
from air_governance.runtime.software_loop import UnifiedBrainRuntime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Run the Air Governance software-in-the-loop demo.')
    add_root_argument(parser)
    args = parser.parse_args(argv)
    root = resolve_cli_root(args)
    ensure_runtime_dirs(root)
    for required in (root/'models/risk/model.json', root/'models/ranker/model.json'):
        if not required.exists():
            raise SystemExit('Models missing; run training pipeline first.')
    runtime=UnifiedBrainRuntime(root, db_path=root/'outputs/logs/brain.db', seed=20260999)
    snap=runtime.run_demo(3)
    runtime.db.export_csv()
    (root/'outputs/logs/system_snapshot.json').write_text(json.dumps(snap.to_dict(),ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    result={'completed_cycles':snap.completed_cycles,'final_state':snap.final_state,'db':portable_path(runtime.db.db_path, root),'source_type':snap.source_type}
    (root/'outputs/metrics/run_demo_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'completed_cycles={snap.completed_cycles}; final_state={snap.final_state}; log_db={portable_path(runtime.db.db_path, root)}')
    runtime.db.close()

if __name__=='__main__': main()
