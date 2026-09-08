# XCodeAgent Agent Runtime Template

可独立安装、启动和对话的最小 Python 3.12 + Deep Agents 应用。模板在 `src/app/agent/` 中装配 `chat` Agent，并提供多模型初始化、SQLite checkpoint、可信 Java 网关上下文和 AG-UI SSE Chat；具体业务能力和工具由 XCodeAgent 在 Build DAG 确认后继续生成。

## 目录所有权

```text
config/agents/                     平台编译的业务 Agent Definition
src/app/agent/                     Definition Loader、Generic Factory 与运行上下文
src/app/extensions/                仅在契约显式需要时生成的 Python Extension
src/app/models/                    多模型初始化
src/app/tools/                     后续业务工具扩展
src/app/interaction/               对话、中断与恢复
src/app/server/                    HTTP 与 AG-UI SSE
tests/                             Runtime 契约测试
```

根目录不再额外建立 `agents/` 或 `tools/`。`chat` 只用于保证模板开箱可运行，不代表任何业务 Agent 已完成。模板不预置业务工具、知识库、MCP、插件、Docker、复杂审批或会话管理 API。

## 本地启动

```bash
cp .env.example .env
# 编辑 .env，至少填写 AGENT_RUNTIME_GATEWAY_TOKEN、MODEL_NAME 和 MODEL_API_KEY。

uv sync --frozen
uv run agent-runtime
```

默认监听 `127.0.0.1:8010`：

```bash
curl http://127.0.0.1:8010/health
```

`/health` 不要求模型凭证，便于模板下载后立即执行 readiness。Chat 会在第一次运行时严格检查模型配置。

## 模型配置

模型统一通过 LangChain `init_chat_model` 初始化，`MODEL_NAME` 使用 `provider:model`：

| 模型 | `MODEL_NAME` 示例 | `MODEL_BASE_URL` |
| --- | --- | --- |
| DeepSeek | `deepseek:deepseek-chat` | 官方 API 可留空 |
| GPT | `openai:gpt-4.1-mini` | 官方 API 可留空 |
| Claude | `anthropic:claude-sonnet-4-5` | 官方 API 可留空 |
| Qwen | `qwen:qwen-plus` | 填写 DashScope OpenAI 兼容地址 |

也支持 `gpt:`、`claude:`、`qwen:` 这些便于阅读的品牌别名；它们会分别映射为 `openai:`、`anthropic:`、`openai:`。模型名、地址和密钥只能来自运行环境，不能由 Chat 请求覆盖。

## 内部 AG-UI Chat

```http
POST /internal/agents/{agent_id}/run
Accept: text/event-stream
Authorization: Bearer <AGENT_RUNTIME_GATEWAY_TOKEN>
X-Agent-User-Id: <trusted-user-id>
X-Agent-Tenant-Id: <trusted-tenant-id>
X-Agent-Scopes: scope-a,scope-b
```

请求体使用 AG-UI `RunAgentInput`。本地独立调试可直接调用模板自带的 `chat` Agent：

```bash
curl -N http://127.0.0.1:8010/internal/agents/chat/run \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer replace-with-a-local-gateway-token' \
  -H 'X-Agent-User-Id: local-user' \
  -H 'X-Agent-Tenant-Id: local-tenant' \
  --data '{"threadId":"local-thread","runId":"local-run","messages":[{"id":"message-1","role":"user","content":"写一个快速排序"}],"state":{},"tools":[],"context":[],"forwardedProps":{}}'
```

接入生成应用后，浏览器不得直连该接口；Java Agent Gateway 校验客户端身份后注入内部认证和可信上下文。

恢复待交互运行时，通过同一个 endpoint、`agentId` 和 `threadId` 发起新 Run，并在 `forwardedProps` 中提交：

```json
{
  "interactionResponse": {
    "interactionId": "interaction-id-from-state",
    "value": {}
  }
}
```

## Agent 创建方式

`src/app/agent/factory.py` 是唯一 Agent 组合根。它保留模板内置 `chat`，业务 Agent
则从 `config/agents/<agent_id>.json` 读取完整七类配置，由 Generic Factory 统一调用
`create_deep_agent`。修改 Prompt、模型参数、Memory、Tools、Skills、Knowledge 或 Context
只会更新 Definition，不会生成一个新的业务 Agent Python 类。

只有 Definition 无法表达的特殊算法、状态机或 SDK 集成才启用
`src/app/extensions/<agent_id>.py`，固定入口为：

```python
from deepagents import create_deep_agent


def create_agent(*, definition, model, tools, runtime_context, checkpointer):
    """实现 Definition 无法表达的特殊能力。"""

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=definition.compiled_prompt,
        checkpointer=checkpointer,
    )
```

模板把由 `init_chat_model` 创建的模型、可信上下文和当前 workspace 的 checkpointer 注入工厂。Extension 不得再次初始化模型。标准 Tool 由 Definition 构造；在 Java Tool Gateway 协议尚未完成时保持 `gateway_not_configured` 的 fail-closed 行为，不编造 URL、Header、Token 或成功响应。

业务 Tool 只允许调用 Agent Contract 已展开的 Java Endpoint。Java 内部地址和服务凭据分别来自 `AGENT_RUNTIME_BACKEND_BASE_URL` 与 `AGENT_RUNTIME_TOOL_GATEWAY_TOKEN`；生成代码只能通过 `RuntimeSettings.require_backend_base_url()` 和 `require_tool_gateway_token()` 读取，不能把值写入源码、Contract、日志或模型输出。用户、租户、Scope 和 Trace 只能从模板校验后的 `RuntimeContext` 转发。

当业务 Agent 需要澄清或确认时，LangGraph interrupt value 必须符合：

```json
{
  "schemaVersion": 1,
  "interactionId": "stable-current-interaction-id",
  "kind": "clarification",
  "message": "需要用户补充的信息",
  "payload": {}
}
```

`kind` 支持 `clarification`、`confirmation` 和 `approval_required`。审批决定必须由 Java/平台的正式审批边界产生，Agent Runtime 不能自行批准。

## 验证

```bash
uv run python -m compileall -q src tests
uv run pytest
```
