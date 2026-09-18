from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from uuid import uuid4

from fastapi import Request

from app.agent.context import RuntimeContext, RuntimeContextError
from app.security.adapters import PublicAuthenticationAdapter
from app.security.tokens import (
    SecurityTokenError,
    issue_anonymous_session,
    verify_anonymous_session,
)
from app.settings import RuntimeSettings


@dataclass(frozen=True)
class PrincipalResolution:
    """返回可信 Principal 和可选的新匿名 Cookie。"""

    context: RuntimeContext
    anonymous_token: str | None = None


async def resolve_public_principal(
    settings: RuntimeSettings,
    *,
    request: Request,
    authentication_adapter: PublicAuthenticationAdapter | None,
    authorization: str,
    anonymous_token: str,
    traceparent: str = "",
) -> PrincipalResolution:
    """按 Auth 开关建立一事通用户或服务器签发的匿名 Principal。"""

    try:
        if settings.auth_enabled:
            scheme, separator, credential = authorization.strip().partition(" ")
            if separator != " " or scheme.lower() != "bearer":
                raise SecurityTokenError("缺少认证凭据。")
            if authentication_adapter is None:
                raise RuntimeError("Auth 已开启，但统一认证适配器不可用。")
            principal = await authentication_adapter.authenticate(request, credential)
            if principal is None:
                raise SecurityTokenError("认证凭据无效或已经过期。")
            return PrincipalResolution(
                RuntimeContext(
                    user_id=principal.user_id,
                    employee_id=principal.employee_id,
                    sap_id=principal.sap_id,
                    enterprise_id=principal.enterprise_id,
                    authentication_mode="enterprise",
                    traceparent=traceparent.strip(),
                )
            )

        if anonymous_token:
            claims = verify_anonymous_session(settings, anonymous_token)
            return PrincipalResolution(
                RuntimeContext(
                    user_id=claims["sub"],
                    authentication_mode="anonymous",
                    traceparent=traceparent.strip(),
                )
            )

        user_id = uuid4().hex
        token = issue_anonymous_session(settings, user_id=user_id)
        return PrincipalResolution(
            RuntimeContext(
                user_id=user_id,
                authentication_mode="anonymous",
                traceparent=traceparent.strip(),
            ),
            anonymous_token=token,
        )
    except (SecurityTokenError, ValueError) as exc:
        raise RuntimeContextError(str(exc)) from exc


def resolve_debug_principal(
    settings: RuntimeSettings,
    *,
    authorization: str,
    client_host: str,
    traceparent: str = "",
) -> RuntimeContext:
    """只为 loopback 和本次启动 Debug Token 建立固定调试 Principal。"""

    if client_host not in {"127.0.0.1", "::1", "testclient"}:
        raise RuntimeContextError("Runtime 调试入口只允许 loopback。")
    expected = settings.debug_token
    scheme, separator, credential = authorization.strip().partition(" ")
    if (
        not expected
        or separator != " "
        or scheme.lower() != "bearer"
        or not secrets.compare_digest(credential, expected)
    ):
        raise RuntimeContextError("Runtime 调试认证失败。")
    launch_namespace = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    return RuntimeContext(
        user_id="xcodeagent-local-debug",
        authentication_mode="debug",
        enterprise_id=f"debug:{launch_namespace}",
        traceparent=traceparent.strip(),
    )
