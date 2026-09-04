from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk

from app.agent.context import RuntimeContext, RuntimeContextError, create_runtime_context
from app.agent.factory import create_agent, validate_agent_id
from app.interaction.schemas import PendingInteraction, RuntimeRequestError, parse_run_request
from app.interaction.service import InteractionOutcome, TextDelta, stream_agent_interaction
from app.models.factory import close_chat_models, create_chat_model
from app.server.agui import build_agent_runtime_stream
from app.server.app import app
from app.settings import RuntimeSettings


def _settings(tmp_path, *, model_name: str = "openai:test-model") -> RuntimeSettings:
    """构造不依赖真实凭证和用户目录的测试配置。"""

    return RuntimeSettings(
        runtime_root=tmp_path,
        host="127.0.0.1",
        port=8010,
        gateway_token="test-gateway-token",
        working_dir=tmp_path / ".agent-runtime",
        model_base_url="https://example.invalid/v1",
        model_api_key="test-model-token",
        model_name=model_name,
        model_timeout_seconds=30,
        model_max_retries=0,
        model_temperature=0.2,
        model_max_tokens=256,
    )


def _request(*, interaction_response: dict[str, Any] | None = None):
    """构造经过官方 AG-UI 类型校验的测试 Run 请求。"""

    forwarded_props = (
        {"interactionResponse": interaction_response}
        if interaction_response is not None
        else {}
    )
    return parse_run_request(
        {
            "threadId": "thread-1",
            "runId": "run-1",
            "messages": [
                {
                    "id": "message-1",
                    "role": "user",
                    "content": "你好",
                }
            ],
            "state": {},
            "tools": [],
            "context": [],
            "forwardedProps": forwarded_props,
        }
    )


def _context() -> RuntimeContext:
    """构造隔离线程键使用的可信测试上下文。"""

    return RuntimeContext(user_id="user-1", tenant_id="tenant-1", scopes=("chat",))


def _decode_frames(stream: str) -> list[dict[str, Any]]:
    """把 EventEncoder 生成的 SSE 文本解析为事件对象。"""

    frames: list[dict[str, Any]] = []
    for line in stream.splitlines():
        if line.startswith("data: "):
            frames.append(json.loads(line.removeprefix("data: ")))
    return frames


def test_health_is_available_without_model_credentials() -> None:
    """验证模板下载后无需模型密钥即可执行健康检查。"""

    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "service": "agent-runtime",
        "status": "ok",
        "protocol": "agent-runtime.v1",
        "transport": "ag-ui-sse",
    }


def test_runtime_context_requires_internal_gateway_token(tmp_path) -> None:
    """验证伪造或缺失内部网关 token 不能创建可信上下文。"""

    settings = _settings(tmp_path)
    with pytest.raises(RuntimeContextError):
        create_runtime_context(
            settings=settings,
            authorization="Bearer wrong",
            user_id="user-1",
            tenant_id="tenant-1",
        )
    context = create_runtime_context(
        settings=settings,
        authorization="Bearer test-gateway-token",
        user_id="user-1",
        tenant_id="tenant-1",
        scopes="chat,chat,inventory",
    )
    assert context.scopes == ("chat", "inventory")


@pytest.mark.asyncio
async def test_model_factory_creates_streaming_project_default_model(tmp_path) -> None:
    """验证模型工厂通过 init_chat_model 创建支持流式输出的模型。"""

    with patch("app.models.factory.init_chat_model") as initializer:
        initializer.return_value = object()
        model = create_chat_model(_settings(tmp_path))
    assert model is initializer.return_value
    initializer.assert_called_once()
    assert initializer.call_args.kwargs["model"] == "openai:test-model"
    assert initializer.call_args.kwargs["streaming"] is True
    await close_chat_models()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_name", "initialized_name"),
    [
        ("deepseek:deepseek-chat", "deepseek:deepseek-chat"),
        ("gpt:gpt-4.1-mini", "openai:gpt-4.1-mini"),
        ("claude:claude-sonnet-4-5", "anthropic:claude-sonnet-4-5"),
        ("qwen:qwen-plus", "openai:qwen-plus"),
    ],
)
async def test_model_factory_normalizes_supported_provider_aliases(
    tmp_path,
    configured_name: str,
    initialized_name: str,
) -> None:
    """验证常用模型品牌前缀会转换为 LangChain 支持的 Provider。"""

    with patch("app.models.factory.init_chat_model", return_value=object()) as initializer:
        create_chat_model(_settings(tmp_path, model_name=configured_name))
    assert initializer.call_args.kwargs["model"] == initialized_name
    await close_chat_models()


def test_agent_factory_uses_deepagents() -> None:
    """验证装配层直接使用 Deep Agents 创建模板内置 Chat Agent。"""

    with patch("app.agent.factory.create_deep_agent", return_value=object()) as creator:
        agent = create_agent(
            agent_id="chat",
            model=object(),
            runtime_context=_context(),
            checkpointer=object(),
        )
    assert agent is creator.return_value
    assert creator.call_args.kwargs["tools"] == []
    assert creator.call_args.kwargs["name"] == "chat"


def test_agent_factory_rejects_unknown_agent() -> None:
    """验证当前最小模板不会把未知 Agent ID 路由到默认 Chat Agent。"""

    with pytest.raises(RuntimeRequestError) as error:
        validate_agent_id("inventory_assistant")
    assert error.value.code == "agent_not_found"


@pytest.mark.asyncio
async def test_interaction_stream_emits_text_and_completion() -> None:
    """验证普通 Agent 消息流会产生文本增量和唯一完成结果。"""

    class FakeAgent:
        """提供最小 astream/aget_state 的测试 Agent。"""

        async def aget_state(self, _config):
            """返回没有待交互项的测试状态。"""

            return SimpleNamespace(tasks=())

        async def astream(self, _input, *, config, stream_mode):
            """产生一段可见文本并记录模板要求的调用参数。"""

            assert config["configurable"]["thread_id"]
            assert stream_mode == "messages"
            yield AIMessageChunk(content="你好，世界"), {}

    events = [
        event
        async for event in stream_agent_interaction(
            agent=FakeAgent(),
            agent_id="inventory_assistant",
            request=_request(),
            runtime_context=_context(),
        )
    ]
    assert events == [
        TextDelta(text="你好，世界"),
        InteractionOutcome(status="completed", pending_interaction=None),
    ]


@pytest.mark.asyncio
async def test_interaction_resume_validates_interaction_id() -> None:
    """验证待交互恢复必须匹配当前 checkpoint 的 interactionId。"""

    pending = PendingInteraction(
        schemaVersion=1,
        interactionId="interaction-1",
        kind="clarification",
        message="请选择仓库。",
        payload={},
    )

    class InterruptingAgent:
        """模拟一次等待用户后恢复完成的业务 Agent。"""

        state_reads = 0

        async def aget_state(self, _config):
            """第一次返回待交互，恢复后返回完成状态。"""

            self.state_reads += 1
            if self.state_reads == 1:
                interrupt = SimpleNamespace(
                    value=pending.model_dump(mode="json", by_alias=True)
                )
                return SimpleNamespace(
                    tasks=(SimpleNamespace(interrupts=(interrupt,)),)
                )
            return SimpleNamespace(tasks=())

        async def astream(self, command, *, config, stream_mode):
            """验证恢复值通过 LangGraph Command 提交。"""

            assert command.resume == {"repository": "demo"}
            assert config["configurable"]["thread_id"]
            assert stream_mode == "messages"
            yield AIMessageChunk(content="已收到"), {}

    request = _request(
        interaction_response={
            "interactionId": "interaction-1",
            "value": {"repository": "demo"},
        }
    )
    events = [
        event
        async for event in stream_agent_interaction(
            agent=InterruptingAgent(),
            agent_id="inventory_assistant",
            request=request,
            runtime_context=_context(),
        )
    ]
    assert events[-1] == InteractionOutcome(status="completed", pending_interaction=None)


@pytest.mark.asyncio
async def test_ag_ui_stream_emits_complete_lifecycle(tmp_path) -> None:
    """验证 Runtime Chat 输出完整 AG-UI 文本、状态、结果和终态。"""

    settings = _settings(tmp_path)

    async def fake_interaction(**_kwargs):
        """产生确定文本和完成结果，隔离真实模型与业务 Agent。"""

        yield TextDelta(text="流式回复")
        yield InteractionOutcome(status="completed")

    with (
        patch("app.server.agui.create_chat_model", return_value=object()),
        patch("app.server.agui.get_checkpointer", new=AsyncMock(return_value=object())),
        patch("app.server.agui.create_agent", return_value=object()),
        patch("app.server.agui.stream_agent_interaction", new=fake_interaction),
    ):
        stream = build_agent_runtime_stream(
            agent_id="chat",
            request=_request(),
            runtime_context=_context(),
            settings=settings,
            accept="text/event-stream",
        )
        encoded = "".join([frame async for frame in stream])

    frames = _decode_frames(encoded)
    assert [frame["type"] for frame in frames] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "CUSTOM",
        "STATE_SNAPSHOT",
        "RUN_FINISHED",
    ]
    assert frames[4]["name"] == "agent_runtime_result.v1"
    assert frames[6]["result"]["agentRuntime"]["status"] == "completed"
