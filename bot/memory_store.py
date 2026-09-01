"""场景记忆持久化：每个场景（群聊/私聊）独立 JSON 文件存储对话记忆。

场景键格式：group:<群名> / private:<好友昵称>，天然隔离不同场景。
文件写入采用"临时文件 + 原子替换"，避免写入中断导致记忆损坏。
"""

import json
import logging
import os
import re
import threading

log = logging.getLogger("teda_bot.memory")


class MemoryStore:
    def __init__(self, dir_path: str = "memory", enabled: bool = True):
        self.enabled = bool(enabled)
        self.dir = dir_path
        if self.enabled:
            os.makedirs(dir_path, exist_ok=True)
        self._lock = threading.Lock()

    def load(self, key: str) -> list:
        """加载场景记忆，返回 [{role, content, ts}, ...]；无记录返回空列表。"""
        if not self.enabled:
            return []
        path = self._path(key)
        if not os.path.exists(path):
            return []
        try:
            with self._lock:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            log.warning("加载场景记忆失败 [%s]: %s", key, e)
            return []

    def save(self, key: str, messages: list):
        """原子写入场景记忆。messages 为 [{role, content, ts}, ...]。"""
        if not self.enabled:
            return
        path = self._path(key)
        tmp = path + ".tmp"
        try:
            with self._lock:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(messages, f, ensure_ascii=False)
                os.replace(tmp, path)  # 原子替换，防写入中断损坏
        except Exception as e:
            log.warning("保存场景记忆失败 [%s]: %s", key, e)

    def scenarios(self) -> list:
        """列出已持久化的所有场景键。"""
        if not self.enabled or not os.path.isdir(self.dir):
            return []
        names = []
        for fn in os.listdir(self.dir):
            if fn.endswith(".json"):
                names.append(fn[:-5])
        return sorted(names)

    def _path(self, key: str) -> str:
        # 场景键 → 安全文件名（保留中文，替换文件系统非法字符）
        safe = re.sub(r'[\\/:*?"<>|]', "_", key)
        return os.path.join(self.dir, f"{safe}.json")
