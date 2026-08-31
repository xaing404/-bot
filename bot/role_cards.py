"""角色卡系统：支持 YAML 内联定义与 JSON 角色卡文件两种来源。

JSON 文件格式（兼容常见角色卡导出格式）：
{
  "role_card": {            # 或直接就是卡片内容本身
    "name": "...",
    "system_prompt": "...{{user}}..."   # {{user}} 会被替换为实际发送者昵称
  }
}

config.yaml 中的引用方式：
  cards:
    yandere:
      file: "角色卡_病娇.json"
或直接内联：
    xiang:
      name: "小象"
      prompt: "..."
"""

import json
import logging
import os

log = logging.getLogger("teda_bot.roles")


class RoleCards:
    def __init__(self, roles_cfg: dict, bot_name: str = ""):
        self.bot_name = bot_name
        cfg = roles_cfg or {}
        self.default = cfg.get("default", "assistant")
        self.cards: dict = {}
        for key, val in (cfg.get("cards") or {}).items():
            try:
                self.cards[key] = self._load_card(val)
            except Exception as e:
                log.error("加载角色卡 [%s] 失败: %s", key, e)

    @staticmethod
    def _load_card(val) -> dict:
        """支持三种定义：内联 dict / JSON 文件路径字符串 / {'file': 路径}。"""
        if isinstance(val, str) and val.lower().endswith(".json"):
            return RoleCards._load_json(val)
        if isinstance(val, dict) and val.get("file"):
            card = RoleCards._load_json(str(val["file"]))
            # 允许配置内联字段覆盖 JSON 文件内容
            card.update({k: v for k, v in val.items() if k != "file"})
            return card
        return val

    @staticmethod
    def _load_json(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        card = data.get("role_card", data)  # 兼容有无外层 role_card 包裹
        return {
            "name": card.get("name", ""),
            "prompt": card.get("system_prompt") or card.get("prompt", ""),
        }

    def get_card(self, name: str = None) -> dict:
        key = name or self.default
        card = self.cards.get(key)
        if card is None:
            raise KeyError(f"角色卡不存在: {key}，可用: {list(self.cards)}")
        return card

    def system_prompt(self, name: str = None, user: str = None) -> str:
        """渲染角色卡 system prompt。

        - {{user}} / {user} → 实际发送者昵称（未提供则用「对方」）
        - {bot_name} → 机器人昵称
        使用 replace 而非 str.format，避免 JSON 中的花括号内容导致 KeyError。
        """
        card = self.get_card(name)
        text = str(card.get("prompt", ""))
        text = text.replace("{{user}}", user or "对方").replace("{user}", user or "对方")
        text = text.replace("{bot_name}", self.bot_name)
        return text

    def list_cards(self) -> list:
        return list(self.cards.keys())
