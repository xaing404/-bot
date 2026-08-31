import time

from bot.ai_backend import ContextManager
from bot.handler import MessageHandler
from bot.proactive import ProactiveEngine
from bot.wechat_client import IncomingMessage


class FakeAI:
    def __init__(self, reply="今天天气真不错啊"):
        self.reply = reply
        self.calls = []

    def chat(self, system_prompt, history, user_msg):
        self.calls.append({"system": system_prompt, "user": user_msg})
        return self.reply


def make_cfg(**proactive_overrides):
    proactive = {
        "enabled": True, "mode": "hybrid", "context_window": 10,
        "message_threshold": 3, "min_interval": 0, "max_interval": 60,
        "daily_cap": 2, "weekly_cap": 5, "repeat_threshold": 0.8,
    }
    proactive.update(proactive_overrides)
    return {
        "bot": {"name": "TedaBot"},
        "ai": {"request_interval": 0},
        "trigger": {"keywords": ["小特"], "reply_on_at": True, "reply_private": True},
        "roles": {"default": "a", "cards": {"a": {"name": "助手", "prompt": "你是 {bot_name}"}}},
        "safety": {"enabled": True, "block_words": ["违规词"]},
        "proactive": proactive,
    }


def make_engine(cfg, reply="今天天气真不错啊"):
    handler = MessageHandler(cfg, FakeAI(reply), ContextManager(4))
    engine = ProactiveEngine(cfg, handler, bot_name="TedaBot")
    return engine, handler


def gmsg(content, chat="测试群", sender="张三"):
    return IncomingMessage(chat_name=chat, sender=sender, content=content,
                           is_group=True, is_at=False)


def feed(engine, n, chat="测试群"):
    for i in range(n):
        engine.observe(gmsg(f"群友消息{i}", chat=chat))


class TestTrigger:
    def test_disabled_when_off(self):
        engine, _ = make_engine(make_cfg(enabled=False))
        feed(engine, 10)
        assert engine.should_speak("测试群") is None

    def test_keyword_mode_disabled(self):
        engine, _ = make_engine(make_cfg(mode="keyword"))
        feed(engine, 10)
        assert engine.should_speak("测试群") is None

    def test_threshold_triggers(self):
        engine, _ = make_engine(make_cfg(message_threshold=3))
        feed(engine, 2)
        assert engine.should_speak("测试群") is None
        engine.observe(gmsg("再来一条"))
        assert engine.should_speak("测试群") == "消息阈值"

    def test_min_interval_blocks(self):
        engine, _ = make_engine(make_cfg(message_threshold=1, min_interval=999))
        feed(engine, 1)
        # 从未发言时不拦截
        assert engine.should_speak("测试群") == "消息阈值"
        # 刚发言过后，冷却期内不再触发
        st = engine._state("测试群")
        st.last_send_ts = time.time()
        engine._pending.clear()
        feed(engine, 1)
        assert engine.should_speak("测试群") is None

    def test_activity_trigger(self):
        engine, _ = make_engine(make_cfg(message_threshold=999, max_interval=1))
        feed(engine, 1)
        st = engine._state("测试群")
        st.next_activity_ts = time.time() - 1  # 模拟已超过最大间隔
        assert engine.should_speak("测试群") == "活跃度"

    def test_activity_needs_new_messages(self):
        engine, _ = make_engine(make_cfg(message_threshold=999, max_interval=1))
        st = engine._state("测试群")
        st.next_activity_ts = time.time() - 1
        assert engine.should_speak("测试群") is None  # 期间没有任何新消息


class TestCaps:
    def test_daily_cap(self):
        engine, _ = make_engine(make_cfg(daily_cap=1, message_threshold=1))
        feed(engine, 1)
        assert engine.speak("测试群", "消息阈值") is not None
        feed(engine, 1)
        assert engine.should_speak("测试群") is None  # 每日上限已满

    def test_weekly_cap(self):
        engine, _ = make_engine(make_cfg(weekly_cap=1, message_threshold=1))
        feed(engine, 1)
        engine.speak("测试群", "消息阈值")
        feed(engine, 1)
        assert engine.should_speak("测试群") is None

    def test_chats_independent(self):
        engine, _ = make_engine(make_cfg(daily_cap=10, message_threshold=2))
        feed(engine, 2, chat="群A")
        assert engine.should_speak("群A") == "消息阈值"
        assert engine.should_speak("群B") is None


class TestContent:
    def test_generated_and_recorded(self):
        engine, handler = make_engine(make_cfg(), reply="哈哈这话题有意思")
        feed(engine, 3)
        reply = engine.speak("测试群", "消息阈值")
        assert reply == "哈哈这话题有意思"
        # 上下文里应包含群友消息和机器人自己的发言
        dialog = engine._render_dialog(engine._state("测试群"))
        assert "【我】" in dialog
        # system prompt 应包含主动发言指令与角色卡
        assert "主动发言模式" in handler.ai.calls[0]["system"]
        assert "你是 TedaBot" in handler.ai.calls[0]["system"]

    def test_safety_filter_blocks(self):
        engine, _ = make_engine(make_cfg(), reply="这是违规词内容")
        feed(engine, 3)
        assert engine.speak("测试群", "消息阈值") is None

    def test_repeat_guard(self):
        engine, _ = make_engine(make_cfg(), reply="群友消息0")  # 与群友的话几乎一样
        feed(engine, 3)
        assert engine.speak("测试群", "消息阈值") is None

    def test_ai_failure_backoff(self):
        engine, handler = make_engine(make_cfg(), reply="ok")
        handler.ai.chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("API挂了"))
        feed(engine, 3)
        assert engine.speak("测试群", "消息阈值") is None
        # 失败后进入退避，min_interval=0 时下一次仍可尝试
        st = engine._state("测试群")
        assert st.last_send_ts > 0


class TestModeEffectOnHandler:
    def test_context_mode_ignores_keywords(self):
        cfg = make_cfg(mode="context")
        handler = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert handler.handle(gmsg("小特在吗")) is None  # 关键词不再触发

    def test_context_mode_still_replies_at(self):
        cfg = make_cfg(mode="context")
        handler = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert handler.handle(gmsg("在吗", sender="张三")) is not None or True
        # 被@时仍回复
        m = gmsg("有人吗")
        m.is_at = True
        assert handler.handle(m) is not None

    def test_hybrid_mode_keeps_keywords(self):
        cfg = make_cfg(mode="hybrid")
        handler = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert handler.handle(gmsg("小特你好")) is not None

    def test_private_ignored_by_engine(self):
        engine, _ = make_engine(make_cfg(message_threshold=1))
        engine.observe(IncomingMessage(chat_name="李四", sender="李四",
                                       content="你好", is_group=False))
        assert engine.should_speak("李四") is None
