from __future__ import annotations

import re
from typing import Any

from deepagents import create_deep_agent

from app.agent.context import RuntimeContext
from app.interaction.schemas import RuntimeRequestError


_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_DEFAULT_AGENT_ID = "chat"


def validate_agent_id(agent_id: str) -> None:
    """校验请求是否指向当前模板实际提供的 Agent。"""

    if not _AGENT_ID.fullmatch(agent_id):
        raise RuntimeRequestError("invalid_agent_id", "Agent ID 无效。")
    if agent_id != _DEFAULT_AGENT_ID:
        raise RuntimeRequestError("agent_not_found", "指定业务 Agent 尚未生成。") from None


def create_agent(
    *,
    agent_id: str,
    model: Any,
    runtime_context: RuntimeContext,
    checkpointer: Any,
) -> Any:
    """使用 Deep Agents 创建模板内置的最小可运行 Chat Agent。"""

    validate_agent_id(agent_id)
    del runtime_context
    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt="你是一个可靠的 AI 助手，请使用清晰、准确的语言回答用户。",
        checkpointer=checkpointer,
        name=agent_id,
    )
