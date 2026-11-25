import asyncio
import selectors
import uvicorn
from config import config_manager  # 配置管理器
from common import *
from web import app  # FastAPI 应用实例

# ========================= 异步主入口 =========================
async def main():
    """异步主入口函数，用于启动 Web 服务"""
    await start_web()

# ========================= 启动 Web 服务 =========================
async def start_web():
    """启动 FastAPI Web 服务"""
    # 获取配置中的 host 和 port
    host = config_manager.get(SERVER_HOST)
    port = int(config_manager.get(SERVER_PORT))

    # 创建 uvicorn 配置
    config = uvicorn.Config(
        app,            # FastAPI 应用
        host=host,      # 绑定主机
        port=port,      # 绑定端口
        log_level="info",  # 日志等级
        access_log=False,  # 关闭访问日志
    )

    # 创建 uvicorn 异步服务器实例
    server = uvicorn.Server(config)

    # 异步启动服务器
    await server.serve()

# ========================= 项目入口 =========================
if __name__ == "__main__":
    # ⚠️ Windows 上需要使用 SelectorEventLoop 才能兼容 psycopg 异步
    try:
        # Runner 在退出时清理剩余任务、异步生成器和线程池，并关闭事件循环
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(
                selector=selectors.SelectSelector()
            )
        ) as runner:
            runner.run(main())
    except KeyboardInterrupt:
        # Uvicorn 完成优雅关闭后会重新触发 SIGINT，按正常停止处理
        pass
