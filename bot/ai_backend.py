"""AI 对话后端：OpenAI 兼容接口适配层 + 多轮上下文管理 + 重试。

上下文管理支持场景键（group:xxx / private:xxx）隔离与持久化（MemoryStore）。
"""

import logging
import threading
import time
from collections import deque

log = logging.getLogger("teda_bot.ai")


class AIBackendError(Exception):
    """AI 调用最终失败（重试耗尽）。"""


class ChatContext:
    """单个会话的对话历史：按条数上限淘汰 + 按时间边界过滤 + 线程安全。"""

    def __init__(self, max_rounds: int = 8):
        self.max_rounds = max(1, int(max_rounds))
        self._messages = deque(maxlen=self.max_rounds * 2)  # (role, content, ts)
        self._lock = threading.Lock()

    def add(self, role: str, content: str):
        with self._lock:
            self._messages.append({"role": role, "content": content, "ts": time.time()})

    def snapshot(self, max_age: float = None) -> list:
        """返回历史消息；max_age 秒之前的旧消息不再带入（边界控制）。"""
        with self._lock:
            msgs = list(self._messages)
        if max_age is not None:
            deadline = time.time() - max(0.0, float(max_age))
            msgs = [m for m in msgs if m["ts"] >= deadline]
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    def dump(self) -> list:
        """导出含时间戳的完整记忆（供持久化）。"""
        with self._lock:
            return [dict(m) for m in self._messages]

    def restore(self, messages: list):
        """从持久化数据恢复记忆（按条数上限截断保留最新）。"""
        with self._lock:
            self._messages.clear()
            for m in messages[-self.max_rounds * 2:]:
                try:
                    self._messages.append({
                        "role": str(m.get("role", "user")),
                        "content": str(m.get("content", "")),
                        "ts": float(m.get("ts", 0.0)),
                    })
                except (TypeError, ValueError):
                    continue

    def clear(self):
        with self._lock:
            self._messages.clear()


class ContextManager:
    """按场景键（group:群名 / private:昵称）维护独立对话上下文。

    传入 MemoryStore 时：首次访问自动从磁盘恢复记忆，每次新增消息后自动持久化。
    """

    def __init__(self, max_rounds: int = 8, store=None):
        self.max_rounds = max_rounds
        self.store = store
        self._sessions: dict = {}

    def get(self, key: str) -> ChatContext:
        if key not in self._sessions:
            ctx = ChatContext(self.max_rounds)
            if self.store is not None:
                saved = self.store.load(key)
                if saved:
                    ctx.restore(saved)
                    log.info("已恢复场景记忆 [%s]（%d条）", key, len(saved))
            self._sessions[key] = ctx
        return self._sessions[key]

    def record(self, key: str, role: str, content: str):
        """写入一条消息并自动持久化到该场景的记忆文件。"""
        ctx = self.get(key)
        ctx.add(role, content)
        if self.store is not None:
            self.store.save(key, ctx.dump())

    def clear(self, key: str):
        self._sessions.pop(key, None)
        if self.store is not None:
            self.store.save(key, [])

    def stats(self) -> dict:
        """所有活跃场景的状态：键 → {messages, last_ts}（供状态监控）。"""
        out = {}
        for key, ctx in self._sessions.items():
            msgs = ctx.dump()
            out[key] = {
                "messages": len(msgs),
                "last_ts": msgs[-1]["ts"] if msgs else 0.0,
            }
        return out


class AIBackend:
    def __init__(self, cfg: dict):
        from openai import OpenAI  # 延迟导入，便于单测在无网环境运行

        self.model = cfg.get("model", "gpt-4o-mini")
        self.temperature = cfg.get("temperature", 0.8)
        self.max_retries = int(cfg.get("max_retries", 2))
        self.retry_backoff = float(cfg.get("retry_backoff", 1.0))
        self.timeout = float(cfg.get("timeout") or 30)
        self.client = OpenAI(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url", "https://api.openai.com/v1"),
            timeout=self.timeout,      # 请求超时（SDK 默认 600s 太长，会长时间占用线程）
            max_retries=1,             # SDK 层网络重试仅 1 次，避免与应用层重试叠加
        )

    def chat(self, system_prompt: str, history: list, user_msg: str) -> str:
        """调用 AI 生成回复。失败按指数退避重试，重试耗尽抛出 AIBackendError。"""
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_msg})

        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:  # 网络/接口异常统一退避重试
                last_err = e
                delay = self.retry_backoff * (2 ** attempt)
                log.warning("AI 请求失败(第%d次): %s，%.1fs 后重试", attempt + 1, e, delay)
                if attempt < self.max_retries - 1:
                    time.sleep(delay)
        raise AIBackendError(f"AI 调用失败（已重试{self.max_retries}次）: {last_err}")
