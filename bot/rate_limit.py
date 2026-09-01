"""请求频率限制：AI 接口全局限流器 + 每场景回复间隔限流器（防刷屏）。"""

import threading
import time


class RateLimiter:
    def __init__(self, min_interval: float = 2.0):
        self.min_interval = max(0.0, float(min_interval))
        self._last = 0.0

    def wait(self):
        """阻塞直到允许下一次请求。"""
        now = time.monotonic()
        remaining = self._last + self.min_interval - now
        if remaining > 0:
            time.sleep(remaining)
        self._last = time.monotonic()

    def reset(self):
        self._last = 0.0


class ScenarioThrottle:
    """按场景（group:xxx / private:xxx）限制最小回复间隔，防刷屏。

    与 RateLimiter 的区别：RateLimiter 限制对 AI 接口的全局请求频率，
    本类限制同一场景两次"发出回复"的最小间隔（私聊/群聊可分别配置）。
    """

    def __init__(self, private_interval: float = 30.0, group_interval: float = 60.0):
        self.private_interval = max(0.0, float(private_interval))
        self.group_interval = max(0.0, float(group_interval))
        self._last: dict = {}
        self._lock = threading.Lock()

    def allow(self, scenario_key: str) -> bool:
        """该场景现在是否允许发出回复。"""
        interval = self.group_interval if scenario_key.startswith("group:") else self.private_interval
        with self._lock:
            last = self._last.get(scenario_key, 0.0)
            return (time.monotonic() - last) >= interval

    def mark(self, scenario_key: str):
        """记录该场景刚刚发出了一次回复。"""
        with self._lock:
            self._last[scenario_key] = time.monotonic()
