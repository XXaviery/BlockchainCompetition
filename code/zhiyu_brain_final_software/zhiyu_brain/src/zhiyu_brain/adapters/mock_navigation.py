from __future__ import annotations
import math
from zhiyu_brain.adapters.base import NavigationAdapter


class MockNavigationAdapter(NavigationAdapter):
    def __init__(self, pose=(0.0, 0.0, 0.0), unreachable: set[str] | None = None):
        self.pose = tuple(map(float, pose))
        self.target = self.pose
        self.target_region = ''
        self._arrived = True
        self._localization_ok = True
        self.unreachable = unreachable or set()

    def navigate_to(self, target_region: str, target_pose: tuple[float, float, float]) -> bool:
        if target_region in self.unreachable or not self._localization_ok:
            self._arrived = False
            return False
        self.target_region = target_region
        self.target = tuple(map(float, target_pose))
        self.pose = self.target
        self._arrived = True
        return True

    def cancel_navigation(self) -> None:
        self._arrived = False

    def get_pose(self) -> tuple[float, float, float]:
        return self.pose

    def get_distance_to_target(self) -> float:
        return math.hypot(self.pose[0] - self.target[0], self.pose[1] - self.target[1])

    def is_arrived(self) -> bool:
        return self._arrived

    def is_localization_ok(self) -> bool:
        return self._localization_ok

    def is_reachable(self, target_region: str) -> bool:
        return target_region not in self.unreachable
