from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Dict, List


class ReplayService:
    """Read-only replay over the unified BrainLogStore SQLite schema."""
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _query(self, sql: str, params=()) -> List[Dict]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    def task_ids(self) -> List[str]:
        rows = self._query("SELECT DISTINCT task_id FROM task_state_events WHERE COALESCE(task_id,'') <> '' ORDER BY task_id")
        return [r['task_id'] for r in rows]

    def task_timeline(self, task_id: str) -> List[Dict]:
        return self._query(
            "SELECT timestamp, task_id, decision_id, COALESCE(region,region_id,'') AS region, "
            "COALESCE(event,reason,'') AS event, COALESCE(state,new_state,'') AS state, cycle "
            "FROM task_state_events WHERE task_id=? ORDER BY id", (task_id,)
        )

    def full_timeline(self) -> List[Dict]:
        return self._query(
            "SELECT timestamp, task_id, decision_id, COALESCE(region,region_id,'') AS region, "
            "COALESCE(event,reason,'') AS event, COALESCE(state,new_state,'') AS state, cycle "
            "FROM task_state_events ORDER BY id"
        )
