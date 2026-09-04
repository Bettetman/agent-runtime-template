from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from app.settings import RuntimeSettings


_MODEL_PROVIDER_ALIASES = {
    "gpt": "openai",
    "claude": "anthropic",
    "qwen": "openai",
}


def _normalized_model_name(model_name: str) -> str:
    """把常用模型品牌前缀转换为 init_chat_model 支持的 Provider 前缀。"""

    provider, separator, name = model_name.partition(":")
    if not separator:
        return model_name
    normalized_provider = _MODEL_PROVIDER_ALIASES.get(provider.lower(), provider.lower())
    return f"{normalized_provider}:{name}"


@lru_cache(maxsize=1)
def create_chat_model(settings: RuntimeSettings) -> BaseChatModel:
    """通过 LangChain 统一入口创建支持流式输出的项目默认 ChatModel。"""

    settings.require_model()
    model_options: dict[str, Any] = {
        "api_key": SecretStr(settings.model_api_key),
        "temperature": settings.model_temperature,
        "max_tokens": settings.model_max_tokens,
        "timeout": settings.model_timeout_seconds,
        "max_retries": settings.model_max_retries,
        "streaming": True,
    }
    if settings.model_base_url:
        model_options["base_url"] = settings.model_base_url
    return init_chat_model(
        model=_normalized_model_name(settings.model_name),
        **model_options,
    )


async def close_chat_models() -> None:
    """清空模型工厂缓存，供服务退出或测试隔离使用。"""

    create_chat_model.cache_clear()
