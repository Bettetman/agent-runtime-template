from __future__ import annotations

import importlib
import inspect
import secrets
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from app.settings import RuntimeSettings


class XcodePrincipal(BaseModel):
    """镜像 Java XcodePrincipal，保存一事通适配器恢复的最小可信身份。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    employee_id: str | None = Field(default=None, max_length=256)
    sap_id: str | None = Field(default=None, max_length=256)
    enterprise_id: str | None = Field(default=None, max_length=256)


@runtime_checkable
class PublicAuthenticationAdapter(Protocol):
    """定义公开 Bearer 凭据到行内可信用户身份的异步适配器合同。"""

    async def authenticate(
        self,
        request: Request,
        bearer_token: str,
    ) -> XcodePrincipal | None:
        """校验公开凭据并返回可信身份；无效凭据返回空结果。"""


@dataclass(frozen=True)
class MockLoginResult:
    """保存行外本地 Mock 登录产生的随机令牌和固定用户。"""

    access_token: str
    expires_at: int
    user: XcodePrincipal


class LocalPublicAuthenticationAdapter:
    """使用固定 Mock 用户和内存令牌实现仅限行外本地的认证适配器。"""

    def __init__(self, settings: RuntimeSettings) -> None:
        """读取固定 Mock 用户，并创建当前进程独占的会话容器。"""

        self._settings = settings
        self._sessions: dict[str, int] = {}
        self._user = XcodePrincipal(
            user_id=settings.mock_user_id,
            employee_id=settings.mock_employee_id or None,
            sap_id=settings.mock_sap_id or None,
            enterprise_id=settings.mock_enterprise_id or None,
        )

    def login(self) -> MockLoginResult:
        """不接收用户输入，为唯一 Mock 用户创建随机内存令牌。"""

        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + self._settings.mock_token_ttl_seconds
        self._sessions[token] = expires_at
        return MockLoginResult(token, expires_at, self._user)

    async def authenticate(
        self,
        _request: Request,
        bearer_token: str,
    ) -> XcodePrincipal | None:
        """校验当前进程的 Mock 令牌并恢复固定用户。"""

        expires_at = self._sessions.get(bearer_token)
        if expires_at is None or expires_at <= int(time.time()):
            self._sessions.pop(bearer_token, None)
            return None
        return self._user

    def logout(self, bearer_token: str) -> None:
        """使指定 Mock 令牌立即失效。"""

        self._sessions.pop(bearer_token, None)


def load_public_authentication_adapter(
    settings: RuntimeSettings,
) -> PublicAuthenticationAdapter:
    """从受控工厂加载一事通适配器，并在 Auth 开启时严格失败。"""

    factory_path = settings.public_auth_adapter_factory
    module_name, separator, attribute_name = factory_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise RuntimeError(
            "Auth 已开启，但未配置有效的 "
            "AGENT_RUNTIME_PUBLIC_AUTH_ADAPTER_FACTORY（格式：module:factory）。"
        )
    try:
        factory = getattr(importlib.import_module(module_name), attribute_name)
        adapter = factory(settings)
    except (ImportError, AttributeError, TypeError) as exc:
        raise RuntimeError("无法加载统一认证适配器工厂。") from exc
    if inspect.isawaitable(adapter):
        raise RuntimeError("统一认证适配器工厂必须同步返回适配器实例。")
    if not isinstance(adapter, PublicAuthenticationAdapter):
        raise RuntimeError("统一认证适配器未实现 PublicAuthenticationAdapter。")
    return adapter


def create_public_authentication_adapter(
    settings: RuntimeSettings,
) -> PublicAuthenticationAdapter | None:
    """按 Auth 开关和运行模式创建行外 Mock 或行内统一认证适配器。"""

    if not settings.auth_enabled:
        return None
    validate_authentication_settings(settings)
    if settings.auth_mode == "local":
        return LocalPublicAuthenticationAdapter(settings)
    return load_public_authentication_adapter(settings)


def validate_authentication_settings(settings: RuntimeSettings) -> None:
    """验证认证模式，并阻止 local Mock 身份进入非本地环境。"""

    if not settings.auth_enabled:
        return
    if settings.auth_mode not in {"enterprise", "local"}:
        raise RuntimeError("AGENT_RUNTIME_AUTH_MODE 只允许为 enterprise 或 local。")
    if settings.auth_mode == "local" and settings.runtime_profile != "local":
        raise RuntimeError("local 认证模式只允许在 AGENT_RUNTIME_PROFILE=local 下使用。")
