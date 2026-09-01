"""发送队列测试：顺序性、优先级、异常处理与状态监控。"""

import threading
import time

from bot.send_queue import (
    PRIORITY_AT,
    PRIORITY_NORMAL,
    PRIORITY_PROACTIVE,
    SendQueue,
)


class FakeClient:
    """记录发送调用顺序的假客户端，可注入异常。

    模拟真实 WeChatClient.send：按换行拆分为多条子消息逐条发送。
    """

    def __init__(self, fail_on=None, delay=0.0):
        self.sent = []
        self.fail_on = set(fail_on or [])
        self.delay = delay
        self._lock = threading.Lock()

    def send(self, chat_name, text):
        for part in [p.strip() for p in (text or "").splitlines() if p.strip()]:
            if self.delay:
                time.sleep(self.delay)
            with self._lock:
                if part in self.fail_on:
                    self.fail_on.discard(part)
                    raise RuntimeError("模拟发送失败")
                self.sent.append((chat_name, part))


def test_fifo_order_same_priority():
    client = FakeClient()
    q = SendQueue(client, maxsize=50)
    for i in range(5):
        q.submit("群A", f"m{i}", PRIORITY_NORMAL)
    q.wait_done(timeout=5)
    assert [t for _, t in client.sent] == [f"m{i}" for i in range(5)]


def test_priority_ordering():
    client = FakeClient()
    q = SendQueue(client, maxsize=50)
    q.submit("群A", "proactive", PRIORITY_PROACTIVE)
    q.submit("群A", "normal", PRIORITY_NORMAL)
    q.submit("群A", "at", PRIORITY_AT)
    q.wait_done(timeout=5)
    # 高优先级（数值小）先发；@回复 > 普通回复 > 主动发言
    assert [t for _, t in client.sent] == ["at", "normal", "proactive"]


def test_no_interleaving_single_worker():
    """单发送线程：一条完整发完才开始下一条（顺序 + 无并发穿插）。"""
    client = FakeClient(delay=0.02)
    q = SendQueue(client, maxsize=50)
    q.submit("群A", "a1\na2", PRIORITY_NORMAL)
    q.submit("群A", "b1", PRIORITY_NORMAL)
    q.wait_done(timeout=5)
    # a1/a2（拆分后）先于 b1，且全程单线程顺序发送
    assert [t for _, t in client.sent] == ["a1", "a2", "b1"]


def test_exception_not_blocking():
    client = FakeClient(fail_on=["bad"])
    q = SendQueue(client, maxsize=50)
    q.submit("群A", "bad", PRIORITY_NORMAL)
    q.submit("群A", "good", PRIORITY_NORMAL)
    q.wait_done(timeout=5)
    assert client.sent == [("群A", "good")]
    st = q.stats()
    assert st["failed"] == 1 and st["sent"] == 1


def test_stats_and_queue_full():
    class BlockedClient:
        def __init__(self):
            self.event = threading.Event()

        def send(self, chat_name, text):
            self.event.wait(timeout=5)

    client = BlockedClient()
    q = SendQueue(client, maxsize=2)
    # 容量2：无论 worker 取走几条，提交5条必然溢出
    for i in range(5):
        q.submit("群A", f"m{i}")
    st = q.stats()
    assert st["dropped"] >= 2
    client.event.set()
    q.wait_done(timeout=5)
    st = q.stats()
    # 已发送 + 丢弃 = 全部提交数，失败为0
    assert st["failed"] == 0
    assert st["sent"] == 5 - st["dropped"]


def test_empty_text_ignored():
    client = FakeClient()
    q = SendQueue(client, maxsize=50)
    q.submit("群A", "")
    q.submit("群A", None)
    q.wait_done(timeout=5)
    assert client.sent == []
