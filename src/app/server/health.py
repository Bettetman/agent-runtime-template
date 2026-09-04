from __future__ import annotations

from fastapi import APIRouter


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, object]:
    """返回无敏感信息的 Agent Runtime 存活状态。"""

    return {
        "service": "agent-runtime",
        "status": "ok",
        "protocol": "agent-runtime.v1",
        "transport": "ag-ui-sse",
    }

