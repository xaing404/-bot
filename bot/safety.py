"""敏感内容过滤：命中词表的消息直接忽略，不做回复。"""


class SafetyFilter:
    def __init__(self, block_words: list, enabled: bool = True):
        self.enabled = enabled
        self.block_words = [w.lower() for w in (block_words or []) if w]

    def is_blocked(self, text: str) -> bool:
        if not self.enabled or not self.block_words:
            return False
        lowered = (text or "").lower()
        return any(w in lowered for w in self.block_words)
