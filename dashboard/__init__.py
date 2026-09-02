"""TedaBot Dashboard — Web 管理后台。

在机器人主进程内以守护线程运行 Flask，提供页面托管与 REST API，
将后端运行时状态（队列指标、场景记忆、角色卡、日志）暴露给前端页面。
"""

from .server import DashboardServer

__all__ = ["DashboardServer"]
