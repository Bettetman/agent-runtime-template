from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from app.agent.context import RuntimeContextError, create_runtime_context
from app.interaction.schemas import RuntimeRequestError, parse_run_request
from app.models.factory import close_chat_models
from app.persistence.checkpointer import close_checkpointers
from app.server.agui import build_agent_runtime_stream
from app.server.health import router as health_router
from app.settings import load_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """在服务退出时关闭 Runtime 持有的 checkpoint 连接。"""

    yield
    await close_checkpointers()
    await close_chat_models()


def create_app() -> FastAPI:
    """创建只暴露健康检查和内部 AG-UI Chat 的 FastAPI 应用。"""

    application = FastAPI(title="XCodeAgent Agent Runtime", lifespan=lifespan)
    application.include_router(health_router)

    @application.post("/internal/agents/{agent_id}/run")
    async def run_agent(
        agent_id: str,
        payload: dict[str, Any] = Body(...),
        accept: str = Header(default="text/event-stream"),
        authorization: str = Header(default=""),
        user_id: str = Header(default="", alias="X-Agent-User-Id"),
        tenant_id: str = Header(default="", alias="X-Agent-Tenant-Id"),
        scopes: str = Header(default="", alias="X-Agent-Scopes"),
        traceparent: str = Header(default=""),
    ) -> StreamingResponse:
        """验证 Java 网关上下文并返回业务 Agent AG-UI SSE。"""

        settings = load_settings()
        try:
            runtime_context = create_runtime_context(
                settings=settings,
                authorization=authorization,
                user_id=user_id,
                tenant_id=tenant_id,
                scopes=scopes,
                traceparent=traceparent,
            )
            request = parse_run_request(payload)
        except RuntimeContextError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except RuntimeRequestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return StreamingResponse(
            build_agent_runtime_stream(
                agent_id=agent_id,
                request=request,
                runtime_context=runtime_context,
                settings=settings,
                accept=accept,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return application


app = create_app()
