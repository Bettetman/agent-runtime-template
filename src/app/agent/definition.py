from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.interaction.schemas import RuntimeRequestError
from app.settings import load_settings


_AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_MODULE_NAMES = {"prompt", "model", "memory", "tools", "skills", "knowledge", "context"}


class ModuleState(BaseModel):
    """描述一个 Agent 配置模块的确定性编译终态。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: Literal["completed", "skipped"]
    config_sha256: str = Field(alias="configSha256", pattern=r"^sha256:[0-9a-f]{64}$")
    reason: str | None = None


class ExtensionDefinition(BaseModel):
    """声明只有标准 Definition 无法表达时才加载的 Python Extension。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    module: str | None
    path: str | None
    testPath: str | None

    @model_validator(mode="after")
    def validate_enabled_fields(self) -> "ExtensionDefinition":
        """保证启用状态与 Extension 模块和路径保持一致。"""

        if self.enabled and (not self.module or not self.path):
            raise ValueError("启用 Python Extension 时必须声明 module 和 path。")
        if not self.enabled and any((self.module, self.path, self.testPath)):
            raise ValueError("未启用 Python Extension 时不得声明扩展路径。")
        return self


class AgentDefinition(BaseModel):
    """定义 Runtime Generic Factory 可加载的当前业务 Agent 配置。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["agent-runtime.definition.v1"] = Field(alias="schemaVersion")
    agent_id: str = Field(alias="agentId")
    source: dict[str, Any]
    identity: dict[str, Any]
    capabilities: list[dict[str, Any]]
    interaction: dict[str, Any]
    agent_settings: dict[str, Any] = Field(alias="agentSettings")
    invocation: dict[str, Any]
    runtime: dict[str, Any]
    security: dict[str, Any]
    evaluation: dict[str, Any]
    compiled_prompt: str = Field(alias="compiledPrompt", min_length=1)
    extension: ExtensionDefinition
    modules: dict[str, ModuleState]

    @model_validator(mode="after")
    def validate_complete_contract(self) -> "AgentDefinition":
        """拒绝缺失七模块或非法 Agent ID 的不完整 Definition。"""

        if not _AGENT_ID.fullmatch(self.agent_id):
            raise ValueError("Agent ID 无效。")
        if set(self.agent_settings) != _MODULE_NAMES:
            raise ValueError("agentSettings 必须完整包含七个配置模块。")
        if set(self.modules) != _MODULE_NAMES:
            raise ValueError("modules 必须完整包含七个编译状态。")
        return self


def _definition_path(agent_id: str) -> Path:
    """把合法 Agent ID 映射到 Runtime 内固定 Definition 目录。"""

    if not _AGENT_ID.fullmatch(agent_id):
        raise RuntimeRequestError("invalid_agent_id", "Agent ID 无效。")
    return load_settings().runtime_root / "config" / "agents" / f"{agent_id}.json"


def load_agent_definition(agent_id: str) -> AgentDefinition:
    """读取并严格校验一个业务 Agent Definition。"""

    path = _definition_path(agent_id)
    if not path.is_file():
        raise RuntimeRequestError("agent_not_found", "指定业务 Agent 尚未生成。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        definition = AgentDefinition.model_validate(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeRequestError("invalid_agent_definition", "业务 Agent Definition 无效。") from exc
    if definition.agent_id != agent_id:
        raise RuntimeRequestError("invalid_agent_definition", "业务 Agent Definition 身份不匹配。")
    return definition


def main() -> None:
    """提供 Build Required Check 使用的单文件 Definition 校验入口。"""

    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.agent.definition config/agents/<agent_id>.json")
    path = Path(sys.argv[1]).resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    AgentDefinition.model_validate(payload)


if __name__ == "__main__":
    main()
