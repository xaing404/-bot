"""AI 聊天模块独立运行入口（无需启动 main.py / 微信客户端）。

用法：
    python chat_server.py            # 使用 config.yaml
    python chat_server.py my.yaml    # 指定配置文件

启动后访问 http://127.0.0.1:8051/chat.html 即可开始聊天。
端口可在 config.yaml 的 dashboard.chat_port 中配置（默认 8051）。
"""

import os
import sys

import yaml


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)  # 保证角色卡 JSON 等相对路径稳定

    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    from dashboard.chat_api import create_chat_app
    app = create_chat_app(cfg)

    dash_cfg = cfg.get("dashboard") or {}
    host = dash_cfg.get("host", "127.0.0.1")
    port = int(dash_cfg.get("chat_port", 8051))

    print(f"AI 聊天模块已启动: http://{host}:{port}/chat.html")
    # 独立进程直接阻塞运行；threaded=True 支持多浏览器标签同时聊天
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
