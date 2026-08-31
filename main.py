"""TedaBot 入口：加载配置 → 初始化组件 → 主循环（watchdog 自动重启）。"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

from bot.ai_backend import ContextManager
from bot.handler import MessageHandler
from bot.logger import setup_logging
from bot.wechat_client import WeChatClient

POLL_INTERVAL = 0.5        # 消息轮询间隔（秒）
RESTART_DELAY = 5          # 崩溃后自动重启的等待时间（秒）
MAX_RESTARTS = 10          # 连续崩溃的最大重启次数（防死循环）
MAX_WORKERS = 4            # AI 回复线程池大小


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def run_once(cfg: dict, log):
    """单次运行：初始化组件并进入消息轮询循环。"""
    ai_cfg = cfg.get("ai") or {}
    if "替换" in ai_cfg.get("api_key", ""):
        raise SystemExit("请先在 config.yaml 中填写 ai.api_key")

    from bot.ai_backend import AIBackend
    ai = AIBackend(ai_cfg)
    contexts = ContextManager(ai_cfg.get("max_context_rounds", 8))
    handler = MessageHandler(cfg, ai, contexts)
    client = WeChatClient(cfg)

    from bot.proactive import ProactiveEngine
    engine = ProactiveEngine(cfg, handler, client.bot_name)

    targets = client.group_whitelist + client.private_whitelist
    log.info("机器人启动完成，正在监听: %s（角色: %s，主动互动: %s）",
             targets, handler.roles.default,
             (cfg.get("proactive") or {}).get("mode", "keyword"))

    poll_interval = float((cfg.get("wechat") or {}).get("poll_interval", 1.0))
    last_refresh = 0.0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        while True:
            for msg in client.poll():
                pool.submit(_process, handler, client, engine, msg)
            # 检查各群是否满足主动发言条件
            for chat in client.group_whitelist:
                reason = engine.should_speak(chat)
                if reason:
                    pool.submit(_proactive_speak, engine, client, handler, chat, reason)
            # 每 30 秒检查一次是否有新打开的独立聊天窗口
            now = time.monotonic()
            if now - last_refresh > 30:
                client.refresh_subwindows()
                last_refresh = now
            time.sleep(poll_interval)


def _process(handler: MessageHandler, client: WeChatClient, engine, msg):
    """在线程池中执行：积累上下文 + AI 生成回复并发送（不阻塞消息轮询）。"""
    try:
        engine.observe(msg)
        reply = handler.handle(msg)
        if reply:
            client.send(msg.chat_name, reply)
    except Exception:
        import logging
        logging.getLogger("teda_bot").exception("处理消息时发生未预期异常")


def _proactive_speak(engine, client: WeChatClient, handler, chat: str, reason: str):
    """主动发言任务：生成内容并发送到群里。"""
    try:
        reply = engine.speak(chat, reason)
        if reply:
            client.send(chat, reply)
            handler.record_sent(chat, reply)  # 发送成功后登记，供重复检测使用
    except Exception:
        import logging
        logging.getLogger("teda_bot").exception("主动发言时发生未预期异常")


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)  # 保证相对路径（config/logs）稳定
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    cfg = load_config(config_path)
    log = setup_logging(cfg.get("logging") or {})

    restarts = 0
    while restarts < MAX_RESTARTS:
        try:
            run_once(cfg, log)
        except KeyboardInterrupt:
            log.info("收到退出信号，机器人已停止")
            break
        except SystemExit as e:
            log.error("%s", e)
            break
        except Exception as e:
            restarts += 1
            log.exception("主循环异常（第%d次自动重启）: %s", restarts, e)
            time.sleep(RESTART_DELAY)
    else:
        log.error("连续崩溃 %d 次，停止自动重启，请检查日志", MAX_RESTARTS)


if __name__ == "__main__":
    main()
