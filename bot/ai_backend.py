"""AI 对话后端：OpenAI 兼容接口适配层 + 多轮上下文管理 + 重试。

上下文管理支持场景键（group:xxx / private:xxx）隔离与持久化（MemoryStore）。
"""

import logging
import threading
import time
import uuid
from collections import deque

from bot.logger import log_task, log_success

log = logging.getLogger("teda_bot.ai")

# 思考内容清洗：去除 <think>/<thinking>/<reasoning> 等标签块，只保留最终回答
import re

_THINK_TAG_RE = re.compile(
    r"<\s*(think|thinking|reasoning|thought)\s*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# 未闭合的思考标签：从开标签起到文本末尾全部丢弃
_UNCLOSED_THINK_RE = re.compile(
    r"<\s*(think|thinking|reasoning|thought)\s*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
# 残留的孤立思考标签（如 "</think>" 单独成段）
_ORPHAN_THINK_TAG_RE = re.compile(
    r"</?\s*(think|thinking|reasoning|thought)\s*>",
    re.IGNORECASE,
)

# 散文式思考内容（无标签、纯文本 CoT）特征短语，均针对"模型以第三人称
# 分析请求"的典型口吻；正常聊天回复几乎不会同时命中 2 项及以上
_COT_INDICATOR_RE = [
    re.compile(r"用户(突然|给出|发来|提供|可能|是在|想要|需要|问|提到|输入)", re.DOTALL),
    re.compile(r"(我|我得|我现在?)(需要|要)(处理|分析|确定|考虑|判断|回[回应复]|确保|仔细)", re.DOTALL),
    re.compile(r"首先[，,].{0,24}(我|需|要|得|让)", re.DOTALL),
    re.compile(r"(让我|我们来|先来)(分析|看看|梳理|理清)", re.DOTALL),
    re.compile(r"根据(设定|角色|人设|用户|以上|上述|上下文|规则)", re.DOTALL),
    re.compile(r"(角色|人设)设定|符合(人设|角色|规则|要求)", re.DOTALL),
    re.compile(r"其次[，,]|最后[，,].{0,12}(确保|注意|检查)", re.DOTALL),
    re.compile(r"看起来像是?(一个|一道)", re.DOTALL),
    re.compile(r"(这个|这道)(问题|题目|请求)", re.DOTALL),
    re.compile(r"(分析|思考)(一下|一下当前|当前|当前对话)", re.DOTALL),
    re.compile(r"(检查|确认)(是否|一下)", re.DOTALL),
]


def looks_like_cot(text: str) -> bool:
    """判断文本是否疑似散文式思考过程（无标签纯文本 CoT）。

    规则：命中 >=2 个不同特征短语即判定；仅命中 1 个时若以典型 CoT
    开头（如 "嗯，用户…"、"好的，现在需要…"）且文本较长也判定。
    """
    if not text:
        return False
    hits = sum(1 for rx in _COT_INDICATOR_RE if rx.search(text))
    if hits >= 2:
        return True
    if hits == 1 and len(text) >= 60 and re.match(
        r"^(嗯|好的|好[的呀吧]|那|这|看来|收到|明白了?)[，,]?\s*(用户|现在|接下来|让我|首先|我)",
        text,
    ):
        return True
    return False


def strip_thinking(text: str) -> str:
    """清洗模型输出中的思考过程，仅保留最终回答。"""
    if not text:
        return text or ""
    text = _THINK_TAG_RE.sub("", text)
    text = _UNCLOSED_THINK_RE.sub("", text)
    text = _ORPHAN_THINK_TAG_RE.sub("", text)
    return text.strip()


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
        # 是否禁用思考模式（默认禁用，防止 CoT 内容被当成回复发出）
        self.disable_thinking = bool(cfg.get("disable_thinking", True))
        # 思考内容二次提炼：检测到思考内容时不直接作废，而是提炼出最终回答复用（默认开）
        self.cot_refine = bool(cfg.get("cot_refine", True))
        self.client = OpenAI(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url", "https://api.openai.com/v1"),
            timeout=self.timeout,      # 请求超时（SDK 默认 600s 太长，会长时间占用线程）
            max_retries=1,             # SDK 层网络重试仅 1 次，避免与应用层重试叠加
        )

    def _build_extra_body(self) -> dict:
        """构造禁用思考模式的多协议参数，兼容主流 OpenAI 兼容后端。

        不同后端识别不同字段，全部带上后端会忽略不认识的字段：
        - Qwen3 / GLM / vLLM/SGLang: enable_thinking / chat_template_kwargs
        - GLM-4.5+ / 智谱: thinking.type = disabled
        - OpenAI o 系列 / GPT-5: reasoning_effort = none/minimal
        - DeepSeek: 不支持关闭，靠响应层清洗
        """
        if not self.disable_thinking:
            return {}
        return {
            "enable_thinking": False,               # vLLM/SGLang/DashScope
            "thinking": {"type": "disabled"},       # 智谱 GLM / Anthropic 风格
            "chat_template_kwargs": {"enable_thinking": False},  # Qwen3 vLLM/SGLang
            "reasoning_effort": "none",             # OpenAI o/gpt-5 系列（旧版用 minimal）
            "reasoning": {"exclude": True},         # OpenRouter
        }

    _REFINE_SYSTEM = (
        "你是一个回复提炼器。用户发来的内容是一段 AI 模型的原始输出，其中混入了模型的"
        "内心思考过程（如分析用户意图、讨论角色设定、检查规则等），且思考内容没有用任何"
        "标签包裹。请从中提炼出模型真正想发送给用户的最终回复："
        "1) 删除所有思考分析过程；"
        "2) 仅保留适合直接发给用户的回答内容，若原文只有思考没有成型的回答，请依据思考中"
        "   的结论补全一条简短自然的回复；"
        "3) 保持原文的语言、人设、语气与核心信息，不引入新的事实；"
        "4) 只输出最终回复本身，不要任何解释、前后缀或标签。"
    )

    def _refine_content(self, task_id: str, content: str) -> str | None:
        """对检测到的思考内容做二次提炼，成功返回可发送的最终回复，失败返回 None。

        提炼请求自身也走 auto 随机路由，可能命中思考源，故最多尝试 2 次。
        """
        for attempt in range(2):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self._REFINE_SYSTEM},
                        {"role": "user", "content": content},
                    ],
                    temperature=0.3,  # 提炼任务要求稳定，低温
                    extra_body=self._build_extra_body() or None,
                )
                refined = strip_thinking(resp.choices[0].message.content or "")
                if refined and not looks_like_cot(refined):
                    log_task(log, task_id, "思考内容已提炼复用（%d字 → %d字）",
                             len(content), len(refined))
                    return refined
                log_task(log, task_id, "提炼第%d次输出仍含思考，%s",
                         attempt + 1, "重试" if attempt == 0 else "放弃提炼转重试")
            except Exception as e:
                log_task(log, task_id, "提炼请求异常: %s", e)
        return None

    def chat(self, system_prompt: str, history: list, user_msg: str) -> str:
        """调用 AI 生成回复。失败按指数退避重试，重试耗尽抛出 AIBackendError。"""
        task_id = uuid.uuid4().hex[:8]
        log_task(log, task_id, "模型生成任务开始 | 模型=%s | 输入%d条历史消息",
                 self.model, len(history) + 2)
        start_ts = time.monotonic()
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_msg})
        extra_body = self._build_extra_body()

        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    extra_body=extra_body or None,
                )
                content = resp.choices[0].message.content or ""
                cleaned = strip_thinking(content)
                if cleaned != content.strip():
                    log.info("已清洗回复中的思考标签（清洗前 %d 字 → 清洗后 %d 字）",
                             len(content), len(cleaned))
                if not cleaned and content:
                    # 整段均为标签思考内容，视为失败触发重试
                    log.warning("回复经标签清洗后为空（整段均为思考内容），重试")
                    raise ValueError("empty after thinking-strip")
                if cleaned and looks_like_cot(cleaned):
                    # auto 路由可能命中带思考模式的源，输出无标签纯文本 CoT；
                    # 先尝试二次提炼复用其中的最终回答，提炼失败才作废重试
                    if self.cot_refine:
                        refined = self._refine_content(task_id, cleaned)
                        if refined:
                            log_success(log, "[任务%s] 模型生成完成(提炼复用) | 耗时%.1fs | 输出%d字",
                                        task_id, time.monotonic() - start_ts, len(refined))
                            return refined
                    log_task(log, task_id, "检测到思考内容，第%d次尝试作废并重试", attempt + 1)
                    raise ValueError("prose thinking detected")
                log_success(log, "[任务%s] 模型生成完成 | 耗时%.1fs | 输出%d字",
                            task_id, time.monotonic() - start_ts, len(cleaned))
                return cleaned
            except Exception as e:  # 网络/接口异常统一退避重试
                last_err = e
                delay = self.retry_backoff * (2 ** attempt)
                log.warning("AI 请求失败(第%d次): %s，%.1fs 后重试", attempt + 1, e, delay)
                if attempt < self.max_retries - 1:
                    time.sleep(delay)
        log_task(log, task_id, "模型生成任务失败（已重试%d次）", self.max_retries)
        raise AIBackendError(f"AI 调用失败（已重试{self.max_retries}次）: {last_err}")
