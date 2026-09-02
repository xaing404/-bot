# -*- coding: utf-8 -*-
"""可复用彩色日志模块。

特性：
- 统一格式：[时间戳][日志级别][模块名称] 消息（级别/模块对齐）
- 差异化颜色：ERROR 红 / WARNING 黄 / INFO 白 / SUCCESS 绿 / TASK 紫(模型生成任务)
- 自定义级别：SUCCESS(25)、TASK(24，模型生成任务专用)
- 长文本多行缩进，提升可读性
- 配置化：级别过滤、控制台开关、颜色开关（Windows 终端自动兼容降级）
- 文件日志恒为纯文本（不写 ANSI 转义码）
"""
import ctypes
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

# ---------- 自定义日志级别 ----------
logging.SUCCESS = 25  # 成功日志（绿色）
logging.addLevelName(logging.SUCCESS, "SUCCESS")
logging.TASK = 24  # 模型生成任务日志（紫色）
logging.addLevelName(logging.TASK, "TASK")

# ---------- ANSI 颜色码 ----------
_C = {
    "reset": "\033[0m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[36m",
    "white": "\033[97m",
    "dim": "\033[90m",
    "magenta": "\033[95m",  # 紫色系：模型生成任务
    "blue": "\033[94m",
}

_LEVEL_COLOR = {
    "ERROR": "red",
    "CRITICAL": "red",
    "WARNING": "yellow",
    "INFO": "white",
    "DEBUG": "dim",
    "SUCCESS": "green",
    "TASK": "magenta",
}

_vt_lock = threading.Lock()
_vt_enabled = False


def _enable_windows_vt() -> bool:
    """Windows 下通过 SetConsoleMode 启用 ANSI 转义序列（Win10+ 有效）。

    兼容旧终端：旧 conhost 不支持 VT 时返回 False，调用方应降级为无色输出。
    """
    global _vt_enabled
    with _vt_lock:
        if _vt_enabled:
            return True
        if os.name != "nt":
            _vt_enabled = True
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE / STD_ERROR_HANDLE
                handle = kernel32.GetStdHandle(handle_id)
                mode = ctypes.c_uint32()
                if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    return False
                # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                if not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                    return False
            _vt_enabled = True
            return True
        except Exception:
            return False


def _detect_color(cfg_value) -> bool:
    """决定是否启用颜色：显式配置优先；auto/None 时探测终端能力。"""
    if cfg_value is not None:
        return bool(cfg_value)
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # 老版 Windows/重定向环境探测失败则降级为无色
        return _enable_windows_vt() or "WT_SESSION" in os.environ
    return True


class ColoredFormatter(logging.Formatter):
    """控制台彩色格式化器：统一前缀 + 级别配色 + 多行缩进。"""

    def __init__(self, use_color: bool = True, indent_long: bool = True):
        super().__init__(
            fmt="[%(asctime)s][%(levelname)s][%(module_tag)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.use_color = use_color
        self.indent_long = indent_long

    @staticmethod
    def _module_tag(record: logging.LogRecord) -> str:
        """模块名短标签：teda_bot.wechat -> wechat，并按宽度对齐。"""
        name = record.name
        for prefix in ("teda_bot.", "httpx2.", "openai."):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        return name[:12].ljust(12)

    def format(self, record: logging.LogRecord) -> str:
        record.module_tag = self._module_tag(record)
        record.levelname = record.levelname.ljust(7)  # 级别列对齐（SUCCESS 最长）
        text = super().format(record)
        if self.use_color:
            color = _LEVEL_COLOR.get(record.levelname.strip(), "white")
            text = f"{_C[color]}{text}{_C['reset']}"
        if self.indent_long and "\n" in record.getMessage():
            # 多行消息：续行统一缩进 21 空格（与正文起始列对齐）
            body_indent = "\n" + " " * 21
            head, _, rest = text.partition("\n")
            text = head + body_indent + rest.replace("\n", body_indent)
        return text


class PlainFormatter(logging.Formatter):
    """文件用纯文本格式化器（无 ANSI 码），格式与控制台一致。"""

    def __init__(self):
        super().__init__(
            fmt="[%(asctime)s][%(levelname)s][%(module_tag)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        record.module_tag = ColoredFormatter._module_tag(record)
        record.levelname = record.levelname.ljust(7)
        return super().format(record)


def log_success(log: logging.Logger, msg: str, *args, **kwargs):
    """可复用工具函数：以 SUCCESS（绿色）级别记录成功日志。"""
    log.log(logging.SUCCESS, msg, *args, **kwargs)


def log_task(log: logging.Logger, task_id: str, msg: str, *args, **kwargs):
    """可复用工具函数：记录模型生成任务日志（紫色，含任务ID）。

    msg 可含自己的 %s/%d 占位符，由 args 填充；
    先预格式化 msg，再整体拼入 [任务%s] 前缀，避免占位符冲突。
    """
    if args:
        msg = msg % args
    log.log(logging.TASK, "[任务%s] %s", task_id, msg, **kwargs)


def setup_logging(cfg: dict) -> logging.Logger:
    """初始化日志：控制台（彩色，可开关）+ 滚动文件（纯文本）。

    cfg 支持：
      level: 显示级别（DEBUG/INFO/WARNING/ERROR），低于该级的不显示
      file / max_bytes / backup_count: 文件日志
      console.enabled: 控制台输出总开关
      console.colors: 颜色开关（None=自动探测）
      console.indent_long: 长文本多行缩进
    """
    console_cfg = cfg.get("console") or {}
    level = getattr(logging, str(cfg.get("level", "INFO")).upper(), logging.INFO)
    use_color = _detect_color(console_cfg.get("colors"))
    indent_long = bool(console_cfg.get("indent_long", True))
    console_enabled = console_cfg.get("enabled", True)

    log_file = cfg.get("file", "logs/bot.log")
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("teda_bot")
    logger.setLevel(logging.DEBUG)  # 根级放开，由各 handler 的 level 控制显示
    logger.propagate = False
    if logger.handlers:  # 避免重复初始化
        return logger

    if console_enabled and sys.stdout:
        console = logging.StreamHandler(stream=sys.stdout)
        console.setLevel(level)  # 控制台分级显示由配置控制
        console.setFormatter(ColoredFormatter(use_color, indent_long))
        logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=cfg.get("max_bytes", 5 * 1024 * 1024),
        backupCount=cfg.get("backup_count", 3),
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(PlainFormatter())
    logger.addHandler(file_handler)
    return logger
