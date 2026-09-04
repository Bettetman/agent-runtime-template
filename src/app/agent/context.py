from __future__ import annotations

import secrets

from pydantic import BaseModel, ConfigDict, Field

from app.settings import RuntimeSettings


class RuntimeContextError(ValueError):
    """表示 Java 网关内部认证或可信上下文无效。"""

    code = "invalid_runtime_context"


class RuntimeContext(BaseModel):
    """保存通过内部认证后才可信的用户、租户、权限和链路上下文。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=256)
    scopes: tuple[str, ...] = ()
    traceparent: str = Field(default="", max_length=512)


def create_runtime_context(
    *,
    settings: RuntimeSettings,
    authorization: str,
    user_id: str,
    tenant_id: str,
    scopes: str = "",
    traceparent: str = "",
) -> RuntimeContext:
    """校验内部 Bearer token 后构造不可伪造的 RuntimeContext。"""

    expected = settings.require_gateway_token()
    scheme, separator, credential = authorization.strip().partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not secrets.compare_digest(
        credential,
        expected,
    ):
        raise RuntimeContextError("Agent Runtime 内部认证失败。")
    normalized_scopes = tuple(
        dict.fromkeys(item.strip() for item in scopes.split(",") if item.strip())
    )
    if len(normalized_scopes) > 64 or any(len(item) > 128 for item in normalized_scopes):
        raise RuntimeContextError("Agent Runtime 权限范围无效。")
    try:
        return RuntimeContext(
            user_id=user_id.strip(),
            tenant_id=tenant_id.strip(),
            scopes=normalized_scopes,
            traceparent=traceparent.strip(),
        )
    except ValueError as exc:
        raise RuntimeContextError("Agent Runtime 可信上下文缺少必要字段。") from exc

