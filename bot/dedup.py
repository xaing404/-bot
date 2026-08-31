"""消息指纹去重：带时间窗口的指纹登记表。

用于两层防重：
1. 收到的消息：id 指纹 + 内容指纹，防止 UI 轮询重放历史消息
2. 已发送的消息：发送成功后登记内容指纹，防止重复处理/自环
"""

import time
from collections import OrderedDict


class MessageDedup:
    def __init__(self, window: float = 1800.0, capacity: int = 2000):
        self.window = float(window)
        self.capacity = int(capacity)
        self._seen: OrderedDict = OrderedDict()  # fingerprint -> ts

    def filter_new(self, *fingerprints: str) -> bool:
        """所有指纹都未登记过才返回 True，并立即全部登记；任一命中返回 False。"""
        now = time.time()
        self._evict(now)
        if any(fp in self._seen for fp in fingerprints):
            return False
        for fp in fingerprints:
            self._seen[fp] = now
        self._trim()
        return True

    def mark(self, *fingerprints: str):
        """主动登记指纹（如已成功发送的消息内容）。"""
        now = time.time()
        self._evict(now)
        for fp in fingerprints:
            self._seen[fp] = now
        self._trim()

    def _evict(self, now: float):
        deadline = now - self.window
        while self._seen:
            _, ts = next(iter(self._seen.items()))
            if ts >= deadline:
                break
            self._seen.popitem(last=False)

    def _trim(self):
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)

    def __len__(self) -> int:
        return len(self._seen)
