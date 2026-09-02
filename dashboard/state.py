"""线程安全的运行时状态容器。

机器人主循环创建的组件（send_queue / contexts / handler / store / cfg）
通过 SharedState 传递给 Dashboard 线程，所有读取加锁，避免并发竞争。
"""

import threading
import time


class SharedState:
    """持有机器人运行时组件引用，供 Dashboard API 只读访问。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._components = {}
        self._start_time = time.time()

    def bind(self, **components):
        """绑定运行时组件：cfg, send_queue, contexts, handler, store, engine。"""
        with self._lock:
            self._components.update(components)

    def get(self, name: str, default=None):
        with self._lock:
            return self._components.get(name, default)

    @property
    def components(self) -> dict:
        with self._lock:
            return dict(self._components)

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time
