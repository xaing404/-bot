"""微信客户端封装：基于 wxauto4（微信 4.x）轮询消息并回复。

两种工作方式（自动选择，优先前者）：
1. 独立聊天子窗口：在微信中双击白名单群/好友，弹出独立聊天窗口后，
   机器人直接轮询该窗口，**不干扰主窗口使用**（推荐）。
2. 主窗口切换：无独立窗口时，轮询会依次 ChatWith 切换到各会话拉取消息，
   此期间尽量不要手动操作微信主窗口。

运行前提：微信 PC 4.x 客户端已登录且主窗口已打开。
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field

from bot.dedup import MessageDedup
from bot.matcher import detect_at

log = logging.getLogger("teda_bot.wechat")


@dataclass
class IncomingMessage:
    chat_name: str          # 会话名（群名或好友昵称）
    sender: str             # 发送者昵称
    content: str            # 文本内容
    is_group: bool = False
    is_at: bool = False
    timestamp: float = field(default_factory=time.time)


class WeChatClient:
    """轮询配置中的群聊/私聊白名单，产出 IncomingMessage（增量去重）。"""

    def __init__(self, cfg: dict):
        from wxauto4 import WeChat  # 微信 4.x 自动化库

        bot_cfg = cfg.get("bot", {})
        wechat_cfg = cfg.get("wechat", {})
        self.group_whitelist = list(wechat_cfg.get("group_whitelist") or [])
        self.private_whitelist = list(wechat_cfg.get("private_whitelist") or [])
        self.targets = self.group_whitelist + self.private_whitelist

        try:
            self.wx = WeChat(ads=False)  # ads=False 关闭启动广告输出
        except Exception as e:
            raise RuntimeError(
                "无法连接微信窗口。请确认：微信 4.x 已登录、主窗口已打开（不能只挂在托盘）。"
            ) from e

        # 自动获取本账号昵称：用于 @识别 和过滤自己发的消息
        try:
            self.bot_name = self.wx.GetMyInfo().get("nickname") or bot_cfg.get("name", "")
        except Exception:
            self.bot_name = bot_cfg.get("name", "")
        if bot_cfg.get("name") and bot_cfg["name"] != self.bot_name:
            log.warning("config.yaml 中 bot.name=%s 与实际微信昵称=%s 不一致，@识别以实际昵称为准",
                        bot_cfg["name"], self.bot_name)

        # 每个会话的已见消息 id 队列 + 全局指纹去重（双保险）
        self._seen: dict = {t: deque(maxlen=500) for t in self.targets}
        dedup_window = float(wechat_cfg.get("dedup_window", 1800))
        self._dedup = MessageDedup(window=dedup_window)

        # 提示用户打开独立聊天窗口以避免干扰
        self._subwindows: dict = {}
        self._check_subwindows()

        log.info("机器人身份: %s；监听目标: %s", self.bot_name, self.targets)

    # ---------- 窗口管理 ----------

    def _check_subwindows(self):
        """尝试为每个目标绑定独立聊天子窗口。"""
        for target in self.targets:
            try:
                sub = self.wx.GetSubWindow(target)
                if sub is not None:
                    self._subwindows[target] = sub
                    log.info("[%s] 已绑定独立聊天窗口", target)
            except Exception:
                pass
        unbound = [t for t in self.targets if t not in self._subwindows]
        if unbound:
            log.info("以下会话无独立窗口，将轮询时切换主窗口: %s（建议在微信中双击会话打开独立窗口）", unbound)

    def _get_chat(self, target: str):
        """返回可执行 GetAllMessage/SendMsg 的聊天窗口对象。优先独立子窗口。"""
        sub = self._subwindows.get(target)
        if sub is not None:
            return sub
        try:
            self.wx.ChatWith(target)  # 切换主窗口到该会话（会占用主窗口）
            return self.wx
        except Exception as e:
            log.error("切换到会话 [%s] 失败: %s", target, e)
            return None

    # ---------- 消息拉取 ----------

    def poll(self) -> list:
        """轮询所有白名单会话，返回新消息列表（已按 id 去重）。"""
        out = []
        for target in self.targets:
            chat = self._get_chat(target)
            if chat is None:
                continue
            try:
                msgs = chat.GetAllMessage()
            except Exception as e:
                log.error("拉取消息异常 [%s]: %s", target, e)
                continue
            seen = self._seen[target]
            for m in msgs or []:
                mid = getattr(m, "id", None) or getattr(m, "hash", None)
                if mid is not None and mid in seen:
                    continue
                sender = getattr(m, "sender", "") or ""
                content = (getattr(m, "content", "") or "").strip()
                # 内容指纹兜底：id 不稳定（UI 重建后全量重放）时，
                # 同一 (sender, content) 在去重窗口内只处理一次
                fps = [f"msg:{sender}|{content}"]
                if mid is not None:
                    fps.append(f"id:{mid}")
                if not self._dedup.filter_new(*fps):
                    continue
                if mid is not None:
                    seen.append(mid)
                item = self._convert(target, m)
                if item:
                    out.append(item)
        return out

    def _convert(self, chat_name: str, m) -> IncomingMessage | None:
        mtype = str(getattr(m, "type", "text")).lower()
        if "text" not in mtype:  # 只处理文本消息
            return None
        content = (getattr(m, "content", "") or "").strip()
        if not content:
            return None
        sender = getattr(m, "sender", "") or ""
        # 过滤自己发的消息（direction 或昵称双重判定）
        direction = str(getattr(m, "direction", "") or "").lower()
        if "send" in direction or sender == self.bot_name:
            return None
        is_at = detect_at(content, self.bot_name)
        is_group = chat_name != sender  # 私聊中会话名与发送者相同
        log.info("捕获消息 [%s/%s]: %s", chat_name, sender, content[:60])
        return IncomingMessage(chat_name, sender, content, is_group, is_at)

    # ---------- 发送 ----------

    def send(self, chat_name: str, text: str):
        """回复消息。独立子窗口直接发；主窗口模式带 who 定位会话。"""
        chat = self._subwindows.get(chat_name)
        try:
            if chat is not None:
                chat.SendMsg(text)
            else:
                self.wx.SendMsg(text, who=chat_name)
            # 登记已发送内容指纹，防止后续轮询把自己的消息当新消息处理（自环防护）
            self._dedup.mark(f"msg:{self.bot_name}|{text}")
            log.info("已回复 [%s]: %s", chat_name, text[:80])
        except Exception as e:
            log.error("发送消息失败 [%s]: %s", chat_name, e)

    def refresh_subwindows(self):
        """定期检查是否有新的独立窗口被打开。"""
        for target in self.targets:
            if target in self._subwindows:
                continue
            try:
                sub = self.wx.GetSubWindow(target)
                if sub is not None:
                    self._subwindows[target] = sub
                    log.info("[%s] 已绑定新打开的独立聊天窗口", target)
            except Exception:
                pass
