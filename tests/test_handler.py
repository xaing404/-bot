import pytest

from bot.ai_backend import ContextManager
from bot.handler import MessageHandler
from bot.wechat_client import IncomingMessage


class FakeAI:
    def __init__(self, fail=False, reply="这是AI回复"):
        self.fail = fail
        self.reply = reply
        self.calls = []

    def chat(self, system_prompt, history, user_msg):
        if self.fail:
            raise RuntimeError("API 不可用")
        self.calls.append({"system": system_prompt, "history": history, "user": user_msg})
        return self.reply


@pytest.fixture
def cfg():
    return {
        "bot": {"name": "TedaBot"},
        "ai": {"request_interval": 0, "max_context_rounds": 4},
        "trigger": {
            "keywords": ["小特"],
            "fuzzy": True,
            "fuzzy_threshold": 0.75,
            "reply_on_at": True,
            "reply_private": True,
        },
        "roles": {
            "default": "assistant",
            "cards": {"assistant": {"name": "通用助手", "prompt": "你是 {bot_name}"}},
        },
        "safety": {"enabled": True, "block_words": ["敏感词"]},
    }


@pytest.fixture
def handler(cfg):
    return MessageHandler(cfg, FakeAI(), ContextManager(4))


def msg(content, group=True, at=False, sender="张三", chat="测试群"):
    return IncomingMessage(chat_name=chat, sender=sender, content=content,
                           is_group=group, is_at=at)


class TestTrigger:
    def test_group_no_trigger(self, handler):
        assert handler.handle(msg("今天天气不错")) is None

    def test_group_keyword(self, handler):
        reply = handler.handle(msg("小特你好"))
        assert reply == "@张三 这是AI回复"  # 默认 @提问人

    def test_group_at_sender_disabled(self, cfg):
        cfg["trigger"]["reply_at_sender"] = False
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert h.handle(msg("小特你好")) == "这是AI回复"

    def test_ai_output_mentions_stripped(self, cfg):
        """AI 输出里自带 @（含 @self）应被清除，且不会出现两个 @。"""
        cfg["trigger"]["reply_at_sender"] = True
        h = MessageHandler(cfg, FakeAI(reply="@李四 你好呀 @TedaBot"), ContextManager(4))
        reply = h.handle(msg("小特你好"))
        assert reply == "@张三 你好呀"  # 只保留 @提问人
        # 上下文中存的是不带 @ 的纯文本，避免 AI 模仿
        history = h.contexts.get("测试群").snapshot()
        assert "@" not in history[-1]["content"]

    def test_mention_only_reply_dropped(self, cfg):
        """AI 回复如果只含 @提及，清洗后为空则不发送。"""
        h = MessageHandler(cfg, FakeAI(reply="@TedaBot"), ContextManager(4))
        assert h.handle(msg("小特你好")) is None

    def test_group_at(self, handler):
        reply = handler.handle(msg("在吗", at=True))
        assert reply is not None

    def test_private_direct_reply(self, handler):
        reply = handler.handle(msg("随便说点什么", group=False, chat="李四"))
        assert reply == "这是AI回复"

    def test_reply_on_at_disabled(self, cfg):
        cfg["trigger"]["reply_on_at"] = False
        cfg["trigger"]["reply_private"] = False
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert h.handle(msg("在吗", at=True)) is None


class TestSafety:
    def test_blocked_not_replied(self, handler):
        assert handler.handle(msg("小特，说个敏感词")) is None


class TestContextFlow:
    def test_history_passed_to_ai(self, cfg):
        ai = FakeAI()
        contexts = ContextManager(4)
        h = MessageHandler(cfg, ai, contexts)
        h.handle(msg("小特，第一句话"))
        h.handle(msg("小特，第二句话，完全不同"))
        assert len(ai.calls) == 2
        # 第二次调用时应带上第一轮的 user 与 assistant 消息
        history = ai.calls[1]["history"]
        assert history[0] == {"role": "user", "content": "张三: 小特，第一句话"}
        assert history[1]["role"] == "assistant"

    def test_private_history_no_sender_prefix(self, cfg):
        ai = FakeAI()
        contexts = ContextManager(4)
        h = MessageHandler(cfg, ai, contexts)
        h.handle(msg("你好", group=False, chat="李四", sender="李四"))
        h.handle(msg("再见", group=False, chat="李四", sender="李四"))
        assert ai.calls[1]["history"][0]["content"] == "你好"

    def test_context_max_age(self, cfg):
        """时间边界：过期上下文不带入 AI。"""
        cfg["ai"]["context_max_age"] = 100
        ai = FakeAI()
        contexts = ContextManager(4)
        h = MessageHandler(cfg, ai, contexts)
        h.handle(msg("小特，第一句话"))
        # 把第一条历史时间戳改旧，模拟陈旧对话
        ctx = contexts.get("测试群")
        ctx._messages[0]["ts"] -= 9999
        h.handle(msg("小特，第二句话，完全不同"))
        # 带入 AI 的历史中不应包含过期消息
        assert all("第一句话" not in m["content"] for m in ai.calls[1]["history"])


class TestDuplicateGuard:
    def test_identical_reply_blocked(self, cfg):
        """多轮场景：AI 对两次不同提问给出相同回复，第二次应被拦截。"""
        ai = FakeAI(reply="这是AI回复")
        h = MessageHandler(cfg, ai, contexts := ContextManager(4))
        assert h.handle(msg("小特，今天天气如何")) is not None
        assert h.handle(msg("小特，你吃饭了吗")) is None  # 与上一条完全相同 → 拦截

    def test_similar_reply_blocked(self, cfg):
        ai = FakeAI(reply="今天天气真不错啊，适合出去玩")
        h = MessageHandler(cfg, FakeAI(reply="今天天气真不错啊，适合出去逛街"), ContextManager(4))
        # 直接往发送记录里塞相似内容模拟
        h.record_sent("测试群", "今天天气真不错啊，适合出去玩")
        assert h.handle(msg("小特，出去玩怎么样")) is None

    def test_different_reply_passes(self, cfg):
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        h.record_sent("测试群", "今天天气真不错啊")
        assert h.handle(msg("小特，讲个笑话听听")) is not None

    def test_chats_independent(self, cfg):
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        h.record_sent("群A", "这是AI回复")
        # 群B 没有发送记录，不受群A 影响
        assert h.handle(msg("小特你好", chat="群B")) is not None

    def test_record_sent_success_path(self, cfg):
        """main 层发送成功后调用 record_sent 的完整链路。"""
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        reply = h.handle(msg("小特你好"))
        # handle 内部已记录（纯文本），直接再发同样内容会被拦截
        assert h.is_duplicate("测试群", "这是AI回复")

    def test_system_prompt_rendered(self, cfg, handler):
        handler.handle(msg("小特在吗"))
        # 通过重新构造 handler 校验角色卡渲染
        assert handler.roles.system_prompt() == "你是 TedaBot"


class TestErrors:
    def test_ai_failure_returns_none(self, cfg):
        h = MessageHandler(cfg, FakeAI(fail=True), ContextManager(4))
        assert h.handle(msg("小特你好")) is None
