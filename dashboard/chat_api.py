"""AI 聊天模块：独立的网页聊天服务（Flask 蓝图 + 会话管理）。

与微信机器人主链路完全解耦：
- 可注册进 DashboardServer（随 main.py 启动），
- 也可通过 chat_server.py 独立运行（无需启动 main.py / 微信客户端）。

设计要点：
1. 复用 bot.role_cards.RoleCards 加载人设卡（中英文键名 JSON 卡 + 身份保护指令），
   支持网页端快速切换角色。
2. 复用 bot.ai_backend.AIBackend 的 OpenAI 兼容调用、思考内容清洗
   （strip_thinking / looks_like_cot / 二次提炼）能力。
3. 思考内容与正式回复分离返回：思考内容仅用于网页"思考过程"展示块，
   永远不会进入微信发送链路（遵守项目硬约束）。
4. 会话（Session）以 session_id 隔离，线程安全；每个会话独立的 ChatContext
   保留多轮上下文，支持从客户端恢复历史（页面刷新后 AI 记忆不丢）。
"""

import re
import threading
import time
import uuid

# 与 ai_backend 同源的思考标签匹配：在"清洗"之前先把标签块捕获出来，
# 供前端"思考过程"展示块使用（ai_backend 只负责丢弃，不负责保留）
_THINK_BLOCK_RE = re.compile(
    r"<\s*(think|thinking|reasoning|thought)\s*>(.*?)<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_THINK_OPEN_RE = re.compile(
    r"<\s*(?:think|thinking|reasoning|thought)\s*>", re.IGNORECASE
)

# 输入边界：单条消息最大长度（防止异常大请求拖垮免费接口）
MAX_MESSAGE_LEN = 4000
# 每个会话本地保留的最大消息条数（超出淘汰最旧的）
MAX_SESSION_MESSAGES = 200


def extract_thinking(raw: str) -> tuple:
    """从模型原始输出中分离"思考内容"与"最终回答"。

    返回 (answer, thinking)：
    - answer: 经 strip_thinking 清洗后的正式回复（可能为空）
    - thinking: 标签块内的思考文本（已去除标签本身），可能为空
    未闭合的思考标签（开标签后全部内容）同样视为思考内容。
    """
    from bot.ai_backend import strip_thinking

    if not raw:
        return "", ""
    blocks = [m.group(2).strip() for m in _THINK_BLOCK_RE.finditer(raw)]
    # 未闭合标签：strip_thinking 会丢弃，这里把其内部文本捕获为思考
    open_m = _THINK_OPEN_RE.search(raw)
    if open_m and not _THINK_BLOCK_RE.search(raw):
        blocks.append(raw[open_m.end():].strip())
    thinking = "\n\n".join(b for b in blocks if b)
    answer = strip_thinking(raw)
    return answer, thinking


class ChatService:
    """网页聊天会话服务：角色卡 + 上下文 + AI 调用 + 思考内容分离。

    用法：
        service = ChatService(cfg)                     # 自建 RoleCards/AIBackend
        service = ChatService(cfg, roles=r, ai=a)      # 复用主进程已有组件
        result = service.send(session_id, "你好")
    """

    def __init__(self, cfg: dict, roles=None, ai=None):
        ai_cfg = dict(cfg.get("ai") or {})
        self._cfg = cfg

        # AI 后端：优先复用主进程实例（共享重试/提炼/清洗逻辑），否则自建
        if ai is None:
            from bot.ai_backend import AIBackend
            ai = AIBackend(ai_cfg)
        self._ai = ai

        # 人设卡：优先复用主进程 RoleCards，否则按配置自建
        if roles is None:
            from bot.role_cards import RoleCards
            roles = RoleCards(cfg.get("roles"), (cfg.get("bot") or {}).get("name", ""))
        self._roles = roles

        self._max_rounds = max(1, int(ai_cfg.get("max_context_rounds", 8)))
        self._context_max_age = float(ai_cfg.get("context_max_age", 1800) or 0)
        self._request_interval = float(ai_cfg.get("request_interval", 1.0) or 0)

        self._sessions: dict = {}
        self._lock = threading.Lock()
        self._last_request_ts = 0.0

    # ------------------------------------------------------------------
    #  会话管理
    # ------------------------------------------------------------------

    def create_session(self, role: str = None, user: str = "网页用户",
                       history: list = None) -> dict:
        """创建新会话；history 用于页面刷新后恢复 AI 上下文。"""
        role_key = role if role in self._roles.cards else self._roles.default
        session_id = uuid.uuid4().hex[:16]

        from bot.ai_backend import ChatContext
        ctx = ChatContext(self._max_rounds)
        if history:
            ctx.restore(history)

        session = {
            "id": session_id,
            "role": role_key,
            "user": str(user or "网页用户")[:50],
            "ctx": ctx,
            "created": time.time(),
        }
        with self._lock:
            self._sessions[session_id] = session
        return {
            "session_id": session_id,
            "role": role_key,
            "role_name": self._roles.get_card(role_key).get("name", role_key),
            "history": self.history(session_id),
        }

    def _get_session(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError("会话不存在或已过期")
        return session

    def clear_session(self, session_id: str):
        """清空会话上下文（保留 session 本身，前端同时清空本地历史）。"""
        with self._lock:
            session = self._get_session(session_id)
        session["ctx"].clear()

    def history(self, session_id: str) -> list:
        """导出会话历史（含时间戳，供前端恢复展示）。"""
        with self._lock:
            session = self._get_session(session_id)
        return session["ctx"].dump()

    def list_roles(self) -> dict:
        """可用人设卡列表（供前端快速切换）。"""
        cards = [
            {"id": key, "name": card.get("name", key)}
            for key, card in self._roles.cards.items()
        ]
        return {"default": self._roles.default, "roles": cards}

    # ------------------------------------------------------------------
    #  消息发送
    # ------------------------------------------------------------------

    def send(self, session_id: str, message: str) -> dict:
        """发送一条消息并生成回复。

        返回 {"status": "ok", "reply", "thinking", "elapsed_ms"}；
        AI 重试耗尽时抛出 AIBackendError（由路由层转换为错误响应）。
        """
        if not isinstance(message, str) or not message.strip():
            raise ValueError("消息不能为空")
        message = message.strip()
        if len(message) > MAX_MESSAGE_LEN:
            raise ValueError(f"消息过长（最多 {MAX_MESSAGE_LEN} 字）")

        with self._lock:
            session = self._get_session(session_id)
            self._throttle_locked()

        role_key = session["role"]
        system_prompt = self._roles.system_prompt(role_key, session["user"])
        history = session["ctx"].snapshot(
            self._context_max_age if self._context_max_age > 0 else None
        )

        start = time.monotonic()
        # 先记录用户消息（与主链路一致：失败时上下文也保留该条）
        session["ctx"].add("user", message)

        answer, thinking = self._generate(system_prompt, history, message)
        session["ctx"].add("assistant", answer)

        return {
            "status": "ok",
            "reply": answer,
            "thinking": thinking,
            "role": role_key,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }

    def _throttle_locked(self):
        """请求间隔限流：两次 AI 请求之间至少间隔 ai.request_interval 秒。"""
        now = time.monotonic()
        wait = self._last_request_ts + self._request_interval - now
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _generate(self, system_prompt: str, history: list, message: str) -> tuple:
        """调用模型并返回 (answer, thinking)；含重试与思考内容二次提炼。

        每轮尝试：先分离标签思考块与回答；回答为空或疑似散文式 CoT 时，
        走 AIBackend 的提炼器复用最终回答；提炼失败则整轮作废重试。
        """
        last_err = None
        for attempt in range(self._ai.max_retries):
            try:
                raw = self._call_model(system_prompt, history, message)
                answer, thinking = extract_thinking(raw)

                if answer and not self._looks_like_cot(answer):
                    return answer, thinking

                # 回答缺失或疑似散文式思考 → 提炼复用其中的最终回答
                candidate = answer or raw
                thinking = f"{thinking}\n\n{answer}".strip() if answer else (thinking or raw)
                if self._ai.cot_refine:
                    task_id = uuid.uuid4().hex[:8]
                    refined = self._ai._refine_content(task_id, candidate)
                    if refined:
                        return refined, thinking

                raise ValueError("回复为空或含思考内容")
            except Exception as e:
                last_err = e
                import logging
                logging.getLogger("teda_bot.chat").warning(
                    "聊天请求失败(第%d次): %s", attempt + 1, e)

        from bot.ai_backend import AIBackendError
        raise AIBackendError(f"AI 调用失败（已重试{self._ai.max_retries}次）: {last_err}")

    def _call_model(self, system_prompt: str, history: list, message: str) -> str:
        """单次 OpenAI 兼容调用（复用 AIBackend 的客户端与禁思考参数）。"""
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})
        resp = self._ai.client.chat.completions.create(
            model=self._ai.model,
            messages=messages,
            temperature=self._ai.temperature,
            extra_body=self._ai._build_extra_body() or None,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def _looks_like_cot(text: str) -> bool:
        """代理到 ai_backend.looks_like_cot（延迟导入便于测试替换）。"""
        from bot.ai_backend import looks_like_cot
        return looks_like_cot(text)


# ----------------------------------------------------------------------
#  Flask 蓝图
# ----------------------------------------------------------------------

def create_chat_blueprint(service: ChatService):
    """构建聊天 API 蓝图（挂到 DashboardServer 或独立 app 均可）。"""
    from flask import Blueprint, jsonify, request

    bp = Blueprint("chat_api", __name__)

    def _err(msg: str, code: int):
        return jsonify({"status": "error", "error": msg}), code

    def _json_body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    @bp.route("/api/chat/roles", methods=["GET"])
    def _roles():
        """人设卡列表：GET /api/chat/roles"""
        return jsonify(service.list_roles())

    @bp.route("/api/chat/session", methods=["POST"])
    def _create_session():
        """创建会话：POST {role?, user?, history?}"""
        body = _json_body()
        history = body.get("history")
        if history is not None and not isinstance(history, list):
            return _err("history 必须是消息数组", 400)
        try:
            data = service.create_session(
                role=body.get("role"),
                user=body.get("user") or "网页用户",
                history=history,
            )
        except KeyError as e:
            return _err(str(e), 404)
        return jsonify(data)

    @bp.route("/api/chat/send", methods=["POST"])
    def _send():
        """发送消息：POST {session_id, message}"""
        body = _json_body()
        session_id = str(body.get("session_id", ""))
        try:
            result = service.send(session_id, body.get("message"))
        except KeyError:
            return _err("会话不存在或已过期，请刷新页面重建会话", 404)
        except ValueError as e:
            return _err(str(e), 400)
        except Exception as e:  # AIBackendError 及其他最终失败
            return _err(f"AI 生成失败: {e}", 502)
        return jsonify(result)

    @bp.route("/api/chat/history", methods=["GET"])
    def _history():
        """查询会话历史：GET /api/chat/history?session_id=xxx"""
        session_id = request.args.get("session_id", "")
        try:
            return jsonify({"session_id": session_id,
                            "history": service.history(session_id)})
        except KeyError:
            return _err("会话不存在或已过期", 404)

    @bp.route("/api/chat/clear", methods=["POST"])
    def _clear():
        """清空会话上下文：POST {session_id}"""
        body = _json_body()
        session_id = str(body.get("session_id", ""))
        try:
            service.clear_session(session_id)
        except KeyError:
            return _err("会话不存在或已过期", 404)
        return jsonify({"status": "ok"})

    return bp


def create_chat_app(cfg: dict, roles=None, ai=None):
    """构建可独立运行的 Flask 应用（聊天页面 + 静态资源 + 聊天 API）。

    chat_server.py 独立入口使用；DashboardServer 也可复用此工厂。
    """
    from flask import Flask, send_from_directory

    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pages_dir = os.path.join(base_dir, "tedabot-dashboard", "pages")
    assets_dir = os.path.join(base_dir, "tedabot-dashboard", "assets")

    app = Flask(__name__, static_folder=None)
    service = ChatService(cfg, roles=roles, ai=ai)
    app.register_blueprint(create_chat_blueprint(service))
    # 挂到扩展上，便于测试与调试访问
    app.extensions["chat_service"] = service

    @app.route("/")
    def _index():
        return send_from_directory(pages_dir, "chat.html")

    @app.route("/chat.html")
    def _chat_page():
        return send_from_directory(pages_dir, "chat.html")

    # 允许其余 Dashboard 页面：独立模式下"返回主界面"也能正常跳转
    _ALLOWED_PAGES = (
        "dashboard", "scenarios", "roles", "trigger",
        "proactive", "logs", "layouts",
    )

    @app.route("/<page_name>.html")
    def _page(page_name):
        from flask import abort
        if page_name not in _ALLOWED_PAGES:
            abort(404)
        return send_from_directory(pages_dir, f"{page_name}.html")

    @app.route("/assets/<path:filename>")
    def _assets(filename):
        return send_from_directory(assets_dir, filename)

    @app.route("/health")
    def _health():
        from flask import jsonify
        return jsonify({"status": "ok", "module": "chat"})

    return app
