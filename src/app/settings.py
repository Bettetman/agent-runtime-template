from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


RUNTIME_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(RUNTIME_ROOT / ".env", override=False)


@dataclass(frozen=True)
class RuntimeSettings:
    """保存 Direct Runtime 的公开入口、安全、模型和持久化配置。"""

    runtime_root: Path
    host: str
    port: int
    working_dir: Path
    auth_enabled: bool
    runtime_profile: str
    auth_mode: str
    public_auth_adapter_factory: str
    mock_token_ttl_seconds: int
    mock_user_id: str
    mock_employee_id: str
    mock_sap_id: str
    mock_enterprise_id: str
    anonymous_session_secret: str
    anonymous_session_ttl_seconds: int
    anonymous_cookie_name: str
    cookie_secure: bool
    allowed_origins: tuple[str, ...]
    debug_token: str
    model_base_url: str
    model_api_key: str
    model_name: str
    model_timeout_seconds: float
    model_max_retries: int
    model_temperature: float
    model_max_tokens: int
    business_database_path: str = ".business-data/business.sqlite"

    @property
    def checkpoint_db_path(self) -> Path:
        """返回当前 Runtime 独占的 SQLite checkpoint 文件路径。"""

        return self.working_dir / "checkpoints.sqlite"

    @property
    def resolved_business_database_path(self) -> Path:
        """返回独立于 Agent checkpoint 的业务数据库文件位置。"""

        path = Path(self.business_database_path).expanduser()
        if not path.is_absolute():
            path = self.runtime_root / path
        return path.resolve()

    def require_anonymous_session_secret(self) -> str:
        """返回只用于 Auth 关闭场景的匿名会话签名密钥。"""

        if len(self.anonymous_session_secret) < 32:
            raise RuntimeError(
                "AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET 至少需要 32 个字符。"
            )
        return self.anonymous_session_secret

    def require_model(self) -> None:
        """在第一次 Chat 前严格校验项目默认模型配置。"""

        missing = [
            name
            for name, value in (
                ("MODEL_API_KEY", self.model_api_key),
                ("MODEL_NAME", self.model_name),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("缺少模型环境变量：" + "、".join(missing) + "。")


def _configured_value(
    name: str,
    fallback_name: str | None,
    default: str = "",
) -> str:
    """按工作区配置、XCodeAgent 托管兜底、默认值的顺序读取配置。"""

    value = os.getenv(name, "").strip()
    if value:
        return value
    fallback_value = os.getenv(fallback_name, "").strip() if fallback_name else ""
    return fallback_value or default


def _positive_int(name: str, fallback_name: str | None, default: str) -> int:
    """读取正整数环境变量并拒绝零值和非法文本。"""

    try:
        value = int(_configured_value(name, fallback_name, default))
    except ValueError:
        raise RuntimeError(f"{name} 必须是正整数。") from None
    if value <= 0:
        raise RuntimeError(f"{name} 必须是正整数。")
    return value


def _non_negative_int(name: str, fallback_name: str | None, default: str) -> int:
    """读取非负整数环境变量并拒绝非法文本。"""

    try:
        value = int(_configured_value(name, fallback_name, default))
    except ValueError:
        raise RuntimeError(f"{name} 必须是非负整数。") from None
    if value < 0:
        raise RuntimeError(f"{name} 必须是非负整数。")
    return value


def _positive_float(name: str, fallback_name: str | None, default: str) -> float:
    """读取正浮点环境变量并拒绝非正值。"""

    try:
        value = float(_configured_value(name, fallback_name, default))
    except ValueError:
        raise RuntimeError(f"{name} 必须是正数。") from None
    if value <= 0:
        raise RuntimeError(f"{name} 必须是正数。")
    return value


def _boolean(name: str, default: bool) -> bool:
    """读取严格布尔环境变量。"""

    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise RuntimeError(f"{name} 必须是布尔值。")


def _allowed_origins() -> tuple[str, ...]:
    """读取去重后的 Frontend Origin 白名单。"""

    values = tuple(
        dict.fromkeys(
            item.strip().rstrip("/")
            for item in os.getenv("AGENT_RUNTIME_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        )
    )
    if "*" in values:
        raise RuntimeError("Direct Runtime 不允许通配 CORS Origin。")
    return values


@lru_cache(maxsize=1)
def load_settings() -> RuntimeSettings:
    """从环境构造一次不可变 Runtime 配置。"""

    working_dir_value = os.getenv("AGENT_RUNTIME_WORKING_DIR", ".agent-runtime").strip()
    working_dir = Path(working_dir_value).expanduser()
    if not working_dir.is_absolute():
        working_dir = RUNTIME_ROOT / working_dir
    return RuntimeSettings(
        runtime_root=RUNTIME_ROOT,
        host=os.getenv("AGENT_RUNTIME_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_positive_int("AGENT_RUNTIME_PORT", None, "8010"),
        working_dir=working_dir.resolve(),
        auth_enabled=_boolean("AGENT_RUNTIME_AUTH_ENABLED", False),
        runtime_profile=(
            os.getenv("AGENT_RUNTIME_PROFILE", "production").strip().lower()
            or "production"
        ),
        auth_mode=(
            os.getenv("AGENT_RUNTIME_AUTH_MODE", "enterprise").strip().lower()
            or "enterprise"
        ),
        public_auth_adapter_factory=os.getenv(
            "AGENT_RUNTIME_PUBLIC_AUTH_ADAPTER_FACTORY", ""
        ).strip(),
        mock_token_ttl_seconds=_positive_int(
            "AGENT_RUNTIME_MOCK_TOKEN_TTL_SECONDS", None, "3600"
        ),
        mock_user_id=(
            os.getenv("AGENT_RUNTIME_MOCK_USER_ID", "local-user").strip()
            or "local-user"
        ),
        mock_employee_id=os.getenv(
            "AGENT_RUNTIME_MOCK_EMPLOYEE_ID", "local-employee"
        ).strip(),
        mock_sap_id=os.getenv("AGENT_RUNTIME_MOCK_SAP_ID", "local-sap").strip(),
        mock_enterprise_id=os.getenv(
            "AGENT_RUNTIME_MOCK_ENTERPRISE_ID", "local-enterprise"
        ).strip(),
        anonymous_session_secret=os.getenv(
            "AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET", ""
        ).strip(),
        anonymous_session_ttl_seconds=_positive_int(
            "AGENT_RUNTIME_ANONYMOUS_SESSION_TTL_SECONDS", None, "86400"
        ),
        anonymous_cookie_name=(
            os.getenv("AGENT_RUNTIME_ANONYMOUS_COOKIE_NAME", "agent_runtime_session").strip()
            or "agent_runtime_session"
        ),
        cookie_secure=_boolean("AGENT_RUNTIME_COOKIE_SECURE", False),
        allowed_origins=_allowed_origins(),
        debug_token=os.getenv("AGENT_RUNTIME_DEBUG_TOKEN", "").strip(),
        model_base_url=_configured_value(
            "MODEL_BASE_URL", "XCODEAGENT_FALLBACK_MODEL_BASE_URL"
        ),
        model_api_key=_configured_value(
            "MODEL_API_KEY", "XCODEAGENT_FALLBACK_MODEL_API_KEY"
        ),
        model_name=_configured_value(
            "MODEL_NAME", "XCODEAGENT_FALLBACK_MODEL_NAME"
        ),
        model_timeout_seconds=_positive_float(
            "MODEL_TIMEOUT_SECONDS",
            "XCODEAGENT_FALLBACK_MODEL_TIMEOUT_SECONDS",
            "120",
        ),
        model_max_retries=_non_negative_int(
            "MODEL_MAX_RETRIES",
            "XCODEAGENT_FALLBACK_MODEL_MAX_RETRIES",
            "2",
        ),
        model_temperature=float(
            _configured_value(
                "AGENT_TEMPERATURE",
                "XCODEAGENT_FALLBACK_AGENT_TEMPERATURE",
                "0.2",
            )
        ),
        model_max_tokens=_positive_int(
            "AGENT_MAX_TOKENS",
            "XCODEAGENT_FALLBACK_AGENT_MAX_TOKENS",
            "4096",
        ),
        business_database_path=(
            os.getenv("BUSINESS_DATABASE_PATH", ".business-data/business.sqlite").strip()
            or ".business-data/business.sqlite"
        ),
    )


def clear_settings_cache() -> None:
    """清理配置缓存，供测试或受控重载使用。"""

    load_settings.cache_clear()
