# XCodeAgent Agent Runtime Direct Template

面向 `agent_runtime_direct` 拓扑的 Python 3.12 + DeepAgents 模板。生成应用只包含 `frontend/` 和 `agent-runtime/`；Frontend 通过公开 AG-UI SSE 直接访问 Runtime，不依赖 Java Backend 或 Gateway。

## 能力边界

- `/agents/{agent_id}/run`：公开 AG-UI SSE；
- Auth 关闭：Runtime 签发 HttpOnly 匿名会话；
- Auth 开启：行外 local Profile 使用固定 Mock 用户，行内 production 使用统一认证适配器；
- thread/checkpoint 按认证模式、可信用户和 Agent 隔离；
- `/debug/agents/{agent_id}/run`：仅 loopback、固定 Debug Principal；
- 不创建用户、密码、角色或权限表，不包含 RBAC、Java Endpoint Tool 或数据库业务模型。

`src/app/agent/factory.py` 是唯一 Agent 组合根。模板内置 `chat` 只证明模型、AG-UI、checkpoint 和交互恢复可运行；具体业务 Agent、Runtime-native Tool、Skill 和 Knowledge 仍由 XCodeAgent 在 Build DAG 确认后生成。

## 目录所有权

```text
src/app/agent/          Agent 创建与可信 RuntimeContext
src/app/security/       一事通适配器合同、local Mock 和匿名会话
src/app/models/         项目默认模型初始化
src/app/tools/          Runtime-native Tool 扩展
src/app/interaction/    对话、中断与恢复
src/app/persistence/    owner-scoped checkpoint
src/app/server/         Public Edge、Auth、Debug 和 AG-UI SSE
tests/                  Runtime 合同测试
```

业务 Agent 不得修改 Public Edge、认证适配器、Principal、CORS 或 ownership 基础设施。

## 本地启动

```bash
cp .env.example .env
# Auth 关闭时设置匿名会话密钥；运行 Agent 前设置 MODEL_NAME 和 MODEL_API_KEY。

uv sync --frozen
uv run agent-runtime
```

默认监听 `127.0.0.1:8010`：

```bash
curl http://127.0.0.1:8010/health
```

`/health` 不要求模型配置或身份凭据。

## 匿名模式

保持：

```text
AGENT_RUNTIME_AUTH_ENABLED=false
```

第一次调用 Public AG-UI 时，Runtime 创建不可由客户端选择的匿名 subject，并通过 HttpOnly Cookie 返回会话。后续 thread、run 和 checkpoint 都绑定该 subject。

```bash
curl -N -c /tmp/agent-runtime-cookie.txt \
  http://127.0.0.1:8010/agents/chat/run \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  --data '{"threadId":"local-thread","runId":"local-run","messages":[{"id":"message-1","role":"user","content":"写一个快速排序"}],"state":{},"tools":[],"context":[],"forwardedProps":{}}'
```

## 行外本地认证

行外开发需要模拟 Auth 时，使用与 Java Gateway 一致的“接口 + local Mock”模式：

```text
AGENT_RUNTIME_AUTH_ENABLED=true
AGENT_RUNTIME_PROFILE=local
AGENT_RUNTIME_AUTH_MODE=local
```

Mock 登录不接收用户名或密码，只为环境变量中的固定用户签发当前进程内的随机令牌：

```http
POST /_xcode/auth/mock/login
GET  /_xcode/auth/me
POST /_xcode/auth/logout
```

调用 Agent 时携带登录结果中的 Token：

```http
POST /agents/chat/run
Authorization: Bearer <accessToken>
Accept: text/event-stream
```

local 认证只能在 `AGENT_RUNTIME_PROFILE=local` 下启动，令牌不落库、进程退出即失效，也不能用于生产。

## 行内统一认证

行内部署启用：

```text
AGENT_RUNTIME_AUTH_ENABLED=true
AGENT_RUNTIME_PROFILE=production
AGENT_RUNTIME_AUTH_MODE=enterprise
AGENT_RUNTIME_PUBLIC_AUTH_ADAPTER_FACTORY=<python-module>:<factory-function>
```

工厂函数同步接收 `RuntimeSettings`，返回实现 `PublicAuthenticationAdapter` 的对象；其异步 `authenticate(request, bearer_token)` 方法负责调用行内“一事通”，并返回 `XcodePrincipal(user_id, employee_id, sap_id, enterprise_id)` 或 `None`。模板不猜测一事通协议，也不签发生产 Token；实际搬入行内时只需提供该适配器实现。

Auth 只建立可信用户身份，不承载 RBAC。所有 Runtime 资源按可信 `user_id` 与 `enterprise_id` 隔离；Auth 开启后认证失败不会回退为匿名身份。

## 本地调试

XCodeAgent 显式调试启动时注入 `AGENT_RUNTIME_DEBUG_TOKEN`。调试接口固定使用 `xcodeagent-local-debug` Principal，不接受用户、租户或 Scope Header：

```bash
curl -N http://127.0.0.1:8010/debug/agents/chat/run \
  -H 'Authorization: Bearer <debug-token>' \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  --data '{"threadId":"debug-thread","runId":"debug-run","messages":[{"id":"message-1","role":"user","content":"你好"}],"state":{},"tools":[],"context":[],"forwardedProps":{}}'
```

调试 Token 不得进入普通预览或生产环境。

## 模型配置

模型统一通过 LangChain `init_chat_model` 初始化。模型名、Base URL 和密钥只来自运行环境，AG-UI 请求不能覆盖。

| 模型 | `MODEL_NAME` 示例 |
| --- | --- |
| DeepSeek | `deepseek:deepseek-chat` |
| GPT | `openai:gpt-4.1-mini` |
| Claude | `anthropic:claude-sonnet-4-5` |
| Qwen | `qwen:qwen-plus` |

## Agent 创建方式

业务 Agent 继续在 `src/app/agent/factory.py` 的 Builder 注册表中显式注册，并复用模板注入的模型、可信 RuntimeContext 和 checkpointer。未知 Agent ID 返回稳定的 `agent_not_found`。

RuntimeContext 只由统一认证适配器、匿名会话或 Debug 入口建立。业务代码不得从请求体、消息、Header 或 Tool 参数读取自报用户身份。

## 验证

```bash
uv run python -m compileall -q src tests
uv run pytest
```
