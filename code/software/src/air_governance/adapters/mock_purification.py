from __future__ import annotations
from zhiyu_brain.adapters.base import PurificationAdapter


class MockPurificationAdapter(PurificationAdapter):
    def __init__(self):
        self.level = 0
        self.running = False
        self.healthy = True

    def set_fan_level(self, level: int) -> None:
        self.level = max(0, min(3, int(level)))

    def start_purification(self) -> None:
        if not self.healthy:
            raise RuntimeError('fan unhealthy')
        self.running = True
        if self.level == 0:
            self.level = 2

    def stop_purification(self) -> None:
        self.running = False
        self.level = 0

    def get_fan_state(self) -> dict:
        return {'running': self.running, 'fan_level': self.level}

    def is_fan_healthy(self) -> bool:
        return self.healthy
