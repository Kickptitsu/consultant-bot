"""Общее для всех провайдеров ИИ: что они принимают и что возвращают.

Провайдер получает список сообщений в формате OpenAI:
[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, ...]
и возвращает текст ответа и сколько токенов на него ушло.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Completion:
    text: str
    prompt_tokens: int       # токены запроса: промт + база знаний + история + вопрос
    completion_tokens: int   # токены ответа

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ProviderError(Exception):
    """ИИ не ответил: нет связи, неверный ключ, превышен лимит и т. п."""


class Provider:
    name = "base"

    async def complete(self, messages: list[dict]) -> Completion:
        raise NotImplementedError

    async def close(self) -> None:
        """Закрыть соединения при остановке бота."""
