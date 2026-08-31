"""请求频率限制：最小间隔限流器，保证对 AI 接口的调用不超过设定频率。"""

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
