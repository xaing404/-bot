"""Dashboard Flask 服务器：页面托管 + REST API。

在机器人主进程内以守护线程运行，不影响消息轮询主循环。
所有 API 为只读（查看配置/状态/日志），不修改任何运行时组件状态。
"""

import logging
import os
import threading
import time
from collections import deque

from .state import SharedState

log = logging.getLogger("teda_bot.dashboard")

# 项目根目录（dashboard/ 的上一级）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PAGES_DIR = os.path.join(_BASE_DIR, "tedabot-dashboard", "pages")
_ASSETS_DIR = os.path.join(_BASE_DIR, "tedabot-dashboard", "assets")


class DashboardServer:
    """Flask 应用工厂 + 守护线程启动器。

    用法：
        server = DashboardServer(cfg, send_queue=sq, contexts=ctx, ...)
        server.start()  # 非阻塞，后台线程
    """

    def __init__(self, cfg: dict, **runtime_components):
        self._cfg = cfg
        self._state = SharedState()
        self._state.bind(**runtime_components)
        self._thread = None
        self._app = None
        dash_cfg = cfg.get("dashboard") or {}
        self._host = dash_cfg.get("host", "127.0.0.1")
        self._port = int(dash_cfg.get("port", 8050))
        self._log_lines = int(dash_cfg.get("log_lines", 200))

    # ------------------------------------------------------------------
    #  生命周期
    # ------------------------------------------------------------------

    def start(self):
        """在守护线程中启动 Flask（非阻塞）。"""
        self._app = self._create_app()
        self._thread = threading.Thread(
            target=self._run_flask, name="dashboard-server", daemon=True
        )
        self._thread.start()
        log.info("Dashboard 已启动: http://%s:%d", self._host, self._port)

    def _run_flask(self):
        """Flask 内部服务器（开发模式，单线程足够：只读 dashboard）。"""
        try:
            self._app.run(
                host=self._host,
                port=self._port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
        except Exception as e:
            log.error("Dashboard 服务异常: %s", e)

    # ------------------------------------------------------------------
    #  Flask 应用构建
    # ------------------------------------------------------------------

    def _create_app(self):
        from flask import Flask, send_from_directory, jsonify, abort

        app = Flask(
            __name__,
            static_folder=None,  # 禁用默认 static，手动路由
        )

        # ---------- 页面托管 ----------

        @app.route("/")
        def _index():
            return send_from_directory(_PAGES_DIR, "dashboard.html")

        @app.route("/<page_name>.html")
        def _page(page_name):
            if page_name not in (
                "dashboard", "scenarios", "roles", "trigger",
                "proactive", "logs", "layouts",
            ):
                abort(404)
            return send_from_directory(_PAGES_DIR, f"{page_name}.html")

        @app.route("/assets/<path:filename>")
        def _assets(filename):
            return send_from_directory(_ASSETS_DIR, filename)

        # ---------- REST API ----------

        @app.route("/api/stats")
        def _api_stats():
            return jsonify(self._build_stats())

        @app.route("/api/scenarios")
        def _api_scenarios():
            return jsonify(self._build_scenarios())

        @app.route("/api/roles")
        def _api_roles():
            return jsonify(self._build_roles())

        @app.route("/api/config")
        def _api_config():
            return jsonify(self._build_config())

        @app.route("/api/logs")
        def _api_logs():
            from flask import request
            lines = int(request.args.get("lines", self._log_lines))
            return jsonify(self._build_logs(lines))

        @app.route("/api/health")
        def _api_health():
            return jsonify(self._build_health())

        return app

    # ------------------------------------------------------------------
    #  API 数据组装（只读访问运行时组件）
    # ------------------------------------------------------------------

    def _build_stats(self) -> dict:
        """仪表盘 KPI 总览。"""
        s = self._state
        ctx = s.get("contexts")
        handler = s.get("handler")
        sq = s.get("send_queue")
        cfg = s.get("cfg") or self._cfg

        # 场景统计
        ctx_stats = ctx.stats() if ctx else {}
        now = time.time()
        active_count = sum(
            1 for info in ctx_stats.values()
            if now - info.get("last_ts", 0) < 600
        )

        # 回复总数
        total_replies = sum(handler.reply_counts.values()) if handler else 0

        # 队列状态
        queue_stats = sq.stats() if sq else {"pending": 0, "sent": 0, "failed": 0, "dropped": 0}

        # 运行时长
        uptime = s.uptime_seconds
        uptime_str = self._format_uptime(uptime)

        return {
            "bot_name": (cfg.get("bot") or {}).get("name", "TedaBot"),
            "proactive_mode": (cfg.get("proactive") or {}).get("mode", "keyword"),
            "scenarios": {
                "total": len(ctx_stats),
                "active": active_count,
            },
            "replies": {
                "total": total_replies,
            },
            "queue": queue_stats,
            "uptime": uptime_str,
            "uptime_seconds": round(uptime, 0),
        }

    def _build_scenarios(self) -> list:
        """场景记忆列表。"""
        s = self._state
        ctx = s.get("contexts")
        handler = s.get("handler")
        store = s.get("store")

        ctx_stats = ctx.stats() if ctx else {}
        reply_counts = handler.reply_counts if handler else {}
        now = time.time()

        # 同时获取持久化场景列表（可能比内存中有更多历史场景）
        persisted = set(store.scenarios()) if store else set()

        results = []
        for key, info in sorted(ctx_stats.items(), key=lambda x: x[1].get("last_ts", 0), reverse=True):
            parts = key.split(":", 1)
            scene_type = parts[0] if len(parts) == 2 else "unknown"
            name = parts[1] if len(parts) == 2 else key
            last_ts = info.get("last_ts", 0)
            age = int(now - last_ts) if last_ts > 0 else -1
            results.append({
                "key": key,
                "type": scene_type,
                "name": name,
                "messages": info.get("messages", 0),
                "replies": reply_counts.get(key, 0),
                "last_active": self._format_age(age),
                "last_ts": last_ts,
                "persisted": key.replace(":", "_") in persisted or key in persisted,
            })

        # 补充仅持久化但当前不在内存中的场景
        mem_keys = set(ctx_stats.keys())
        if store:
            for pkey in store.scenarios():
                # 恢复原始场景键格式
                orig_key = pkey.replace("_", ":", 1) if "_" in pkey else pkey
                if orig_key not in mem_keys:
                    parts = orig_key.split(":", 1)
                    results.append({
                        "key": orig_key,
                        "type": parts[0] if len(parts) == 2 else "unknown",
                        "name": parts[1] if len(parts) == 2 else orig_key,
                        "messages": 0,
                        "replies": 0,
                        "last_active": "未活跃",
                        "last_ts": 0,
                        "persisted": True,
                    })

        return results

    def _build_roles(self) -> list:
        """角色卡列表。"""
        s = self._state
        handler = s.get("handler")

        if not handler or not handler.roles:
            return []

        roles = handler.roles
        default_name = roles.default
        results = []

        for card_id, card in roles.cards.items():
            results.append({
                "id": card_id,
                "name": card.name,
                "is_default": card_id == default_name,
                "source": "file" if hasattr(card, "file_path") and card.file_path else "inline",
                "prompt_preview": (card.prompt or "")[:100],
            })

        return results

    def _build_config(self) -> dict:
        """当前配置（只读展示）。"""
        cfg = self._state.get("cfg") or self._cfg

        # 只暴露非敏感字段
        ai_cfg = dict(cfg.get("ai") or {})
        if "api_key" in ai_cfg:
            api_key = ai_cfg["api_key"]
            ai_cfg["api_key"] = api_key[:8] + "***" if len(api_key) > 8 else "***"

        return {
            "trigger": cfg.get("trigger") or {},
            "proactive": cfg.get("proactive") or {},
            "ai": ai_cfg,
            "roles": {
                "default": (cfg.get("roles") or {}).get("default", ""),
                "card_count": len((cfg.get("roles") or {}).get("cards", {})),
            },
            "safety": cfg.get("safety") or {},
            "reply_interval": cfg.get("reply_interval") or {},
            "wechat": {
                "poll_interval": (cfg.get("wechat") or {}).get("poll_interval", 1.0),
                "group_whitelist": (cfg.get("wechat") or {}).get("group_whitelist", []),
                "private_whitelist": (cfg.get("wechat") or {}).get("private_whitelist", []),
            },
        }

    def _build_logs(self, max_lines: int) -> dict:
        """读取最新日志行。"""
        cfg = self._state.get("cfg") or self._cfg
        log_file = (cfg.get("logging") or {}).get("file", "logs/bot.log")
        log_path = log_file if os.path.isabs(log_file) else os.path.join(_BASE_DIR, log_file)

        lines = []
        if os.path.exists(log_path):
            try:
                # 高效读取文件末尾（不加载整个大文件）
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    # deque(maxlen=N) 自动丢弃旧行，只保留最后 N 行
                    tail = deque(f, maxlen=max_lines)
                    lines = list(tail)
            except Exception as e:
                lines = [f"[Dashboard] 读取日志失败: {e}"]
        else:
            lines = ["[Dashboard] 日志文件不存在，机器人可能尚未产生日志"]

        # 解析每行的级别用于前端着色
        parsed = []
        for line in lines:
            line = line.rstrip("\n\r")
            level = "INFO"
            # 日志格式：[时间戳][LEVEL   ][模块名] 消息
            # 级别名按 7 字符左对齐填充空格，如 [ERROR  ] [WARNING]
            if "[ERROR" in line or "[CRITICAL" in line:
                level = "ERROR"
            elif "[WARNING" in line:
                level = "WARN"
            elif "[SUCCESS" in line:
                level = "SUCCESS"
            elif "[TASK" in line:
                level = "TASK"

            parsed.append({
                "raw": line,
                "level": level,
                "timestamp": self._extract_timestamp(line),
            })

        return {
            "lines": parsed,
            "total_returned": len(parsed),
            "log_file": log_file,
        }

    def _build_health(self) -> dict:
        """系统健康指标。"""
        s = self._state
        import platform
        import os

        health = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_percent": None,
            "memory": None,
            "uptime": self._format_uptime(s.uptime_seconds),
        }

        # 尝试使用 psutil 获取精确指标（可选依赖）
        try:
            import psutil
            health["cpu_percent"] = round(psutil.cpu_percent(interval=0.5), 1)
            mem = psutil.virtual_memory()
            health["memory"] = {
                "used_mb": round(mem.used / 1024 / 1024, 0),
                "total_mb": round(mem.total / 1024 / 1024, 0),
                "percent": round(mem.percent, 1),
            }
        except ImportError:
            health["cpu_percent"] = "psutil 未安装"
            health["memory"] = None

        return health

    # ------------------------------------------------------------------
    #  工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """格式化运行时长：3h 24m / 45m / 12s。"""
        s = int(seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m {s}s"
        h, m = divmod(m, 60)
        if h < 24:
            return f"{h}h {m}m"
        d, h = divmod(h, 24)
        return f"{d}d {h}h {m}m"

    @staticmethod
    def _format_age(age_seconds: int) -> str:
        """格式化距上次活跃：23s前 / 5m前 / 2h前。"""
        if age_seconds < 0:
            return "未知"
        if age_seconds < 60:
            return f"{age_seconds}s前"
        m = age_seconds // 60
        if m < 60:
            return f"{m}m前"
        h = m // 60
        if h < 24:
            return f"{h}h前"
        d = h // 24
        return f"{d}d前"

    @staticmethod
    def _extract_timestamp(line: str) -> str:
        """从日志行中提取时间戳：[2026-09-02 10:00:00]..."""
        if len(line) >= 21 and line.startswith("["):
            return line[1:20]
        return ""
