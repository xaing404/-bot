"""关键词识别：支持精确匹配（包含关键词）与模糊匹配（滑动窗口相似度）。"""

import re
from difflib import SequenceMatcher


# @提及：位于文本开头或空白字符之后（微信真实 @提及总是独立 token），
# 昵称后可跟微信插入的特殊空格（U+2005/U+2006）或普通空格。
# 正文中间的 @（如邮箱 a@b.com）不属于提及，不受影响。
_MENTION_RE = re.compile(
    r"(^|\s)@[^\s\u2005\u2006@，。！？、；：…—~]+[\u2005\u2006 ]*"
)


def strip_mentions(text: str) -> str:
    """精准清除文本中的 @提及（如 @某人、@机器人自己），保留其余内容。

    微信中含 @昵称 的文本发送后会变成真实 @，为避免机器人乱@人，
    AI 生成的回复统一在此清洗，@提问人 由调用方按需添加。
    """
    if not text:
        return ""
    return _MENTION_RE.sub(r"\1", text).strip()


def contains_keyword(text: str, keyword: str) -> bool:
    """精确匹配：消息中包含关键词（忽略大小写）。"""
    return keyword.lower() in (text or "").lower()


def fuzzy_match(text: str, keyword: str, threshold: float = 0.75) -> bool:
    """模糊匹配：关键词与消息整体或其滑动窗口的相似度达到阈值即命中。"""
    t = (text or "").lower()
    k = keyword.lower()
    if not k:
        return False
    if SequenceMatcher(None, t, k).ratio() >= threshold:
        return True
    n = len(k)
    for size in (n - 1, n, n + 1):
        if size <= 0:
            continue
        for i in range(0, max(1, len(t) - size + 1)):
            if SequenceMatcher(None, t[i:i + size], k).ratio() >= threshold:
                return True
    return False


def match_keywords(text: str, keywords: list, fuzzy: bool = False,
                   threshold: float = 0.75) -> str | None:
    """返回第一个命中的关键词；未命中返回 None。优先精确匹配。"""
    for kw in keywords or []:
        if contains_keyword(text, kw):
            return kw
    if fuzzy:
        for kw in keywords or []:
            if fuzzy_match(text, kw, threshold):
                return kw
    return None


def detect_at(text: str, bot_name: str) -> bool:
    """识别消息是否 @了机器人（兼容 @ 与昵称之间的特殊字符）。"""
    if not text or not bot_name:
        return False
    lowered = text.lower()
    name = bot_name.lower()
    if f"@{name}" in lowered:
        return True
    stripped = lowered.lstrip()
    return stripped.startswith("@") and name in stripped[: len(name) + 4]
