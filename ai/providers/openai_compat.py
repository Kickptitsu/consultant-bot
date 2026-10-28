"""Провайдер для любого OpenAI-совместимого API.

И Yandex AI Studio, и GigaChat понимают запросы в формате OpenAI
(POST /chat/completions), поэтому для обоих подходит официальная библиотека openai —
отличаются только адрес, модель и способ авторизации.
"""

import logging
from collections.abc import Awaitable, Callable

import httpx2
from openai import AsyncOpenAI, OpenAIError

from ai.providers.base import Completion, Provider, ProviderError
from config import AI_MAX_TOKENS, AI_TEMPERATURE, AI_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: str | Callable[[], Awaitable[str]],
        project: str | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,          # строка или функция, которая выдаёт свежий токен
            base_url=base_url,
            project=project,          # Yandex: ID каталога (заголовок OpenAI-Project)
            timeout=AI_TIMEOUT_SECONDS,
            max_retries=1,            # один повтор при сбое сети, дольше клиент ждать не станет
            http_client=http_client,
        )

    async def complete(self, messages: list[dict]) -> Completion:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=AI_TEMPERATURE,
                max_tokens=AI_MAX_TOKENS,
            )
        except OpenAIError as error:
            # В тексте ошибки ключа нет — только код ответа и сообщение сервиса
            raise ProviderError(f"{self.name}: {type(error).__name__}: {error}") from error

        text = response.choices[0].message.content if response.choices else ""
        usage = response.usage
        return Completion(
            text=text or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

    async def close(self) -> None:
        await self.client.close()
