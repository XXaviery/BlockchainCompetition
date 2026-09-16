from __future__ import annotations
from pathlib import Path
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Sequence

from air_governance.common.config import load_yaml, resolve_project_root

TABLES = [
    'env_samples', 'region_states', 'risk_predictions', 'candidate_tasks',
    'rank_results', 'safety_events', 'navigation_events', 'purification_events',
    'task_state_events'
]

# Final unified schema. It keeps Phase-1 BrainLogStore.log(payload) as the only
# write API while exposing typed columns needed by GUI/replay/report evidence.
SCHEMA = {
    'env_samples': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        region_id TEXT, pose_x REAL, pose_y REAL, pose_yaw REAL,
        pm25 REAL, voc REAL, co2 REAL, temperature REAL, humidity REAL,
        fan_level INTEGER, battery_soc REAL, task_id TEXT, decision_id TEXT,
        robot_state TEXT, valid_flag INTEGER, quality_code TEXT, source_type TEXT,
        episode_id TEXT, scenario_id TEXT
    ''',
    'region_states': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        decision_id TEXT, region_id TEXT, current_risk REAL, predicted_risk REAL, trend TEXT,
        pm25 REAL, voc REAL, co2 REAL, temperature REAL, humidity REAL,
        abnormal_duration REAL, waiting_time REAL, data_age REAL, distance REAL,
        estimated_move_time REAL, estimated_energy REAL, switch_cost REAL,
        valid_flag INTEGER, quality_code TEXT
    ''',
    'risk_predictions': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        decision_id TEXT, region_id TEXT, current_risk REAL, predicted_risk REAL,
        future_risk REAL, trend TEXT, true_trend TEXT, predicted_trend TEXT, source_type TEXT
    ''',
    'candidate_tasks': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        decision_id TEXT, task_id TEXT, region_id TEXT,
        current_risk REAL, predicted_risk REAL, trend TEXT, abnormal_duration REAL,
        waiting_time REAL, data_age REAL, distance REAL, estimated_move_time REAL,
        estimated_energy REAL, battery_soc REAL, switch_cost REAL, scene_weight REAL,
        reachable INTEGER, task_score REAL, valid_flag INTEGER, safety_result TEXT,
        selected INTEGER, policy TEXT, rank_position INTEGER
    ''',
    'rank_results': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        decision_id TEXT, region_id TEXT, task_score REAL, rank_position INTEGER,
        selected INTEGER, policy TEXT
    ''',
    'safety_events': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        decision_id TEXT, task_id TEXT, region_id TEXT, region TEXT,
        event_type TEXT, input_task TEXT, action TEXT, result TEXT, reason TEXT, fallback TEXT
    ''',
    'navigation_events': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        decision_id TEXT, task_id TEXT, region_id TEXT, region TEXT,
        target_region TEXT, target_pose TEXT, event TEXT, status TEXT,
        success INTEGER, arrived INTEGER, source_type TEXT
    ''',
    'purification_events': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        decision_id TEXT, task_id TEXT, region_id TEXT, region TEXT,
        event TEXT, fan_level INTEGER, pre_risk REAL, post_risk REAL, source_type TEXT
    ''',
    'task_state_events': '''
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL, event_key TEXT, payload_json TEXT NOT NULL,
        decision_id TEXT, task_id TEXT, region_id TEXT, region TEXT,
        old_state TEXT, new_state TEXT, state TEXT, event TEXT, reason TEXT, cycle INTEGER
    ''',
}


class BrainLogStore:
    def __init__(self, db_path: str | Path | None = None, root: str | Path | None = None):
        self.root = resolve_project_root(root)
        cfg = load_yaml('config/logging.yaml', root=self.root)
        p = Path(db_path or cfg['database'])
        if not p.is_absolute():
            p = self.root / p
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = p
        self.db_path = p  # GUI/replay compatibility; same single database.
        self.conn = sqlite3.connect(p)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        typed_required = {
            'env_samples':'region_id', 'region_states':'decision_id', 'risk_predictions':'decision_id',
            'candidate_tasks':'decision_id', 'rank_results':'decision_id', 'safety_events':'event_type',
            'navigation_events':'event', 'purification_events':'event', 'task_state_events':'state',
        }
        for table in TABLES:
            existing = {r['name'] for r in cur.execute(f'PRAGMA table_info({table})').fetchall()}
            # Phase-1 and Phase-2 used incompatible schemas for the same table names.
            # Final engineering uses only this schema; stale copied tables are rebuilt.
            base_required = {'id', 'timestamp', 'event_key', 'payload_json'}
            if existing and (not base_required.issubset(existing) or typed_required[table] not in existing):
                cur.execute(f'DROP TABLE {table}')
            cur.execute(f'CREATE TABLE IF NOT EXISTS {table} ({SCHEMA[table]})')
        self.conn.commit()

    def reset(self) -> None:
        for table in TABLES:
            self.conn.execute(f'DELETE FROM {table}')
        self.conn.commit()

    def log(self, table: str, payload: dict[str, Any], event_key: str = '', timestamp: datetime | None = None) -> None:
        if table not in TABLES:
            raise KeyError(table)
        ts_raw = timestamp or payload.get('timestamp') or datetime.now(timezone.utc)
        ts = ts_raw.isoformat() if hasattr(ts_raw, 'isoformat') else str(ts_raw)
        payload_clean = dict(payload)
        payload_clean.pop('timestamp', None)
        columns = {r['name'] for r in self.conn.execute(f'PRAGMA table_info({table})').fetchall()}
        row: dict[str, Any] = {
            'timestamp': ts,
            'event_key': event_key,
            'payload_json': json.dumps(payload, ensure_ascii=False, default=str),
        }
        aliases = {
            'source': 'source_type',
            'target_region': 'target_region',
        }
        for key, value in payload_clean.items():
            col = aliases.get(key, key)
            if col not in columns:
                continue
            if isinstance(value, (dict, list, tuple, set)):
                value = json.dumps(value, ensure_ascii=False, default=str)
            elif isinstance(value, bool):
                value = int(value)
            row[col] = value
        # Helpful aliases for uniform replay queries.
        if 'region' in columns and 'region' not in row:
            row['region'] = payload.get('region_id') or payload.get('target_region')
        if 'region_id' in columns and 'region_id' not in row:
            row['region_id'] = payload.get('region') or payload.get('target_region')
        if table == 'task_state_events':
            row.setdefault('state', payload.get('new_state') or payload.get('state'))
            row.setdefault('event', payload.get('reason') or payload.get('event'))
        keys = list(row)
        self.conn.execute(
            f"INSERT INTO {table}({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
            [row[k] for k in keys],
        )
        self.conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def table_count(self, table: str) -> int:
        if table not in TABLES:
            raise KeyError(table)
        return int(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])

    def export_csv(self, out_dir: str | Path | None = None) -> None:
        import pandas as pd
        cfg = load_yaml('config/logging.yaml', root=self.root)
        p = Path(out_dir or cfg['csv_export_dir'])
        if not p.is_absolute():
            p = self.root / p
        p.mkdir(parents=True, exist_ok=True)
        for table in TABLES:
            df = pd.read_sql_query(f'SELECT * FROM {table}', self.conn)
            df.to_csv(p / f'{table}.csv', index=False)

    def close(self) -> None:
        self.conn.close()
