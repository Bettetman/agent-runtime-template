from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk

from app.agent.context import RuntimeContext, RuntimeContextError
from app.agent.factory import create_agent, validate_agent_id
from app.interaction.schemas import PendingInteraction, RuntimeRequestError, parse_run_request
from app.interaction.service import (
    InteractionOutcome,
    TextDelta,
    _thread_key,
    stream_agent_interaction,
)
from app.models.factory import close_chat_models, create_chat_model
from app.security.adapters import XcodePrincipal
from app.security.service import (
    resolve_debug_principal,
    resolve_public_principal,
)
from app.server.agui import build_agent_runtime_stream
from app.server.app import app, create_app
from app.settings import RuntimeSettings


def _settings(tmp_path, *, model_name: str = "openai:test-model") -> RuntimeSettings:
    """构造不依赖真实凭证和用户目录的测试配置。"""

    return RuntimeSettings(
        runtime_root=tmp_path,
        host="127.0.0.1",
        port=8010,
        working_dir=tmp_path / ".agent-runtime",
        auth_enabled=False,
        runtime_profile="production",
        auth_mode="enterprise",
        public_auth_adapter_factory="",
        mock_token_ttl_seconds=3600,
        mock_user_id="local-user",
        mock_employee_id="local-employee",
        mock_sap_id="local-sap",
        mock_enterprise_id="local-enterprise",
        anonymous_session_secret="test-session-secret-that-is-long-enough",
        anonymous_session_ttl_seconds=3600,
        anonymous_cookie_name="agent_runtime_session",
        cookie_secure=False,
        allowed_origins=("http://127.0.0.1:5173",),
        debug_token="test-debug-token",
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

    return RuntimeContext(
        user_id="user-1",
        enterprise_id="enterprise-1",
        authentication_mode="enterprise",
    )


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
        "topology": "agent_runtime_direct",
        "protocol": "agent-runtime-direct.v1",
        "transport": "ag-ui-sse",
    }


@pytest.mark.asyncio
async def test_anonymous_session_is_server_issued_and_stable(tmp_path) -> None:
    """验证匿名 subject 由 Runtime 签发，并能通过受保护 Cookie 恢复。"""

    settings = _settings(tmp_path)
    created = await resolve_public_principal(
        settings,
        request=Mock(),
        authentication_adapter=None,
        authorization="",
        anonymous_token="",
    )
    assert created.context.authentication_mode == "anonymous"
    assert created.anonymous_token

    restored = await resolve_public_principal(
        settings,
        request=Mock(),
        authentication_adapter=None,
        authorization="",
        anonymous_token=created.anonymous_token or "",
    )
    assert restored.anonymous_token is None
    assert restored.context.user_id == created.context.user_id


@pytest.mark.asyncio
async def test_enterprise_adapter_builds_xcode_principal(tmp_path) -> None:
    """验证 Auth 只消费一事通适配器身份，不创建 Runtime 用户或访问令牌。"""

    settings = replace(_settings(tmp_path), auth_enabled=True)

    class FakeEnterpriseAdapter:
        """模拟行内适配器返回标准 XcodePrincipal。"""

        async def authenticate(self, _request, bearer_token):
            """只接受测试用统一认证凭据。"""

            if bearer_token != "enterprise-token":
                return None
            return XcodePrincipal(
                user_id="user-1001",
                employee_id="employee-1001",
                sap_id="sap-1001",
                enterprise_id="enterprise-1",
            )

    principal = (
        await resolve_public_principal(
            settings,
            request=Mock(),
            authentication_adapter=FakeEnterpriseAdapter(),
            authorization="Bearer enterprise-token",
            anonymous_token="",
        )
    ).context
    assert principal.user_id == "user-1001"
    assert principal.employee_id == "employee-1001"
    assert principal.sap_id == "sap-1001"
    assert principal.enterprise_id == "enterprise-1"
    assert principal.authentication_mode == "enterprise"

    with pytest.raises(RuntimeContextError):
        await resolve_public_principal(
            settings,
            request=Mock(),
            authentication_adapter=FakeEnterpriseAdapter(),
            authorization="Bearer invalid",
            anonymous_token="",
        )


def test_auth_enabled_requires_enterprise_adapter(tmp_path) -> None:
    """验证 Auth 开启但未接入一事通工厂时服务严格拒绝启动。"""

    settings = replace(_settings(tmp_path), auth_enabled=True)
    with pytest.raises(RuntimeError, match="PUBLIC_AUTH_ADAPTER_FACTORY"):
        create_app(settings)


def test_local_auth_uses_fixed_mock_user_and_memory_token(tmp_path) -> None:
    """验证行外 local 模式不收账号密码、不建表，并可注销内存令牌。"""

    settings = replace(
        _settings(tmp_path),
        auth_enabled=True,
        runtime_profile="local",
        auth_mode="local",
    )
    with TestClient(create_app(settings)) as client:
        login = client.post("/_xcode/auth/mock/login")
        assert login.status_code == 200
        assert login.json()["user"]["userId"] == "local-user"
        token = login.json()["accessToken"]

        current = client.get(
            "/_xcode/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert current.status_code == 200
        assert current.json()["userId"] == "local-user"

        logout = client.post(
            "/_xcode/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert logout.status_code == 204
        rejected = client.get(
            "/_xcode/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert rejected.status_code == 401


def test_local_auth_is_rejected_outside_local_profile(tmp_path) -> None:
    """验证 Mock 认证不能在非 local Profile 下启动。"""

    settings = replace(
        _settings(tmp_path),
        auth_enabled=True,
        auth_mode="local",
        runtime_profile="production",
    )
    with pytest.raises(RuntimeError, match="PROFILE=local"):
        create_app(settings)


@pytest.mark.asyncio
async def test_public_agent_route_uses_enterprise_adapter(tmp_path) -> None:
    """验证公开 Agent 请求把 Bearer 交给适配器并使用可信用户身份。"""

    settings = replace(_settings(tmp_path), auth_enabled=True)

    class FakeEnterpriseAdapter:
        """记录公开入口传入的 Bearer，并返回测试用户。"""

        def __init__(self):
            """初始化凭据记录。"""

            self.bearer_token = ""

        async def authenticate(self, _request, bearer_token):
            """记录 Bearer 并返回可信身份。"""

            self.bearer_token = bearer_token
            return XcodePrincipal(user_id="user-1001", enterprise_id="enterprise-1")

    async def fake_stream(**_kwargs):
        """返回最小 SSE 帧以隔离模型和 Agent 执行。"""

        yield "data: {}\n\n"

    adapter = FakeEnterpriseAdapter()
    stream_builder = Mock(side_effect=fake_stream)
    with (
        patch("app.server.app.build_agent_runtime_stream", new=stream_builder),
        TestClient(create_app(settings, adapter)) as client,
    ):
        response = client.post(
            "/agents/chat/run",
            headers={
                "Accept": "text/event-stream",
                "Authorization": "Bearer enterprise-token",
            },
            json={
                "threadId": "thread-1",
                "runId": "run-1",
                "messages": [
                    {"id": "message-1", "role": "user", "content": "你好"}
                ],
                "state": {},
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    assert response.status_code == 200
    assert adapter.bearer_token == "enterprise-token"
    assert stream_builder.call_args.kwargs["runtime_context"].user_id == "user-1001"


@pytest.mark.asyncio
async def test_enterprise_adapter_rejects_missing_bearer(tmp_path) -> None:
    """验证 Auth 模式不会回退到匿名身份。"""

    settings = replace(_settings(tmp_path), auth_enabled=True)
    with pytest.raises(RuntimeContextError):
        await resolve_public_principal(
            settings,
            request=Mock(),
            authentication_adapter=AsyncMock(),
            authorization="",
            anonymous_token="",
        )


def test_debug_principal_cannot_be_selected_by_headers(tmp_path) -> None:
    """验证 Debug Token 只建立固定 Principal，且非 loopback 请求被拒绝。"""

    settings = _settings(tmp_path)
    context = resolve_debug_principal(
        settings,
        authorization="Bearer test-debug-token",
        client_host="127.0.0.1",
    )
    assert context.user_id == "xcodeagent-local-debug"
    assert context.authentication_mode == "debug"
    with pytest.raises(RuntimeContextError):
        resolve_debug_principal(
            settings,
            authorization="Bearer test-debug-token",
            client_host="192.0.2.10",
        )


def test_checkpoint_thread_key_isolated_by_principal() -> None:
    """验证相同 Agent/thread 在不同 subject 下使用不同 checkpoint key。"""

    first = _context()
    second = first.model_copy(update={"user_id": "user-2"})
    assert _thread_key("chat", first, "thread-1") == _thread_key(
        "chat", first, "thread-1"
    )
    assert _thread_key("chat", first, "thread-1") != _thread_key(
        "chat", second, "thread-1"
    )


def test_public_agent_route_issues_anonymous_session(tmp_path) -> None:
    """验证 Public Edge 不依赖 Gateway Header，并签发服务器匿名会话。"""

    settings = _settings(tmp_path)

    async def fake_stream(**_kwargs):
        """返回最小 SSE 帧以隔离模型和 Agent 执行。"""

        yield "data: {}\n\n"

    stream_builder = Mock(side_effect=fake_stream)
    with (
        patch("app.server.app.build_agent_runtime_stream", new=stream_builder),
        TestClient(create_app(settings)) as client,
    ):
        response = client.post(
            "/agents/chat/run",
            headers={"Accept": "text/event-stream"},
            json={
                "threadId": "thread-1",
                "runId": "run-1",
                "messages": [
                    {"id": "message-1", "role": "user", "content": "你好"}
                ],
                "state": {},
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    assert response.status_code == 200
    assert "agent_runtime_session=" in response.headers["set-cookie"]
    assert (
        stream_builder.call_args.kwargs["runtime_context"].authentication_mode
        == "anonymous"
    )


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


def test_agent_factory_uses_registered_builder() -> None:
    """验证 Agent 创建只依赖当前应用显式注册的 Builder。"""

    from app.agent import factory

    generated_agent = object()
    generated_builder = Mock(return_value=generated_agent)
    model = object()
    runtime_context = _context()
    checkpointer = object()
    with patch.dict(factory._AGENT_BUILDERS, {"inventory_assistant": generated_builder}):
        agent = create_agent(
            agent_id="inventory_assistant",
            model=model,
            runtime_context=runtime_context,
            checkpointer=checkpointer,
        )

    assert agent is generated_agent
    generated_builder.assert_called_once_with(
        model=model,
        runtime_context=runtime_context,
        checkpointer=checkpointer,
    )


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
