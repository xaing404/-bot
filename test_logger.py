# -*- coding: utf-8 -*-
"""日志模块完整测试用例：python test_logger.py

覆盖：各级别颜色、格式统一、模块对齐、多行缩进、级别过滤、
控制台开关、无色降级、文件纯文本、中文显示、工具函数复用。
"""
import io
import logging
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

from bot.logger import (
    ColoredFormatter, PlainFormatter, log_success, log_task, setup_logging,
)

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def make_record(level: int, msg: str, name: str = "teda_bot.wechat") -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1,
        args=None, msg=msg, exc_info=None,
    )


print("== 1. 格式与颜色单元测试 ==")
cf = ColoredFormatter(use_color=True, indent_long=True)
r = make_record(logging.ERROR, "接口超时")
out = cf.format(r)
check("统一前缀 [时间戳][级别][模块]", out.count("[") >= 3 and "ERROR" in out and "wechat" in out)
check("ERROR 红色", "\033[31m" in out and "\033[0m" in out)
check("级别列对齐(补位至7字符)", "[ERROR  ]" in out.replace("\033[31m", "").replace("\033[0m", ""))

r = make_record(logging.WARNING, "重试退避")
check("WARNING 黄色", "\033[33m" in cf.format(r))
r = make_record(logging.INFO, "捕获消息")
check("INFO 白色", "\033[97m" in cf.format(r))
r = make_record(logging.SUCCESS, "发送完成")
check("SUCCESS 绿色", "\033[32m" in cf.format(r))
check("SUCCESS 级别名注册", logging.getLevelName(25) == "SUCCESS")
r = make_record(logging.TASK, "模型生成任务开始")
check("TASK 紫色系", "\033[95m" in cf.format(r))
check("TASK 级别名注册", logging.getLevelName(24) == "TASK")

r = make_record(logging.INFO, "第一行\n第二行\n第三行")
out = cf.format(r)
lines = [ln for ln in out.split("\n")]
check("多行自动缩进对齐(21空格)", len(lines) == 3 and lines[1].startswith(" " * 21) and lines[2].startswith(" " * 21))

r = make_record(logging.INFO, "中文消息无乱码✅", name="teda_bot.send_queue")
check("模块名短标签", "send_queue" in cf.format(r))
r = make_record(logging.INFO, "x", name="teda_bot.ai")
check("超长模块名截断对齐", cf.format(r).count("ai".ljust(12) + "]") == 1 or "ai          ]" in cf.format(r))

print("== 2. 文件纯文本（无 ANSI 码） ==")
pf = PlainFormatter()
for lv in (logging.ERROR, logging.WARNING, logging.INFO, logging.SUCCESS, logging.TASK):
    out = pf.format(make_record(lv, "msg"))
    check(f"级别{lv} 无转义码", "\033[" not in out)

print("== 3. 级别过滤 / 控制台开关 ==")
from logging.handlers import RotatingFileHandler as _RFH


def _reset_root():
    root = logging.getLogger("teda_bot")
    for h in list(root.handlers):  # 用副本迭代，避免边遍历边删导致跳过
        root.removeHandler(h)
        if isinstance(h, _RFH):
            h.close()  # Windows 下必须关闭句柄才能清理临时目录
    return root


with tempfile.TemporaryDirectory() as td:
    root = logging.getLogger("teda_bot")
    saved_handlers, saved_level = root.handlers[:], root.level
    for h in saved_handlers:
        root.removeHandler(h)
    try:
        cfg = {
            "level": "WARNING", "file": os.path.join(td, "t1.log"),
            "console": {"enabled": True, "colors": True, "indent_long": True},
        }
        setup_logging(cfg)
        console = [h for h in root.handlers
                   if isinstance(h, logging.StreamHandler) and not isinstance(h, _RFH)]
        fh = [h for h in root.handlers if isinstance(h, _RFH)]
        check("WARNING 级别下 INFO 被过滤", bool(console) and console[0].level == logging.WARNING)
        check("文件 handler 仍生效", bool(fh))

        _reset_root()
        cfg2 = {
            "level": "INFO", "file": os.path.join(td, "t2.log"),
            "console": {"enabled": False, "colors": False, "indent_long": False},
        }
        setup_logging(cfg2)
        check("控制台开关关闭后无 StreamHandler",
              not [h for h in root.handlers
                   if isinstance(h, logging.StreamHandler) and not isinstance(h, _RFH)])
        # 文件内容与编码验证
        log_task(root, "abcd1234", "模型生成任务开始 | 测试中文✅")
        for h in root.handlers:
            h.flush()
        content = open(os.path.join(td, "t2.log"), encoding="utf-8").read()
        check("文件含任务ID与中文", "abcd1234" in content and "模型生成任务开始" in content,
              f"实际内容: {content!r}")
        check("文件格式含标准前缀", "[TASK   ]" in content and "teda_bot" in content,
              f"实际内容: {content!r}")
    finally:
        _reset_root()
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)

print("== 4. 可视化效果演示（控制台直接观察颜色） ==")
log = setup_logging({
    "level": "DEBUG", "file": os.path.join(tempfile.gettempdir(), "teda_logger_demo.log"),
    "console": {"enabled": True, "colors": True, "indent_long": True},
})
log.debug("这是 DEBUG 级别（暗灰）")
log.info("这是 INFO 级别（白色/默认）——中文与 emoji 🐘✨ 显示正常")
log.warning("这是 WARNING 级别（黄色）：窗口切换校验失败")
log.error("这是 ERROR 级别（红色）：AI 调用失败")
log_success(log, "这是 SUCCESS 级别（绿色）：发送队列冲刷完成")
log_task(log, "a1b2c3d4", "模型生成任务开始 | 模型=auto | 输入5条历史消息")
log.info("这是含换行的长文本日志（自动缩进对齐）：\n好的，现在需要处理用户提供的群聊场景，\n并生成符合林小满角色设定的回复。\n首先，分析当前对话内容。")

print(f"\n结果: {'全部通过' if FAIL == 0 else f'{FAIL} 项失败'}（PASS {PASS}）")
sys.exit(1 if FAIL else 0)
