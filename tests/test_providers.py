"""Провайдеры ИИ: какой запрос уходит в YandexGPT и GigaChat (без интернета).

Вместо сети — httpx2.MockTransport: он перехватывает HTTP-запросы библиотеки openai
и отвечает так, как ответил бы настоящий сервис.
"""

import json
import ssl
import time

import httpx2
import pytest

from ai.providers import ProviderError
from ai.providers import gigachat, yandex

MESSAGES = [{"role": "system", "content": "правила"}, {"role": "user", "content": "Сколько стоит ТО-1?"}]


def chat_response(text: str = "ТО-1 стоит 2 500 ₽.") -> dict:
    """Ответ в формате OpenAI Chat Completions."""
    return {
        "id": "1", "object": "chat.completion", "created": 0, "model": "test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1500, "completion_tokens": 12, "total_tokens": 1512},
    }


class Recorder:
    """Запоминает запросы и отвечает через handler."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx2.Request] = []
        self.handler = handler

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.handler(request)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self))


async def test_yandex_request(settings):
    settings(yandex_api_key="test-key", yandex_folder_id="b1gfolder", yandex_model="yandexgpt-5.1")
    recorder = Recorder(lambda request: httpx2.Response(200, json=chat_response()))
    provider = yandex.create(http_client=recorder.client())

    completion = await provider.complete(MESSAGES)

    assert (completion.text, completion.prompt_tokens, completion.completion_tokens) == ("ТО-1 стоит 2 500 ₽.", 1500, 12)
    request = recorder.requests[0]
    assert str(request.url) == "https://ai.api.cloud.yandex.net/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["openai-project"] == "b1gfolder"
    body = json.loads(request.content)
    assert body["model"] == "gpt://b1gfolder/yandexgpt-5.1"
    assert body["messages"] == MESSAGES
    assert body["temperature"] == 0.2


async def test_yandex_error_becomes_provider_error(settings):
    settings(yandex_api_key="wrong", yandex_folder_id="b1gfolder")
    recorder = Recorder(lambda request: httpx2.Response(401, json={"error": {"message": "Unauthorized"}}))
    provider = yandex.create(http_client=recorder.client())
    with pytest.raises(ProviderError, match="AuthenticationError"):
        await provider.complete(MESSAGES)


async def test_gigachat_gets_token_once_and_refreshes_it(settings):
    settings(gigachat_auth_key="QmFzZTY0S2V5", gigachat_scope="GIGACHAT_API_PERS")
    tokens = iter(["token-1", "token-2"])

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v2/oauth":
            expires_ms = int((time.time() + 1800) * 1000)   # Сбер присылает миллисекунды
            return httpx2.Response(200, json={"access_token": next(tokens), "expires_at": expires_ms})
        return httpx2.Response(200, json=chat_response())

    recorder = Recorder(handler)
    provider = gigachat.create(http_client=recorder.client())

    await provider.complete(MESSAGES)
    await provider.complete(MESSAGES)
    oauth, chat1, chat2 = recorder.requests
    assert oauth.headers["authorization"] == "Basic QmFzZTY0S2V5"
    assert len(oauth.headers["rquid"]) == 36
    assert oauth.content == b"scope=GIGACHAT_API_PERS"
    assert str(chat1.url) == "https://api.giga.chat/v1/chat/completions"
    assert chat1.headers["authorization"] == chat2.headers["authorization"] == "Bearer token-1"
    assert json.loads(chat1.content)["model"] == "GigaChat-3-Lightning"

    provider.token.expires_at = time.time() + 30   # до конца жизни токена меньше минуты
    await provider.complete(MESSAGES)
    assert recorder.requests[-1].headers["authorization"] == "Bearer token-2"


async def test_gigachat_certificate_error_has_hint(settings):
    """Windows пишет ошибку сертификата на языке системы — подсказку ищем по типу ошибки."""
    settings(gigachat_auth_key="QmFzZTY0S2V5")

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("Цепочка сертификатов обработана...") from ssl.SSLCertVerificationError()

    provider = gigachat.create(http_client=Recorder(handler).client())
    with pytest.raises(ProviderError, match="GIGACHAT_CA_BUNDLE"):
        await provider.complete(MESSAGES)


async def test_gigachat_bad_key(settings):
    settings(gigachat_auth_key="wrong")
    recorder = Recorder(lambda request: httpx2.Response(401, json={"message": "Authorization error"}))
    provider = gigachat.create(http_client=recorder.client())
    with pytest.raises(ProviderError, match="токен не выдан, HTTP 401"):
        await provider.complete(MESSAGES)
