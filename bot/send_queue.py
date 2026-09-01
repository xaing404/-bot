"""消息发送队列：所有回复统一入队，由专职线程按序串行发送。

解决的问题：线程池并发生成多条回复时，发送阶段在场景锁之外，
多条回复（含拆分的子消息）可能乱序或互相穿插。

机制：
- 优先级：@回复 > 普通/私聊回复 > 主动发言（数值越小越优先）
- 顺序性：同优先级按入队顺序先进先出；单线程逐条发送，
  一条消息（含多段子消息）完全发完才开始发下一条
- 异常处理：单条发送失败记录日志并计数，不阻塞后续消息
- 状态监控：暴露队列深度/已发送/失败/丢弃计数，供状态日志输出
"""

import logging
import queue
import threading
import time

log = logging.getLogger("teda_bot.send_queue")

# 优先级定义（数值越小越优先）
PRIORITY_AT = 0         # 被@机器人的回复（最高优先级）
PRIORITY_NORMAL = 1     # 普通关键词/私聊回复
PRIORITY_PROACTIVE = 2  # 主动发言


class SendQueue:
    """串行发送队列：submit() 入队，后台线程按优先级+FIFO 逐条发送。"""

    def __init__(self, client, maxsize: int = 200, watchdog_interval: float = 30.0):
        self._client = client
        self._q: queue.PriorityQueue = queue.PriorityQueue(maxsize=max(1, int(maxsize)))
        self._seq = 0                    # 入队序号：同优先级保证 FIFO
        self._seq_lock = threading.Lock()
        self._sent = 0
        self._failed = 0
        self._dropped = 0                # 队列满被丢弃的消息数
        self._stop = threading.Event()
        # 发送看门狗：单条消息发送超过 watchdog_interval 秒时周期性告警
        # （曾出现主窗口模式下轮询与发送并发操作微信窗口导致发送卡死）
        self._watchdog_interval = float(watchdog_interval)
        self._sending_since = 0.0        # 当前这条消息开始发送的时间（0=空闲）
        self._sending_chat = ""
        self._worker = threading.Thread(
            target=self._run, name="send-queue", daemon=True
        )
        self._watchdog = threading.Thread(
            target=self._watch_loop, name="send-queue-watchdog", daemon=True
        )
        self._worker.start()
        self._watchdog.start()
        log.info("发送队列已启动（容量%d，单线程串行发送）", self._q.maxsize)

    def submit(self, chat_name: str, text: str, priority: int = PRIORITY_NORMAL):
        """将待发送回复入队；队列满时丢弃并记录（防内存积压）。"""
        if not text:
            return
        with self._seq_lock:
            seq = self._seq
            self._seq += 1
        try:
            self._q.put_nowait((priority, seq, chat_name, text))
        except queue.Full:
            self._dropped += 1
            log.warning("发送队列已满(%d)，丢弃消息 [%s]: %s",
                        self._q.maxsize, chat_name, text[:40])

    def stats(self) -> dict:
        """队列状态监控指标。"""
        return {
            "pending": self._q.qsize(),
            "sent": self._sent,
            "failed": self._failed,
            "dropped": self._dropped,
        }

    def wait_done(self, timeout: float = None):
        """等待队列中所有消息发送完成（用于优雅退出）。"""
        deadline = time.monotonic() + timeout if timeout else None
        while not self._q.empty():
            if deadline and time.monotonic() > deadline:
                log.warning("退出等待超时，队列剩余 %d 条未发送", self._q.qsize())
                return
            time.sleep(0.1)

    def _run(self):
        """发送线程：逐条取出、完整发送（含拆分子消息）后才开始下一条。"""
        while not self._stop.is_set():
            try:
                priority, seq, chat_name, text = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            start = time.monotonic()
            self._sending_since = start
            self._sending_chat = chat_name
            try:
                # client.send 内部按换行拆分逐条发送，本线程串行执行
                # 保证同一会话（及全局）的消息按入队顺序依次发出
                self._client.send(chat_name, text)
                self._sent += 1
                log.info("队列发送完成 [%s]（优先级%d，排队耗时%.1fs）: %s",
                         chat_name, priority, time.monotonic() - start,
                         (text or "").splitlines()[0][:50])
            except Exception as e:
                self._failed += 1
                log.error("队列发送失败 [%s]: %s", chat_name, e)
            finally:
                self._sending_since = 0.0
                self._sending_chat = ""
                self._q.task_done()

    def _watch_loop(self):
        """看门狗：单条消息发送超过阈值时周期性告警，便于发现发送卡死。"""
        warned_at = 0.0
        while not self._stop.is_set():
            time.sleep(5.0)
            if self._sending_since <= 0:
                warned_at = 0.0
                continue
            elapsed = time.monotonic() - self._sending_since
            if elapsed >= self._watchdog_interval and time.monotonic() - warned_at >= self._watchdog_interval:
                warned_at = time.monotonic()
                log.warning(
                    "发送疑似卡住 [%s] 已持续 %.0fs 未完成（若频繁出现，"
                    "请在微信中为监听群打开独立聊天窗口，并确认微信窗口未被遮挡最小化）",
                    self._sending_chat, elapsed,
                )
