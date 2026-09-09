from __future__ import annotations

from importlib import import_module
import re
from typing import Any

from deepagents import create_deep_agent

from app.agent.context import RuntimeContext
from app.interaction.schemas import RuntimeRequestError


_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_DEFAULT_AGENT_ID = "chat"


def _validate_agent_id_format(agent_id: str) -> None:
    """拒绝不能安全映射到 app.agent 子模块的 Agent ID。"""

    if not _AGENT_ID.fullmatch(agent_id):
        raise RuntimeRequestError("invalid_agent_id", "Agent ID 无效。")


def _business_agent_factory(agent_id: str) -> Any:
    """加载由 XCodeAgent Build 生成的受控业务 Agent 工厂。"""

    module_name = f"app.agent.{agent_id}"
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise RuntimeRequestError(
                "agent_not_found",
                "指定业务 Agent 尚未生成。",
            ) from None
        raise
    factory = getattr(module, "create_agent", None)
    if not callable(factory):
        raise RuntimeRequestError(
            "invalid_agent_module",
            "业务 Agent 缺少受支持的创建入口。",
        )
    return factory


def validate_agent_id(agent_id: str) -> None:
    """校验请求是否指向当前模板实际提供的 Agent。"""

    _validate_agent_id_format(agent_id)
    if agent_id != _DEFAULT_AGENT_ID:
        _business_agent_factory(agent_id)


def create_agent(
    *,
    agent_id: str,
    model: Any,
    runtime_context: RuntimeContext,
    checkpointer: Any,
) -> Any:
    """使用 Deep Agents 创建模板内置的最小可运行 Chat Agent。"""

    _validate_agent_id_format(agent_id)
    if agent_id == _DEFAULT_AGENT_ID:
        del runtime_context
        return create_deep_agent(
            model=model,
            tools=[],
            system_prompt="你是一个可靠的 AI 助手，请使用清晰、准确的语言回答用户。",
            checkpointer=checkpointer,
            name=agent_id,
        )
    factory = _business_agent_factory(agent_id)
    return factory(
        model=model,
        runtime_context=runtime_context,
        checkpointer=checkpointer,
    )
