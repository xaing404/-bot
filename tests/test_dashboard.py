"""Dashboard Web 管理后台单元测试。

测试范围：
1. SharedState 线程安全容器的基本操作
2. DashboardServer Flask 应用 API 端点（使用 test_client）
3. 工具方法：时间格式化、日志解析
"""

import json
import os
import time

import pytest

from dashboard.state import SharedState
from dashboard.server import DashboardServer


# ---------- SharedState 测试 ----------

class TestSharedState:
    def test_bind_and_get(self):
        s = SharedState()
        s.bind(foo="bar", count=42)
        assert s.get("foo") == "bar"
        assert s.get("count") == 42
        assert s.get("missing", "default") == "default"

    def test_components_returns_copy(self):
        s = SharedState()
        s.bind(a=1)
        d = s.components
        d["b"] = 2
        # 修改副本不影响原数据
        assert s.get("b") is None

    def test_uptime_positive(self):
        s = SharedState()
        time.sleep(0.01)
        assert s.uptime_seconds > 0


# ---------- DashboardServer API 测试 ----------

class TestDashboardAPI:
    """使用 Flask test_client 测试 API 端点，不需要真正启动服务器。"""

    def _make_server(self, tmp_path):
        """构建带 mock 组件的 DashboardServer。"""
        cfg = {
            "bot": {"name": "TestBot"},
            "proactive": {"mode": "hybrid"},
            "ai": {"api_key": "sk-test1234567890abcdef"},
            "trigger": {"keywords": ["@TestBot"], "fuzzy": True},
            "logging": {"file": str(tmp_path / "test.log")},
            "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 8050},
        }

        # Mock 组件
        class MockContexts:
            def stats(self):
                return {
                    "group:test群": {"messages": 10, "last_ts": time.time() - 30},
                    "private:张三": {"messages": 5, "last_ts": time.time() - 120},
                }

        class MockHandler:
            reply_counts = {"group:test群": 8, "private:张三": 3}
            class _MockRoles:
                default = "assistant"
                class _Card:
                    name = "通用助手"
                    prompt = "你是 TestBot"
                    file_path = None
                cards = {"assistant": _Card()}
            roles = _MockRoles()

        class MockSendQueue:
            def stats(self):
                return {"pending": 2, "sent": 100, "failed": 1, "dropped": 0}

        class MockStore:
            def scenarios(self):
                return []

        return DashboardServer(
            cfg,
            send_queue=MockSendQueue(),
            contexts=MockContexts(),
            handler=MockHandler(),
            store=MockStore(),
        )

    def test_stats_endpoint(self, tmp_path):
        server = self._make_server(tmp_path)
        app = server._create_app()
        client = app.test_client()

        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["bot_name"] == "TestBot"
        assert data["proactive_mode"] == "hybrid"
        assert data["scenarios"]["total"] == 2
        assert data["queue"]["pending"] == 2
        assert data["queue"]["sent"] == 100
        assert "uptime" in data

    def test_scenarios_endpoint(self, tmp_path):
        server = self._make_server(tmp_path)
        app = server._create_app()
        client = app.test_client()

        resp = client.get("/api/scenarios")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        # 第一个应该是最近活跃的（30s前 < 120s前）
        assert data[0]["name"] == "test群"
        assert data[0]["replies"] == 8
        assert data[0]["messages"] == 10

    def test_roles_endpoint(self, tmp_path):
        server = self._make_server(tmp_path)
        app = server._create_app()
        client = app.test_client()

        resp = client.get("/api/roles")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["id"] == "assistant"
        assert data[0]["is_default"] is True
        assert data[0]["source"] == "inline"

    def test_config_endpoint_hides_api_key(self, tmp_path):
        server = self._make_server(tmp_path)
        app = server._create_app()
        client = app.test_client()

        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()
        # API key 应被脱敏
        assert "***" in data["ai"]["api_key"]
        assert "sk-test1234567890abcdef" not in data["ai"]["api_key"]

    def test_logs_endpoint(self, tmp_path):
        """测试日志读取：创建临时日志文件并验证 API 返回。"""
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "[2026-09-02 10:00:00][INFO   ][handler     ] 收到消息\n"
            "[2026-09-02 10:00:01][WARNING][send_queue  ] 队列接近满\n"
            "[2026-09-02 10:00:02][ERROR  ][ai_backend  ] AI调用失败\n",
            encoding="utf-8",
        )

        server = self._make_server(tmp_path)
        app = server._create_app()
        client = app.test_client()

        resp = client.get("/api/logs?lines=10")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_returned"] == 3
        levels = [line["level"] for line in data["lines"]]
        assert "INFO" in levels
        assert "WARN" in levels
        assert "ERROR" in levels

    def test_health_endpoint(self, tmp_path):
        server = self._make_server(tmp_path)
        app = server._create_app()
        client = app.test_client()

        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "uptime" in data
        assert "platform" in data

    def test_page_routing(self, tmp_path):
        """测试页面路由：所有 7 个页面都应返回 200。"""
        server = self._make_server(tmp_path)
        app = server._create_app()
        client = app.test_client()

        for page in ["dashboard", "scenarios", "roles", "trigger",
                      "proactive", "logs", "layouts"]:
            resp = client.get(f"/{page}.html")
            assert resp.status_code == 200, f"页面 {page} 返回 {resp.status_code}"

    def test_invalid_page_404(self, tmp_path):
        server = self._make_server(tmp_path)
        app = server._create_app()
        client = app.test_client()

        resp = client.get("/nonexistent.html")
        assert resp.status_code == 404


# ---------- 工具方法测试 ----------

class TestUtilities:
    def test_format_uptime_seconds(self):
        assert DashboardServer._format_uptime(45) == "45s"

    def test_format_uptime_minutes(self):
        assert DashboardServer._format_uptime(125) == "2m 5s"

    def test_format_uptime_hours(self):
        assert DashboardServer._format_uptime(7384) == "2h 3m"

    def test_format_uptime_days(self):
        assert DashboardServer._format_uptime(90061) == "1d 1h 1m"

    def test_format_age_recent(self):
        assert DashboardServer._format_age(23) == "23s前"

    def test_format_age_minutes(self):
        assert DashboardServer._format_age(300) == "5m前"

    def test_format_age_hours(self):
        assert DashboardServer._format_age(7200) == "2h前"

    def test_format_age_unknown(self):
        assert DashboardServer._format_age(-1) == "未知"

    def test_extract_timestamp(self):
        line = "[2026-09-02 10:00:00][INFO][handler] 收到消息"
        assert DashboardServer._extract_timestamp(line) == "2026-09-02 10:00:00"

    def test_extract_timestamp_empty(self):
        assert DashboardServer._extract_timestamp("短行") == ""
