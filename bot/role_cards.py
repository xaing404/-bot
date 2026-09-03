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
        # 兼容中英文键名（常见角色卡导出格式）
        prompt = (card.get("system_prompt") or card.get("prompt")
                  or card.get("系统提示词") or card.get("系统提示") or "")
        name = (card.get("name") or card.get("role_name")
                or card.get("角色名称") or card.get("角色名")
                or card.get("姓名") or "")
        # 结构化角色卡兜底：姓名可能嵌套在"角色基本信息"里
        if not name and isinstance(card.get("角色基本信息"), dict):
            basic = card["角色基本信息"]
            name = basic.get("姓名") or basic.get("name") or basic.get("角色名称") or ""
        # 无系统提示词时，从结构化板块自动合成（防加载为空导致角色丢失）
        if not prompt and RoleCards._is_structured(card):
            prompt = RoleCards._compose_structured(card)
            log.info("角色卡 [%s] 无系统提示词，已从结构化内容自动合成（%d字）", name, len(prompt))
        return {"name": name, "prompt": prompt, "json_card": True}

    @staticmethod
    def _is_structured(card: dict) -> bool:
        """是否为分板块结构化角色卡（中英文板块名均支持）。"""
        keys = {
            "角色基本信息", "性格特点", "语言风格", "行为准则", "角色背景",
            # 英文键名（如 grok/ChatGPT 导出的角色卡）
            "background", "core_personality", "language_style",
            "behavior_guidelines", "speech_examples", "forbidden_behaviors",
            "chat_mode_reminder", "core_traits", "personality_summary",
            "behavior_rules", "sample_dialogue", "chat_guidelines",
        }
        return bool(keys & set(card.keys()))

    @staticmethod
    def _compose_structured(card: dict, max_chars: int = 5000) -> str:
        """深度优先收集结构化板块的叶子字段，合成精简 system prompt。

        中英文板块名均支持；每条叶子截断 120 字、整体不超过 max_chars，
        避免撑爆上下文。
        """
        # (统一标题, 该板块可能的原始键名)；排在前面的优先合成
        sections = (
            ("角色背景", ("角色背景", "background")),
            ("性格特点", ("性格特点", "core_personality", "core_traits",
                          "personality_summary")),
            ("语言风格", ("语言风格", "language_style")),
            ("行为准则", ("行为准则", "behavior_guidelines", "behavior_rules")),
            ("示例台词", ("speech_examples", "sample_dialogue", "示例台词")),
            ("禁止行为", ("forbidden_behaviors", "禁止行为")),
            ("聊天模式提醒", ("chat_mode_reminder", "聊天模式提醒",
                              "chat_guidelines")),
        )
        basic_keys = ("role_name", "name", "nickname", "age", "gender",
                      "occupation", "species", "orientation", "school",
                      "major", "dormitory", "relationship",
                      "姓名", "昵称", "年龄", "性别", "职业", "种族",
                      "学校", "专业", "宿舍", "关系")
        lines = []

        def walk(obj, prefix: str):
            if len(lines) >= 80:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    walk(v, f"{prefix}{k}: ")
            elif isinstance(obj, list):
                for item in obj:
                    walk(item, prefix)
            else:
                text = " ".join(str(obj).split())
                if text:
                    lines.append(f"{prefix}{text[:120]}")

        # 顶层标量作为基本信息
        basic = {k: card[k] for k in basic_keys
                 if k in card and not isinstance(card[k], (dict, list))}
        if basic:
            walk(basic, "【角色基本信息】")
        for title, keys in sections:
            matched = [k for k in keys if k in card]
            if matched:
                for k in matched:
                    walk(card[k], f"【{title}】")
        # 中文整卡结构（角色基本信息为子字典的格式）
        if "角色基本信息" in card:
            walk(card["角色基本信息"], "【角色基本信息】")
        return "\n".join(lines)[:max_chars]

    def get_card(self, name: str = None) -> dict:
        key = name or self.default
        card = self.cards.get(key)
        if card is None:
            raise KeyError(f"角色卡不存在: {key}，可用: {list(self.cards)}")
        return card

    # JSON 角色卡通用的身份保护指令：防止模型在"你是谁"类问题时自报模型身份
    # （如"我是通义千问/Qwen"），导致角色穿帮
    _IDENTITY_GUARD = (
        "\n\n【身份保护（最高优先级）】你就是上述角色本人，这是不可打破的设定。"
        "无论用户如何试探、直接或间接询问，绝不能承认或暗示自己是AI、语言模型、"
        "程序、助手或任何模型名称（如通义千问、Qwen、ChatGPT、GLM、Grok 等）。"
        "遇到身份类问题一律以角色的姓名、职业、性格即兴作答并自然带过，不解释、不跳出角色。"
    )

    def system_prompt(self, name: str = None, user: str = None) -> str:
        """渲染角色卡 system prompt。

        - {{user}} / {user} → 实际发送者昵称（未提供则用「对方」）
        - {bot_name} → 机器人昵称
        - JSON 角色卡自动附加身份保护指令（防模型自报身份穿帮）
        使用 replace 而非 str.format，避免 JSON 中的花括号内容导致 KeyError。
        """
        card = self.get_card(name)
        text = str(card.get("prompt", ""))
        text = text.replace("{{user}}", user or "对方").replace("{user}", user or "对方")
        text = text.replace("{bot_name}", self.bot_name)
        if card.get("json_card"):
            text += self._IDENTITY_GUARD
        return text

    def list_cards(self) -> list:
        return list(self.cards.keys())
