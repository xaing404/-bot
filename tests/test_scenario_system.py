"""多场景智能回复系统测试：记忆隔离、持久化、场景级频率限制。"""

import pytest

from bot.ai_backend import ContextManager
from bot.handler import MessageHandler, scenario_key
from bot.memory_store import MemoryStore
from bot.rate_limit import ScenarioThrottle
from bot.wechat_client import IncomingMessage


class FakeAI:
    def __init__(self, reply="这是AI回复"):
        self.reply = reply
        self.calls = []

    def chat(self, system_prompt, history, user_msg):
        self.calls.append({"system": system_prompt, "history": history, "user": user_msg})
        return self.reply


class EchoAI:
    """回声 AI：回复中带上传入消息，避免固定回复触发重复拦截。"""

    def chat(self, system_prompt, history, user_msg):
        return f"回复[{user_msg}]"


def gmsg(content, chat="测试群", sender="张三", at=True):
    return IncomingMessage(chat_name=chat, sender=sender, content=content,
                           is_group=True, is_at=at)


def pmsg(content, chat="李四"):
    return IncomingMessage(chat_name=chat, sender=chat, content=content,
                           is_group=False, is_at=False)


def make_cfg(**overrides):
    cfg = {
        "bot": {"name": "TedaBot"},
        "ai": {"request_interval": 0, "max_context_rounds": 4},
        "trigger": {"keywords": ["小特"], "reply_on_at": True, "reply_private": True},
        "roles": {"default": "a", "cards": {"a": {"name": "助手", "prompt": "你是 {bot_name}"}}},
        "safety": {"enabled": True, "block_words": []},
    }
    cfg.update(overrides)
    return cfg


# ---------- 记忆持久化 ----------

class TestMemoryStore:
    def test_save_load_roundtrip(self, tmp_path):
        store = MemoryStore(str(tmp_path))
        msgs = [{"role": "user", "content": "你好", "ts": 123.0},
                {"role": "assistant", "content": "你好呀", "ts": 124.0}]
        store.save("group:测试群", msgs)
        assert store.load("group:测试群") == msgs

    def test_scenarios_isolated_files(self, tmp_path):
        """不同场景各自独立文件，互不干扰。"""
        store = MemoryStore(str(tmp_path))
        store.save("private:李四", [{"role": "user", "content": "私聊内容", "ts": 1.0}])
        store.save("group:李四", [{"role": "user", "content": "群聊内容", "ts": 2.0}])
        assert store.load("private:李四")[0]["content"] == "私聊内容"
        assert store.load("group:李四")[0]["content"] == "群聊内容"

    def test_invalid_chars_in_key(self, tmp_path):
        """场景名含文件系统非法字符时不报错、可回读。"""
        store = MemoryStore(str(tmp_path))
        store.save('group:a/b:c*?', [{"role": "user", "content": "x", "ts": 1.0}])
        assert store.load('group:a/b:c*?')[0]["content"] == "x"

    def test_load_missing_returns_empty(self, tmp_path):
        store = MemoryStore(str(tmp_path))
        assert store.load("group:不存在") == []

    def test_scenarios_listing(self, tmp_path):
        store = MemoryStore(str(tmp_path))
        store.save("group:群A", [])
        store.save("private:李四", [])
        # 文件名中的 ":" 会被替换为 "_"（Windows 文件系统限制）
        assert store.scenarios() == ["group_群A", "private_李四"]

    def test_disabled_no_files(self, tmp_path):
        store = MemoryStore(str(tmp_path), enabled=False)
        store.save("group:群A", [{"role": "user", "content": "x", "ts": 1.0}])
        assert store.load("group:群A") == []
        assert store.scenarios() == []


class TestContextPersistence:
    def test_record_persists_and_reload(self, tmp_path):
        """record 写入即持久化；新 ContextManager 从磁盘恢复记忆。"""
        store = MemoryStore(str(tmp_path))
        m1 = ContextManager(4, store=store)
        m1.record("group:测试群", "user", "记住我喜欢吃火锅")
        m1.record("group:测试群", "assistant", "好的")

        m2 = ContextManager(4, store=store)  # 模拟重启
        snap = m2.get("group:测试群").snapshot()
        assert [m["content"] for m in snap] == ["记住我喜欢吃火锅", "好的"]

    def test_restore_respects_max_rounds(self, tmp_path):
        store = MemoryStore(str(tmp_path))
        msgs = [{"role": "user", "content": f"u{i}", "ts": float(i)} for i in range(10)]
        store.save("group:群A", msgs)
        m = ContextManager(2, store=store)  # 2 轮 = 4 条
        assert [x["content"] for x in m.get("group:群A").snapshot()] == ["u6", "u7", "u8", "u9"]

    def test_clear_removes_persisted(self, tmp_path):
        store = MemoryStore(str(tmp_path))
        m = ContextManager(4, store=store)
        m.record("group:群A", "user", "x")
        m.clear("group:群A")
        assert store.load("group:群A") == []


# ---------- 场景键隔离 ----------

class TestScenarioKeys:
    def test_group_and_private_keys(self):
        assert scenario_key(gmsg("x")) == "group:测试群"
        assert scenario_key(pmsg("x")) == "private:李四"

    def test_same_name_group_private_isolated(self, tmp_path):
        """同名群聊与私聊的记忆完全隔离。"""
        store = MemoryStore(str(tmp_path))
        cfg = make_cfg(memory={"enabled": True, "dir": str(tmp_path)})
        contexts = ContextManager(4, store=store)
        h = MessageHandler(cfg, EchoAI(), contexts)
        h.handle(pmsg("我是私聊里的李四", chat="李四"))
        h.handle(gmsg("我是群里的消息", chat="李四"))
        private_hist = contexts.get("private:李四").snapshot()
        group_hist = contexts.get("group:李四").snapshot()
        assert len(private_hist) == 2
        assert len(group_hist) == 2
        assert "私聊" in private_hist[0]["content"]
        assert "群" in group_hist[0]["content"]


# ---------- 场景级回复频率限制 ----------

class TestScenarioThrottle:
    def test_first_reply_allowed(self):
        t = ScenarioThrottle(private_interval=30, group_interval=60)
        assert t.allow("private:李四")
        assert t.allow("group:测试群")

    def test_mark_blocks_within_interval(self):
        t = ScenarioThrottle(private_interval=30, group_interval=60)
        t.mark("private:李四")
        assert not t.allow("private:李四")

    def test_private_and_group_independent(self):
        t = ScenarioThrottle(private_interval=30, group_interval=60)
        t.mark("private:李四")
        assert t.allow("group:测试群")  # 不同场景互不影响

    def test_zero_interval_always_allowed(self):
        t = ScenarioThrottle(private_interval=0, group_interval=0)
        t.mark("group:测试群")
        assert t.allow("group:测试群")


class TestHandlerThrottle:
    def test_second_group_reply_blocked(self):
        """群聊冷却期内即使被@也不再回复（防刷屏优先）。"""
        cfg = make_cfg(reply_interval={"private": 0, "group": 60})
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert h.handle(gmsg("第一个问题")) is not None
        assert h.handle(gmsg("第二个问题")) is None

    def test_private_reply_interval(self):
        cfg = make_cfg(reply_interval={"private": 60, "group": 0})
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert h.handle(pmsg("第一句")) is not None
        assert h.handle(pmsg("第二句")) is None

    def test_chats_have_independent_cooldowns(self):
        cfg = make_cfg(reply_interval={"private": 60, "group": 60})
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert h.handle(gmsg("群A的问题", chat="群A")) is not None
        assert h.handle(gmsg("群B的问题", chat="群B")) is not None  # 不同群各自冷却

    def test_group_and_private_cooldowns_independent(self):
        cfg = make_cfg(reply_interval={"private": 60, "group": 60})
        h = MessageHandler(cfg, EchoAI(), ContextManager(4))
        assert h.handle(pmsg("私聊你好呀朋友", chat="张三")) is not None
        assert h.handle(gmsg("群里的大家好吗", chat="张三")) is not None

    def test_blocked_message_not_marked(self):
        """被敏感词拦截的消息不应启动冷却计时。"""
        cfg = make_cfg(reply_interval={"private": 0, "group": 60},
                       safety={"enabled": True, "block_words": ["敏感词"]})
        h = MessageHandler(cfg, FakeAI(), ContextManager(4))
        assert h.handle(gmsg("说个敏感词试试")) is None
        assert h.handle(gmsg("正常问题")) is not None  # 未进入冷却

    def test_stats_expose_scenarios(self):
        """状态监控数据：stats() 返回各场景记忆条数与最近活跃时间。"""
        h = MessageHandler(make_cfg(), FakeAI(), ContextManager(4))
        h.handle(gmsg("问题一"))
        h.handle(pmsg("你好", chat="李四"))
        stats = h.contexts.stats()
        assert stats["group:测试群"]["messages"] == 2
        assert stats["private:李四"]["messages"] == 2
        assert stats["group:测试群"]["last_ts"] > 0


# ---------- @ 提及优先响应 ----------

class TestAtPriority:
    def test_at_bypasses_cooldown(self):
        """冷却期内被 @ 的消息仍立即回复（最高优先级）。"""
        cfg = make_cfg(reply_interval={"private": 0, "group": 60})
        h = MessageHandler(cfg, EchoAI(), ContextManager(4))
        assert h.handle(gmsg("小特问题一", at=False)) is not None  # 关键词回复，触发冷却
        # 冷却中：关键词消息被拦截
        assert h.handle(gmsg("小特问题二", at=False)) is None
        # 被 @ 的消息绕过冷却立即回复
        assert h.handle(gmsg("@xiang 紧急问题", at=True)) is not None

    def test_at_reply_starts_new_cooldown(self):
        """@回复后冷却重新计时，随后普通消息仍被拦截；连续 @ 每次都回。"""
        cfg = make_cfg(reply_interval={"private": 0, "group": 60})
        h = MessageHandler(cfg, EchoAI(), ContextManager(4))
        assert h.handle(gmsg("小特第一问", at=True)) is not None
        assert h.handle(gmsg("小特第二问", at=False)) is None
        assert h.handle(gmsg("小特你好呀在吗", at=True)) is not None


# ---------- 自身消息识别 ----------

class TestSelfFilter:
    def test_is_self_normalized(self):
        from bot.wechat_client import is_self
        assert is_self("self", "xiang")          # wxauto4 回报自身消息的形式
        assert is_self(" Self ", "xiang")        # 大小写与空格变体
        assert is_self("xiang", "xiang")
        assert is_self(" xiang ", "xiang")       # 空格变体
        assert is_self("@xiang", "xiang")        # 带 @ 前缀
        assert is_self("xiang：", "xiang")        # 中文冒号后缀
        assert not is_self("xiang2", "xiang")    # 昵称超集不算
        assert not is_self("张三", "xiang")
        assert not is_self("", "xiang")          # 空发送者不误判

    def test_at_stripped_from_context(self):
        """存入记忆的群消息清除 @提及，AI 不会把 @机器人 当成别人的话。"""
        h = MessageHandler(make_cfg(), EchoAI(), ContextManager(4))
        h.handle(gmsg("@xiang 今天吃什么好"))
        history = h.contexts.get("group:测试群").snapshot()
        assert history[0]["content"] == "张三: 今天吃什么好"  # @xiang 已清除
        # 机器人自己的回复以 assistant 角色存放，且不含任何 @
        assert history[1]["role"] == "assistant"
        assert "@" not in history[1]["content"]
