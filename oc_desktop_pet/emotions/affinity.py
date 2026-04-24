"""好感度系统 - 管理好感度值、等级、隐藏台词解锁"""
from ..persistence.defaults import (
    AFFINITY_MIN, AFFINITY_MAX, AFFINITY_THRESHOLDS,
    AFFINITY_LEVEL_LABELS, HIDDEN_DIALOGUES,
)


class AffinityManager:
    """封装好感度相关逻辑，替代全局 affinity 变量。"""

    def __init__(self, state: dict):
        self._state = state

    @property
    def value(self) -> int:
        v = self._state.get("affinity", 0)
        return max(AFFINITY_MIN, min(AFFINITY_MAX, int(v)))

    @value.setter
    def value(self, val: int):
        self._state["affinity"] = max(AFFINITY_MIN, min(AFFINITY_MAX, int(val)))

    def increase(self, val: int, reason: str = "") -> int:
        """增加好感度，返回旧值用于解锁检测。"""
        old = self.value
        self.value = old + val
        return old

    @property
    def level(self) -> str:
        """当前好感度等级文字。"""
        v = self.value
        label = "陌生"
        for threshold in AFFINITY_THRESHOLDS:
            if v >= threshold:
                label = AFFINITY_LEVEL_LABELS.get(threshold, label)
        return label

    @property
    def level_value(self) -> int:
        """当前好感度对应的阈值（用于等级比较）。"""
        v = self.value
        result = 0
        for threshold in AFFINITY_THRESHOLDS:
            if v >= threshold:
                result = threshold
        return result

    def check_unlocks(self, old_value: int, new_value: int) -> list[str]:
        """检查是否有新的好感度等级解锁，返回解锁的台词列表。"""
        unlocked_dialogues = []
        unlocked_list = self._state.setdefault("affinity_unlocked", [])
        for threshold in AFFINITY_THRESHOLDS:
            if old_value < threshold <= new_value and threshold not in unlocked_list:
                unlocked_list.append(threshold)
                if threshold in HIDDEN_DIALOGUES:
                    unlocked_dialogues.extend(HIDDEN_DIALOGUES[threshold])
        return unlocked_dialogues

    def current_unlocked_level(self) -> int:
        """当前已解锁的最高好感度等级。"""
        unlocked = self._state.get("affinity_unlocked", [])
        return max(unlocked) if unlocked else 0
