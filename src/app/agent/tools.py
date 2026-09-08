from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.context import RuntimeContext
from app.agent.definition import AgentDefinition


class GatewayToolInput(BaseModel):
    """在 Java Tool Gateway 协议完成前保存声明式业务入参。"""

    payload: dict[str, Any] = Field(default_factory=dict)


def _deferred_tool(binding: dict[str, Any]) -> StructuredTool:
    """为已声明但尚无网关协议的 Tool 创建明确失败的安全边界。"""

    tool_id = str(binding.get("toolId") or "").strip()
    description = str(binding.get("description") or tool_id).strip()
    endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
    endpoint_id = str(endpoint.get("endpointId") or "unconfigured")

    def invoke(payload: dict[str, Any] | None = None) -> str:
        """拒绝伪造尚未具备正式 Java Gateway 传输协议的工具调用。"""

        del payload
        raise RuntimeError(f"gateway_not_configured: Tool {tool_id} 尚未接入 Endpoint {endpoint_id}。")

    return StructuredTool.from_function(
        func=invoke,
        name=tool_id,
        description=description,
        args_schema=GatewayToolInput,
    )


def build_tools(definition: AgentDefinition, runtime_context: RuntimeContext) -> list[Any]:
    """从 Definition 构造标准 Tool；网关未完成时保持 fail-closed。"""

    del runtime_context
    settings = definition.agent_settings
    tools = settings.get("tools") if isinstance(settings.get("tools"), dict) else {}
    if tools.get("enabled") is not True:
        return []
    bindings = tools.get("bindings") if isinstance(tools.get("bindings"), list) else []
    return [_deferred_tool(binding) for binding in bindings if isinstance(binding, dict)]
