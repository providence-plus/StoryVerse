"""StoryVerse 后端入口。

职责边界：本文件只负责创建 FastAPI 应用与挂载路由。
业务逻辑放在 backend/core/，接口定义放在 backend/api/，数据模型放在 backend/models/。
从项目根目录启动：python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# 所有路径都相对项目根目录解析，避免依赖"当前工作目录"这种隐形前提。
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
WORLDS_DIR = PROJECT_ROOT / "worlds"
SETTINGS_PATH = CONFIG_DIR / "settings.yaml"


def create_app() -> FastAPI:
    """应用工厂：测试和 uvicorn 都用同一个入口，避免两套配置漂移。"""
    app = FastAPI(
        title="StoryVerse",
        version="0.1.0",
        description="本地运行的多世界 AI 互动叙事引擎",
        docs_url="/api/docs",
    )

    # 浏览器只访问前端来源，/api 由 Vite 代理到本机后端。
    # 因此只放开两个本机开发端口，不使用通配来源。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 统一异常映射：让前端拿到可读的中文错误，而不是 500 堆栈。
    @app.exception_handler(ValueError)
    async def _bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def _not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(FileExistsError)
    async def _conflict(_: Request, exc: FileExistsError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RuntimeError)
    async def _upstream_error(_: Request, exc: RuntimeError) -> JSONResponse:
        # 模型未连接、生成失败都以 502 明确告诉前端，绝不伪装成成功。
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, str]:
        """健康检查：前端和启动脚本用它判断后端是否已就绪。"""
        return {"status": "ok", "service": "storyverse", "version": "0.1.0"}

    from backend.api import characters, panels, prompts, saves, settings, story, worlds

    for module in (worlds, characters, story, saves, prompts, settings, panels):
        app.include_router(module.router)

    return app


app = create_app()
