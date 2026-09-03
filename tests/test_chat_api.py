"""AI 聊天模块单元测试：dashboard/chat_api.py + chat_server.py。

覆盖：角色卡列表、消息发送、思考内容分离（标签/未闭合/散文式 CoT）、
二次提炼、重试与失败、输入校验、会话管理与历史恢复、清空、
身份保护指令、独立 app 页面托管。
全部使用假 OpenAI 客户端，无真实网络请求。
"""

import os
import time
from types import SimpleNamespace

import pytest

from dashboard.chat_api import ChatService, create_chat_app, extract_thinking


# ----------------------------------------------------------------------
#  假 OpenAI 客户端：按队列依次返回预设响应（字符串）或抛出异常
# ----------------------------------------------------------------------

class _Completions:
    def __init__(self, outer):
        self._outer = outer

    def create(self, model=None, messages=None, **kwargs):
        self._outer.calls.append({"model": model, "messages": messages})
        item = self._outer.queue.pop(0) if self._outer.queue else "默认回复"
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))]
        )


class FakeClient:
    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=_Completions(self))


# ----------------------------------------------------------------------
#  夹具
# ----------------------------------------------------------------------

def _make_cfg() -> dict:
    json_card = os.path.join(
        os.path.dirname(__file__), "..", "角色卡_病娇.json"
    )
    return {
        "bot": {"name": "测试Bot"},
        "ai": {
            "model": "auto",
            "api_key": "test-key",
            "base_url": "http://localhost:9/v1",
            "max_retries": 2,
            "retry_backoff": 0,          # 测试中不等待
            "timeout": 5,
            "request_interval": 0,       # 测试中不限流
            "max_context_rounds": 8,
            "context_max_age": 1800,
            "cot_refine": True,
            "disable_thinking": True,
        },
        "roles": {
            "default": "assistant",
            "cards": {
                "assistant": {"name": "通用助手", "prompt": "你是 {bot_name}"},
                "yandere": {"file": json_card},
            },
        },
    }


@pytest.fixture()
def app_svc():
    """返回 (Flask test client, ChatService, FakeClient)。"""
    app = create_chat_app(_make_cfg())
    service = app.extensions["chat_service"]
    fake = FakeClient()
    service._ai.client = fake  # 注入假客户端，隔离真实网络
    app.config["TESTING"] = True
    return app.test_client(), service, fake


def _create_session(api, role=None, history=None) -> str:
    resp = api.post("/api/chat/session", json={
        **({"role": role} if role else {}),
        **({"history": history} if history else {}),
    })
    assert resp.status_code == 200
    return resp.get_json()["session_id"]


# 散文式思考样例：命中多个特征短语，looks_like_cot 应判定为 True
_PROSE_COT = "嗯，用户发来一个问题。我需要分析一下这个请求，首先，让我梳理角色设定，确认是否符合人设要求。"


# ----------------------------------------------------------------------
#  思考内容分离（纯函数）
# ----------------------------------------------------------------------

class TestExtractThinking:
    def test_plain_answer_no_thinking(self):
        answer, thinking = extract_thinking("你好呀！")
        assert answer == "你好呀！"
        assert thinking == ""

    def test_tagged_thinking_split(self):
        answer, thinking = extract_thinking("<think>用户想打招呼</think>你好呀！")
        assert answer == "你好呀！"
        assert thinking == "用户想打招呼"

    def test_unclosed_tag_all_thinking(self):
        answer, thinking = extract_thinking("<think>只有思考没有回答")
        assert answer == ""
        assert "只有思考没有回答" in thinking

    def test_empty(self):
        assert extract_thinking("") == ("", "")


# ----------------------------------------------------------------------
#  REST API
# ----------------------------------------------------------------------

class TestRolesApi:
    def test_list_roles(self, app_svc):
        api, _, _ = app_svc
        resp = api.get("/api/chat/roles")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["default"] == "assistant"
        ids = [r["id"] for r in data["roles"]]
        assert "assistant" in ids and "yandere" in ids


class TestSessionApi:
    def test_create_session_default_role(self, app_svc):
        api, _, _ = app_svc
        resp = api.post("/api/chat/session", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["role"] == "assistant"
        assert data["role_name"] == "通用助手"
        assert data["history"] == []

    def test_create_session_invalid_role_falls_back(self, app_svc):
        api, _, _ = app_svc
        resp = api.post("/api/chat/session", json={"role": "不存在"})
        assert resp.get_json()["role"] == "assistant"

    def test_history_restore(self, app_svc):
        api, _, _ = app_svc
        history = [
            {"role": "user", "content": "早", "ts": time.time() - 60},
            {"role": "assistant", "content": "早呀", "ts": time.time() - 59},
        ]
        sid = _create_session(api, history=history)
        resp = api.get(f"/api/chat/history?session_id={sid}")
        data = resp.get_json()
        assert [m["content"] for m in data["history"]] == ["早", "早呀"]

    def test_history_unknown_session_404(self, app_svc):
        api, _, _ = app_svc
        assert api.get("/api/chat/history?session_id=nope").status_code == 404


class TestSendApi:
    def test_send_ok(self, app_svc):
        api, svc, fake = app_svc
        fake.queue.append("你好呀！")
        sid = _create_session(api)
        resp = api.post("/api/chat/send", json={"session_id": sid, "message": "你好"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["status"] == "ok"
        assert data["reply"] == "你好呀！"
        assert data["thinking"] == ""
        # 上下文已记录两条消息
        assert len(svc.history(sid)) == 2

    def test_send_system_prompt_has_bot_name_and_identity_guard(self, app_svc):
        api, svc, fake = app_svc
        fake.queue.append("回复")
        sid = _create_session(api)
        api.post("/api/chat/send", json={"session_id": sid, "message": "hi"})
        system_msg = fake.calls[0]["messages"][0]
        assert system_msg["role"] == "system"
        assert "测试Bot" in system_msg["content"]

    def test_json_card_appends_identity_guard(self, app_svc):
        api, svc, fake = app_svc
        fake.queue.append("回复")
        sid = _create_session(api, role="yandere")
        api.post("/api/chat/send", json={"session_id": sid, "message": "hi"})
        system_msg = fake.calls[0]["messages"][0]["content"]
        assert "身份保护" in system_msg  # JSON 角色卡自动追加防穿帮指令

    def test_thinking_content_returned_separately(self, app_svc):
        api, svc, fake = app_svc
        fake.queue.append("<think>用户在打招呼，我要热情回应</think>你好呀！")
        sid = _create_session(api)
        data = api.post("/api/chat/send", json={
            "session_id": sid, "message": "你好"}).get_json()
        assert data["reply"] == "你好呀！"
        assert "热情回应" in data["thinking"]

    def test_unclosed_thinking_refined(self, app_svc):
        api, svc, fake = app_svc
        fake.queue.append("<think>只有思考没有成型的回答，用户想要一句问候")
        fake.queue.append("晚上好呀，吃了吗？")  # 提炼器输出
        sid = _create_session(api)
        data = api.post("/api/chat/send", json={
            "session_id": sid, "message": "在吗"}).get_json()
        assert data["reply"] == "晚上好呀，吃了吗？"
        assert "只有思考" in data["thinking"]

    def test_prose_cot_detected_and_refined(self, app_svc):
        from bot.ai_backend import looks_like_cot
        assert looks_like_cot(_PROSE_COT)  # 前置：样例确实命中检测器

        api, svc, fake = app_svc
        fake.queue.append(_PROSE_COT)
        fake.queue.append("（轻笑）问吧，我在听。")  # 提炼器输出
        sid = _create_session(api)
        data = api.post("/api/chat/send", json={
            "session_id": sid, "message": "你猜"}).get_json()
        assert data["reply"] == "（轻笑）问吧，我在听。"
        assert _PROSE_COT[:10] in data["thinking"]  # 原思考保留进展示块

    def test_retry_then_success(self, app_svc):
        api, svc, fake = app_svc
        svc._ai.cot_refine = False  # 关闭提炼，验证重试路径
        fake.queue.append(_PROSE_COT)      # 第1次：散文式 CoT → 作废重试
        fake.queue.append("最终回答")       # 第2次：干净回复
        sid = _create_session(api)
        data = api.post("/api/chat/send", json={
            "session_id": sid, "message": "hi"}).get_json()
        assert data["reply"] == "最终回答"

    def test_all_retries_exhausted_502(self, app_svc):
        api, svc, fake = app_svc
        svc._ai.cot_refine = False
        fake.queue.append(Exception("boom"))
        fake.queue.append(Exception("boom"))
        sid = _create_session(api)
        resp = api.post("/api/chat/send", json={"session_id": sid, "message": "hi"})
        assert resp.status_code == 502
        assert resp.get_json()["status"] == "error"

    def test_empty_message_400(self, app_svc):
        api, _, _ = app_svc
        sid = _create_session(api)
        assert api.post("/api/chat/send", json={
            "session_id": sid, "message": "   "}).status_code == 400

    def test_oversize_message_400(self, app_svc):
        api, _, _ = app_svc
        sid = _create_session(api)
        assert api.post("/api/chat/send", json={
            "session_id": sid, "message": "a" * 4001}).status_code == 400

    def test_unknown_session_404(self, app_svc):
        api, _, _ = app_svc
        assert api.post("/api/chat/send", json={
            "session_id": "ghost", "message": "hi"}).status_code == 404

    def test_context_brought_to_model(self, app_svc):
        api, svc, fake = app_svc
        fake.queue.append("第1答")
        fake.queue.append("第2答")
        sid = _create_session(api)
        api.post("/api/chat/send", json={"session_id": sid, "message": "第一问"})
        api.post("/api/chat/send", json={"session_id": sid, "message": "第二问"})
        second = fake.calls[1]["messages"]
        # 第二次请求应带第一轮上下文：system + user1 + assistant1 + user2
        assert [m["role"] for m in second] == [
            "system", "user", "assistant", "user"]


class TestClearApi:
    def test_clear_session(self, app_svc):
        api, svc, fake = app_svc
        fake.queue.append("回复")
        sid = _create_session(api)
        api.post("/api/chat/send", json={"session_id": sid, "message": "hi"})
        assert svc.history(sid)
        resp = api.post("/api/chat/clear", json={"session_id": sid})
        assert resp.status_code == 200
        assert svc.history(sid) == []

    def test_clear_unknown_session_404(self, app_svc):
        api, _, _ = app_svc
        assert api.post("/api/chat/clear", json={
            "session_id": "ghost"}).status_code == 404


# ----------------------------------------------------------------------
#  独立运行 app（页面托管）
# ----------------------------------------------------------------------

class TestStandaloneApp:
    def test_chat_page_served(self, app_svc):
        api, _, _ = app_svc
        resp = api.get("/chat.html")
        assert resp.status_code == 200
        assert "AI 聊天".encode() in resp.data

    def test_assets_served(self, app_svc):
        api, _, _ = app_svc
        for name in ("chat-bus.js", "chat-virtual-list.js", "chat-store.js", "chat-app.js"):
            assert api.get(f"/assets/{name}").status_code == 200, name

    def test_unknown_page_404(self, app_svc):
        api, _, _ = app_svc
        assert api.get("/secret.html").status_code == 404

    def test_health(self, app_svc):
        api, _, _ = app_svc
        assert api.get("/health").get_json()["module"] == "chat"

    def test_service_independent_from_main(self):
        """ChatService 自建组件时不依赖主进程任何运行时对象。"""
        service = ChatService(_make_cfg())
        service._ai.client = FakeClient("你好")
        result = service.send(service.create_session()["session_id"], "hi")
        assert result["status"] == "ok"
