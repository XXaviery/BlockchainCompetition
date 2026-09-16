from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from zhiyu_brain.common.types import TaskState
from zhiyu_brain.logging.store import BrainLogStore


class TaskStateMachine:
    def __init__(self, store: BrainLogStore | None = None):
        self.state = TaskState.IDLE
        self.store = store
        self.history: list[dict] = []

    def transition(self, new_state: TaskState, reason: str, task_id: str = '', **context: Any) -> None:
        event = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'task_id': task_id,
            'old_state': self.state.value,
            'new_state': new_state.value,
            'reason': reason,
            **context,
        }
        self.history.append(event)
        if self.store:
            self.store.log('task_state_events', event, event_key=task_id)
        self.state = new_state
