#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from scripts._paths import add_root_argument, portable_path, resolve_cli_root
from services.gui_backend import GuiBackend


def main() -> int:
    parser=argparse.ArgumentParser(description='智驭新风上位机与可视化演示系统')
    parser.add_argument('--demo',action='store_true')
    parser.add_argument('--demo-headless',action='store_true')
    add_root_argument(parser)
    args=parser.parse_args()
    root=resolve_cli_root(args)
    backend=GuiBackend(root)
    if args.demo_headless:
        snap=backend.run_demo(3)
        backend.db.export_csv()
        result={'mode':snap.source_type,'completed_cycles':snap.completed_cycles,'final_state':snap.final_state,
                'decision_id':snap.robot.decision_id if snap.robot else '', 'task_id':snap.robot.task_id if snap.robot else '',
                'db':portable_path(backend.db.db_path, root)}
        (root/'outputs/metrics/gui_headless_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if snap.completed_cycles==3 and snap.final_state=='COMPLETED' else 2
    try:
        from ui.main_window import MainWindow
        MainWindow(backend,auto_demo=args.demo).run()
    except Exception as e:
        print(f'GUI startup failed: {e}',file=sys.stderr)
        print('Use --demo-headless for display-free execution.',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
