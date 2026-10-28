"""Выбор провайдера ИИ по настройке AI_PROVIDER в .env.

Остальной код вызывает только get_provider().complete(...) и не знает,
кто отвечает — YandexGPT, GigaChat или демо-режим.
"""

from ai.providers.base import Completion, Provider, ProviderError
from config import config

__all__ = ["Completion", "Provider", "ProviderError", "get_provider", "set_provider", "close_provider"]

_provider: Provider | None = None


def _create() -> Provider:
    if config.ai_provider == "yandex":
        from ai.providers import yandex
        return yandex.create()
    if config.ai_provider == "gigachat":
        from ai.providers import gigachat
        return gigachat.create()
    from ai.providers.demo import DemoProvider
    return DemoProvider()


def get_provider() -> Provider:
    global _provider
    if _provider is None:
        _provider = _create()
    return _provider


def set_provider(provider: Provider | None) -> None:
    """Подменить провайдера (в тестах) или сбросить, чтобы создать заново."""
    global _provider
    _provider = provider


async def close_provider() -> None:
    if _provider is not None:
        await _provider.close()
