from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


RUNTIME_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(RUNTIME_ROOT / ".env", override=False)


@dataclass(frozen=True)
class RuntimeSettings:
    """保存模板运行、内部认证、模型和 checkpoint 的当前配置。"""

    runtime_root: Path
    host: str
    port: int
    gateway_token: str
    working_dir: Path
    model_base_url: str
    model_api_key: str
    model_name: str
    model_timeout_seconds: float
    model_max_retries: int
    model_temperature: float
    model_max_tokens: int
    backend_base_url: str = ""
    tool_gateway_token: str = ""

    @property
    def checkpoint_db_path(self) -> Path:
        """返回当前 Runtime 独占的 SQLite checkpoint 文件路径。"""

        return self.working_dir / "checkpoints.sqlite"

    def require_gateway_token(self) -> str:
        """返回内部网关 token，缺失时拒绝开放 Chat。"""

        if not self.gateway_token:
            raise RuntimeError("缺少 AGENT_RUNTIME_GATEWAY_TOKEN。")
        return self.gateway_token

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

    def require_backend_base_url(self) -> str:
        """返回只含 HTTP(S) Origin 的 Java Backend 内部地址。"""

        value = self.backend_base_url.rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RuntimeError("AGENT_RUNTIME_BACKEND_BASE_URL 配置无效。")
        return value

    def require_tool_gateway_token(self) -> str:
        """返回 Runtime 调用 Java Tool Gateway 使用的内部凭据。"""

        if not self.tool_gateway_token:
            raise RuntimeError("缺少 AGENT_RUNTIME_TOOL_GATEWAY_TOKEN。")
        return self.tool_gateway_token


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
        gateway_token=os.getenv("AGENT_RUNTIME_GATEWAY_TOKEN", "").strip(),
        working_dir=working_dir.resolve(),
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
        backend_base_url=os.getenv("AGENT_RUNTIME_BACKEND_BASE_URL", "").strip(),
        tool_gateway_token=os.getenv("AGENT_RUNTIME_TOOL_GATEWAY_TOKEN", "").strip(),
    )


def clear_settings_cache() -> None:
    """清理配置缓存，供测试或受控重载使用。"""

    load_settings.cache_clear()
