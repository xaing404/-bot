"""AI 对话后端：OpenAI 兼容接口适配层 + 多轮上下文管理 + 重试。"""

import logging
import time
from collections import deque

log = logging.getLogger("teda_bot.ai")


class AIBackendError(Exception):
    """AI 调用最终失败（重试耗尽）。"""


class ChatContext:
    """单个会话的对话历史：按条数上限淘汰 + 按时间边界过滤。"""

    def __init__(self, max_rounds: int = 8):
        self.max_rounds = max(1, int(max_rounds))
        self._messages = deque(maxlen=self.max_rounds * 2)  # (role, content, ts)

    def add(self, role: str, content: str):
        self._messages.append({"role": role, "content": content, "ts": time.time()})

    def snapshot(self, max_age: float = None) -> list:
        """返回历史消息；max_age 秒之前的旧消息不再带入（边界控制）。"""
        if max_age is None:
            msgs = self._messages
        else:
            deadline = time.time() - max(0.0, float(max_age))
            msgs = (m for m in self._messages if m["ts"] >= deadline)
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    def clear(self):
        self._messages.clear()


class ContextManager:
    """按会话键（群名/好友昵称）维护各自独立的对话上下文。"""

    def __init__(self, max_rounds: int = 8):
        self.max_rounds = max_rounds
        self._sessions: dict = {}

    def get(self, key: str) -> ChatContext:
        if key not in self._sessions:
            self._sessions[key] = ChatContext(self.max_rounds)
        return self._sessions[key]

    def clear(self, key: str):
        self._sessions.pop(key, None)


class AIBackend:
    def __init__(self, cfg: dict):
        from openai import OpenAI  # 延迟导入，便于单测在无网环境运行

        self.model = cfg.get("model", "gpt-4o-mini")
        self.temperature = cfg.get("temperature", 0.8)
        self.max_retries = int(cfg.get("max_retries", 3))
        self.retry_backoff = float(cfg.get("retry_backoff", 1.0))
        self.client = OpenAI(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url", "https://api.openai.com/v1"),
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
