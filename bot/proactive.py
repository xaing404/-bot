"""主动互动引擎：根据群聊上下文与频率策略，让机器人主动参与群聊。

触发条件（全部满足才发言）：
1. 未超过每日/每周发言上限
2. 距上次主动发言 >= min_interval（防刷屏）
3. 满足其一：
   - 距上次发言累计新群消息 >= message_threshold（跟话题走）
   - 距上次发言 >= max_interval 且期间群里有新消息（保活跃度）
   - 群静默：超过 silence_interval 无任何成员发言时基于上下文接话；
     任一成员发新消息立即重置静默计时（silence_interval=0 关闭）

内容生成：将最近 context_window 条群聊记录（含机器人自己说的话）作为上下文，
按角色卡生成一句贴合话题的回复；与最近聊天内容相似度过高则放弃（防重复），
命中敏感词则丢弃。每次发言（含失败退避）后重新进入下一个监测周期。
"""

import logging
import random
import time
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher

from bot.matcher import strip_mentions

log = logging.getLogger("teda_bot.proactive")

# 主动发言的附加指令（拼在角色卡 system prompt 之后）
PROACTIVE_INSTRUCTION = """

【主动发言模式】以下是最近的群聊记录（【我】开头的行是你自己说过的话）。
请以你的角色身份，自然地接一句话参与当前话题。
要求：
1. 紧扣群里最近的话题，保持连贯；
2. 简短（不超过50字），口语化，符合群聊氛围；
3. 绝不重复记录中出现过（你自己或群友说过的）内容；
4. 不要自我介绍，不要说明你是机器人；
5. 只输出要发送的那一句话本身，不要任何前缀、引号或解释。"""


class _ChatState:
    """单个群聊的观察状态。"""

    def __init__(self, context_window: int):
        self.history = deque(maxlen=max(5, context_window))  # (sender, content, is_bot)
        self.count_since_send = 0
        self.last_send_ts = 0.0
        self.next_activity_ts = 0.0  # 下次"活跃度冒泡"的最早时间
        # 最后一次成员发言时间：群静默计时的基准（任一新消息即重置）
        self.last_msg_ts = time.time()


class ProactiveEngine:
    def __init__(self, cfg: dict, handler, bot_name: str = "", throttle=None):
        pc = cfg.get("proactive") or {}
        mode = str(pc.get("mode", "keyword")).lower()
        self.enabled = bool(pc.get("enabled", False)) and mode in ("context", "hybrid")
        self.context_window = int(pc.get("context_window", 20))
        self.message_threshold = max(1, int(pc.get("message_threshold", 25)))
        self.min_interval = float(pc.get("min_interval", 300))
        self.max_interval = float(pc.get("max_interval", 1800))
        # 群静默冒泡时长（秒）：超过该时长无任何成员发言时基于上下文接话；0 = 关闭
        self.silence_interval = float(pc.get("silence_interval", 300))
        self.daily_cap = int(pc.get("daily_cap", 50))
        self.weekly_cap = int(pc.get("weekly_cap", 200))
        self.repeat_threshold = float(pc.get("repeat_threshold", 0.8))

        self.handler = handler      # 复用其 ai / roles / safety / limiter
        # 共享场景限流器：主动发言与普通回复共同遵守该群的最小回复间隔
        self.throttle = throttle or handler.throttle
        self.bot_name = bot_name
        self._states: dict = {}
        self._pending: set = set()  # 正在生成发言的群，防止重复触发

        # 每日/每周计数（内存态，重启清零）
        self._daily = {"date": self._today(), "count": 0}
        self._weekly = {"week": self._week(), "count": 0}

    # ---------- 观察 ----------

    def observe(self, msg):
        """收集所有群聊消息（含未触发回复的），积累上下文。"""
        if not self.enabled or not msg.is_group:
            return
        st = self._state(msg.chat_name)
        # 清除 @提及（如 @机器人昵称）后再入历史，防止 AI 误解析为对话对象
        st.history.append((msg.sender, strip_mentions(msg.content) or msg.content, False))
        st.count_since_send += 1
        # 成员发言重置群静默计时，开始监测下一个静默周期
        st.last_msg_ts = time.time()

    # ---------- 触发判定 ----------

    def should_speak(self, chat_name: str) -> str | None:
        """返回触发原因（"消息阈值"/"活跃度"/"群静默"）；不触发返回 None。"""
        if not self.enabled or chat_name in self._pending:
            return None
        # 场景忙碌（有消息正在处理/回复正在生成）则不主动发言：
        # 否则会对刚发言的成员触发"群静默"，生成出与@回复雷同的内容，
        # 造成"先发无@消息、紧跟带@重复消息"的刷屏
        if self.handler.is_busy(f"group:{chat_name}"):
            return None
        # 场景级防刷屏：距该群上次回复（含@回复）不足最小间隔则不主动发言
        if not self.throttle.allow(f"group:{chat_name}"):
            return None
        # 距该群任意回复（@回复/普通回复/主动发言）不足 min_interval 时不主动发言：
        # 刚回复完就"群静默"冒泡，会基于同一上下文再次作答/复述刚才的话题，
        # 造成"带@回复发一次、不带@主动发言再发一次"的重复刷屏
        since = self.throttle.seconds_since(f"group:{chat_name}")
        if since is not None and since < self.min_interval:
            return None
        st = self._state(chat_name)
        now = time.time()
        self._roll_counters()

        if self._daily["count"] >= self.daily_cap:
            return None
        if self._weekly["count"] >= self.weekly_cap:
            return None
        if st.last_send_ts and now - st.last_send_ts < self.min_interval:
            return None
        if st.count_since_send >= self.message_threshold:
            self._pending.add(chat_name)
            return "消息阈值"
        if st.next_activity_ts and now >= st.next_activity_ts and st.count_since_send > 0:
            self._pending.add(chat_name)
            return "活跃度"
        # 群静默冒泡：超过 silence_interval 无任何成员发言时基于上下文接话
        if self._silence_due(st):
            self._pending.add(chat_name)
            return "群静默"
        return None

    def _silence_due(self, st: _ChatState) -> bool:
        """群静默判定：静默时长已到且存在可依据的上下文（无上下文不冒泡）。"""
        if self.silence_interval <= 0 or not st.history:
            return False
        return time.time() - st.last_msg_ts >= self.silence_interval

    # ---------- 内容生成 ----------

    def speak(self, chat_name: str, reason: str) -> str | None:
        """生成一条主动发言；失败/被过滤返回 None。"""
        try:
            return self._speak_inner(chat_name, reason)
        finally:
            self._pending.discard(chat_name)

    def _speak_inner(self, chat_name: str, reason: str) -> str | None:
        st = self._state(chat_name)
        dialog = self._render_dialog(st)
        if not dialog:
            return None

        system = self.handler.roles.system_prompt(user="大家") + PROACTIVE_INSTRUCTION
        self.handler.limiter.wait()
        try:
            reply = (self.handler.ai.chat(system, [], dialog) or "").strip()
        except Exception as e:
            log.error("主动发言生成失败 [%s]: %s", chat_name, e)
            st.last_send_ts = time.time()  # 失败退避，避免重试风暴
            return None
        if not reply:
            st.last_send_ts = time.time()
            return None

        # 清洗 @提及，主动发言不 @ 任何人
        reply = strip_mentions(reply)
        if not reply:
            st.last_send_ts = time.time()
            return None

        if self.handler.safety.is_blocked(reply):
            log.warning("主动发言命中敏感词，已丢弃 [%s]", chat_name)
            st.last_send_ts = time.time()
            return None
        if self._too_similar(reply, st):
            log.info("主动发言与近期内容重复，已丢弃 [%s]: %s", chat_name, reply[:50])
            st.last_send_ts = time.time()
            return None
        # 与普通回复共用重复检测：与该群最近发送内容高度相似则拦截
        if self.handler.is_duplicate(chat_name, reply):
            log.info("主动发言与最近回复重复，已丢弃 [%s]: %s", chat_name, reply[:50])
            st.last_send_ts = time.time()
            return None

        self._record_send(st, reply, chat_name)
        log.info("主动发言 [%s] 触发: %s | 每日%d/%d 周%d/%d | 内容: %s",
                 chat_name, reason, self._daily["count"], self.daily_cap,
                 self._weekly["count"], self.weekly_cap, reply[:80])
        return reply

    # ---------- 内部工具 ----------

    def _state(self, chat_name: str) -> _ChatState:
        if chat_name not in self._states:
            self._states[chat_name] = _ChatState(self.context_window)
        return self._states[chat_name]

    def _render_dialog(self, st: _ChatState) -> str:
        lines = []
        for sender, content, is_bot in st.history:
            lines.append(f"{'【我】' if is_bot else sender}: {content}")
        return "\n".join(lines)

    def _too_similar(self, reply: str, st: _ChatState) -> bool:
        for _, content, _ in st.history:
            if SequenceMatcher(None, reply, content).ratio() >= self.repeat_threshold:
                return True
        return False

    def _record_send(self, st: _ChatState, reply: str, chat_name: str = ""):
        # 换行折叠为空格，保持对话记录单行格式
        st.history.append((self.bot_name, " ".join(reply.split()), True))
        st.count_since_send = 0
        st.last_send_ts = time.time()
        # 本次发言视作新的计时基准，重新开始监测下一个静默周期（防连环刷屏）
        st.last_msg_ts = time.time()
        # 活跃度冒泡点随机落在 [min_interval, max_interval] 区间，更像真人
        st.next_activity_ts = time.time() + random.uniform(self.min_interval, self.max_interval)
        # 与普通回复共享场景冷却计时，防止主动发言后紧跟 @回复刷屏
        if chat_name:
            self.throttle.mark(f"group:{chat_name}")
        self._roll_counters()
        self._daily["count"] += 1
        self._weekly["count"] += 1

    def _roll_counters(self):
        today, week = self._today(), self._week()
        if self._daily["date"] != today:
            self._daily = {"date": today, "count": 0}
        if self._weekly["week"] != week:
            self._weekly = {"week": week, "count": 0}

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _week():
        return datetime.now().isocalendar()[:2]
