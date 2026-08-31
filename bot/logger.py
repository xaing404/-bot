import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(cfg: dict) -> logging.Logger:
    """初始化日志：控制台 + 滚动文件。"""
    level = getattr(logging, str(cfg.get("level", "INFO")).upper(), logging.INFO)
    log_file = cfg.get("file", "logs/bot.log")
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger("teda_bot")
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:  # 避免重复初始化
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=cfg.get("max_bytes", 5 * 1024 * 1024),
        backupCount=cfg.get("backup_count", 3),
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger
