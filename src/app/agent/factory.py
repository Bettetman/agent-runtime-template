from __future__ import annotations

from importlib import import_module
import re
from typing import Any

from deepagents import create_deep_agent

from app.agent.context import RuntimeContext
from app.agent.definition import AgentDefinition, load_agent_definition
from app.agent.tools import build_tools
from app.interaction.schemas import RuntimeRequestError


_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_DEFAULT_AGENT_ID = "chat"


def _validate_agent_id_format(agent_id: str) -> None:
    """拒绝不能安全映射到 app.agent 子模块的 Agent ID。"""

    if not _AGENT_ID.fullmatch(agent_id):
        raise RuntimeRequestError("invalid_agent_id", "Agent ID 无效。")


def _business_agent_factory(definition: AgentDefinition) -> Any:
    """仅为 Definition 明确启用的 Python Extension 加载受控工厂。"""

    module_name = str(definition.extension.module or "")
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise RuntimeRequestError(
                "invalid_agent_extension",
                "业务 Agent Extension 尚未生成。",
            ) from None
        raise
    factory = getattr(module, "create_agent", None)
    if not callable(factory):
        raise RuntimeRequestError(
            "invalid_agent_extension",
            "业务 Agent Extension 缺少受支持的创建入口。",
        )
    return factory


def validate_agent_id(agent_id: str) -> None:
    """校验请求是否指向当前模板实际提供的 Agent。"""

    _validate_agent_id_format(agent_id)
    if agent_id != _DEFAULT_AGENT_ID:
        definition = load_agent_definition(agent_id)
        if definition.extension.enabled:
            _business_agent_factory(definition)


def _configured_model(model: Any, definition: AgentDefinition) -> Any:
    """在项目默认模型上绑定 Definition 声明的生成参数。"""

    model_settings = definition.agent_settings.get("model")
    generation = model_settings.get("generation") if isinstance(model_settings, dict) else {}
    temperature = generation.get("temperature") if isinstance(generation, dict) else None
    return model.bind(temperature=temperature) if temperature is not None and hasattr(model, "bind") else model


def _configured_checkpointer(checkpointer: Any, definition: AgentDefinition) -> Any:
    """只在正式 Definition 启用短期记忆时注入模板 Checkpointer。"""

    memory = definition.agent_settings.get("memory")
    short_term = memory.get("shortTerm") if isinstance(memory, dict) else {}
    return checkpointer if isinstance(short_term, dict) and short_term.get("enabled") is True else None


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
    definition = load_agent_definition(agent_id)
    settings = definition.agent_settings
    for name in ("skills", "knowledge"):
        value = settings.get(name)
        if isinstance(value, dict) and value.get("enabled") is True:
            raise RuntimeRequestError("unsupported_agent_capability", f"当前 Runtime 尚未启用 {name} Loader。")
    configured_model = _configured_model(model, definition)
    configured_checkpointer = _configured_checkpointer(checkpointer, definition)
    tools = build_tools(definition, runtime_context)
    if definition.extension.enabled:
        factory = _business_agent_factory(definition)
        return factory(
            definition=definition,
            model=configured_model,
            tools=tools,
            runtime_context=runtime_context,
            checkpointer=configured_checkpointer,
        )
    return create_deep_agent(
        model=configured_model,
        tools=tools,
        system_prompt=definition.compiled_prompt,
        checkpointer=configured_checkpointer,
        name=agent_id,
    )
