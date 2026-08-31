"""消息处理管线：白名单 → 敏感过滤 → 触发判定 → AI 调用 → 重复检测 → 生成回复。"""

import logging
from collections import deque
from difflib import SequenceMatcher

from bot.matcher import match_keywords, strip_mentions
from bot.rate_limit import RateLimiter
from bot.role_cards import RoleCards
from bot.safety import SafetyFilter

log = logging.getLogger("teda_bot.handler")


class MessageHandler:
    def __init__(self, cfg: dict, ai, contexts):
        self.cfg = cfg
        self.ai = ai
        self.contexts = contexts
        self.roles = RoleCards(cfg.get("roles"), cfg.get("bot", {}).get("name", ""))
        self.safety = SafetyFilter(
            (cfg.get("safety") or {}).get("block_words", []),
            (cfg.get("safety") or {}).get("enabled", True),
        )
        trig = cfg.get("trigger") or {}
        self.keywords = trig.get("keywords", [])
        self.fuzzy = trig.get("fuzzy", True)
        self.fuzzy_threshold = trig.get("fuzzy_threshold", 0.75)
        self.reply_on_at = trig.get("reply_on_at", True)
        self.reply_private = trig.get("reply_private", True)
        # 群聊回复是否在开头 @发送者（默认开启：@提问人）
        self.reply_at_sender = trig.get("reply_at_sender", True)
        # 与最近回复相似度 >= 该值时拦截（防重复发送）
        self.duplicate_threshold = float(trig.get("duplicate_threshold", 0.8))
        # 上下文时间边界：超过该秒数的旧消息不再带入 AI（防误关联历史）
        self.context_max_age = float((cfg.get("ai") or {}).get("context_max_age", 1800))
        # 主动互动模式：context 模式下关键词不再触发直接回复（@机器人仍回复）
        self.proactive_mode = str((cfg.get("proactive") or {}).get("mode", "keyword")).lower()
        self.limiter = RateLimiter((cfg.get("ai") or {}).get("request_interval", 2.0))
        # 每个会话最近发出的回复（用于重复发送检测）
        self._recent_replies: dict = {}

    def should_reply(self, msg) -> bool:
        """触发判定：关键词命中 / 被@ / 私聊，满足任一即回复。"""
        if not msg.is_group:
            return self.reply_private
        if msg.is_at and self.reply_on_at:
            return True
        if self.proactive_mode == "context":  # 纯上下文模式：忽略关键词
            return False
        return match_keywords(
            msg.content, self.keywords, self.fuzzy, self.fuzzy_threshold
        ) is not None

    def handle(self, msg) -> str | None:
        """处理一条消息，返回要发送的回复文本；不需要回复返回 None。"""
        if not self.should_reply(msg):
            return None
        if self.safety.is_blocked(msg.content):
            log.warning("消息命中敏感词，已忽略 [%s/%s]", msg.chat_name, msg.sender)
            return None

        log.info("收到消息 [%s/%s]: %s", msg.chat_name, msg.sender, msg.content[:80])
        ctx = self.contexts.get(msg.chat_name)
        self.limiter.wait()
        try:
            reply = self.ai.chat(
                self.roles.system_prompt(user=msg.sender),
                ctx.snapshot(self.context_max_age),  # 时间边界：旧消息不带入
                msg.content,
            )
        except Exception as e:
            log.error("AI 调用失败，跳过该消息: %s", e)
            return None
        if not reply:
            return None

        # 清洗 AI 输出中的 @提及（防止 AI 模仿历史格式产生 @self 或乱@人）
        clean = strip_mentions(reply)
        if not clean:
            return None

        # 重复发送检测：与该会话最近回复高度相似则自动拦截
        if self.is_duplicate(msg.chat_name, clean):
            log.warning("回复与最近发送内容高度相似，已拦截 [%s]: %s",
                        msg.chat_name, clean[:60])
            return None

        # 上下文存发送者标注的 user 消息 + 不带 @ 的纯文本回复，避免 AI 模仿 @ 格式
        user_text = f"{msg.sender}: {msg.content}" if msg.is_group and msg.sender else msg.content
        ctx.add("user", user_text)
        ctx.add("assistant", clean)
        self.record_sent(msg.chat_name, clean)

        # 按配置在开头 @提问人
        if self.reply_at_sender and msg.is_group and msg.sender:
            clean = f"@{msg.sender} {clean}"
        return clean

    # ---------- 发送状态跟踪 ----------

    def is_duplicate(self, chat_name: str, text: str) -> bool:
        """与该会话最近几条已发送回复相似度过高即视为重复。"""
        for prev in self._recent_replies.get(chat_name, ()):
            if SequenceMatcher(None, text, prev).ratio() >= self.duplicate_threshold:
                return True
        return False

    def record_sent(self, chat_name: str, text: str):
        """记录已发送回复（发送成功后调用）。"""
        recent = self._recent_replies.setdefault(chat_name, deque(maxlen=5))
        recent.append(text)
