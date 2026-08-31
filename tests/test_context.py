import time

from bot.ai_backend import ChatContext, ContextManager


class TestChatContext:
    def test_add_and_snapshot(self):
        ctx = ChatContext(max_rounds=8)
        ctx.add("user", "你好")
        ctx.add("assistant", "你好呀")
        snap = ctx.snapshot()
        assert snap == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ]

    def test_snapshot_strips_ts(self):
        """snapshot 返回的消息不应携带时间戳（AI 只看 role/content）。"""
        ctx = ChatContext()
        ctx.add("user", "hi")
        assert all(set(m.keys()) == {"role", "content"} for m in ctx.snapshot())

    def test_max_age_boundary(self):
        """时间边界：过期消息不再带入，新消息保留。"""
        ctx = ChatContext()
        ctx.add("user", "旧消息")
        # 手动把首条时间戳改旧
        ctx._messages[0]["ts"] = time.time() - 3600
        ctx.add("assistant", "新回复")
        snap = ctx.snapshot(1800)
        assert [m["content"] for m in snap] == ["新回复"]
        # 不传 max_age 时全部保留
        assert len(ctx.snapshot()) == 2

    def test_trim_old_rounds(self):
        ctx = ChatContext(max_rounds=2)
        for i in range(10):
            ctx.add("user", f"u{i}")
            ctx.add("assistant", f"a{i}")
        snap = ctx.snapshot()
        assert len(snap) == 4  # 保留最近 2 轮
        assert snap[0]["content"] == "u8"
        assert snap[-1]["content"] == "a9"

    def test_clear(self):
        ctx = ChatContext(max_rounds=2)
        ctx.add("user", "hi")
        ctx.clear()
        assert ctx.snapshot() == []


class TestContextManager:
    def test_sessions_isolated(self):
        m = ContextManager(max_rounds=4)
        m.get("群A").add("user", "a")
        m.get("群B").add("user", "b")
        assert m.get("群A").snapshot()[0]["content"] == "a"
        assert m.get("群B").snapshot()[0]["content"] == "b"

    def test_clear_session(self):
        m = ContextManager()
        m.get("群A").add("user", "a")
        m.clear("群A")
        assert m.get("群A").snapshot() == []
