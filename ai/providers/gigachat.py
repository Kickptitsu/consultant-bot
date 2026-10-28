"""GigaChat (Сбер) — запасной провайдер.

Отличие от YandexGPT — в авторизации: постоянный «ключ авторизации» из личного
кабинета обменивается на токен доступа, который живёт 30 минут. GigaToken сам
получает токен и обновляет его за минуту до истечения.

Оба сервера GigaChat (токены и чат) подписаны сертификатом Минцифры, которого
нет в Windows. Он лежит в certs/ (скачан с Госуслуг), путь к нему — в GIGACHAT_CA_BUNDLE.
"""

import asyncio
import logging
import ssl
import time
import uuid

import httpx2

from ai.providers.base import ProviderError
from ai.providers.openai_compat import OpenAICompatibleProvider
from config import AI_TIMEOUT_SECONDS, config

logger = logging.getLogger(__name__)
CERT_HINT = " (не доверяем сертификату сервера: укажите в GIGACHAT_CA_BUNDLE сертификат Минцифры, см. README)"


def _is_certificate_error(error: BaseException) -> bool:
    """Ошибка проверки сертификата спрятана внутри ConnectError — ищем её по цепочке причин.
    По тексту не определить: Windows пишет его на языке системы."""
    while error is not None:
        if isinstance(error, ssl.SSLCertVerificationError):
            return True
        error = error.__cause__ or error.__context__
    return False


class GigaToken:
    def __init__(self, http_client: httpx2.AsyncClient) -> None:
        self.http = http_client
        self.token = ""
        self.expires_at = 0.0     # время по часам компьютера, в секундах
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        # Блокировка: если 5 клиентов спросили одновременно, токен запросим один раз
        async with self._lock:
            if self.token and time.time() < self.expires_at - 60:
                return self.token
            try:
                response = await self.http.post(
                    config.gigachat_oauth_url,
                    headers={
                        "Authorization": f"Basic {config.gigachat_auth_key}",
                        "RqUID": str(uuid.uuid4()),   # номер запроса, Сбер требует уникальный
                        "Accept": "application/json",
                    },
                    data={"scope": config.gigachat_scope},
                    timeout=AI_TIMEOUT_SECONDS,
                )
            except httpx2.HTTPError as error:
                hint = CERT_HINT if _is_certificate_error(error) else ""
                raise ProviderError(f"GigaChat: нет связи с сервером токенов: {error}{hint}") from error
            if response.status_code != 200:
                raise ProviderError(
                    f"GigaChat: токен не выдан, HTTP {response.status_code}. "
                    "Проверьте GIGACHAT_AUTH_KEY и GIGACHAT_SCOPE"
                )
            data = response.json()
            self.token = data["access_token"]
            expires_at = float(data["expires_at"])
            # Сбер присылает время в миллисекундах
            self.expires_at = expires_at / 1000 if expires_at > 10**11 else expires_at
            logger.info("GigaChat: получен новый токен доступа")
            return self.token


def create(http_client: httpx2.AsyncClient | None = None) -> OpenAICompatibleProvider:
    if http_client is None:
        http_client = httpx2.AsyncClient(verify=config.gigachat_ca_bundle or True)
    token = GigaToken(http_client)
    provider = OpenAICompatibleProvider(
        name="GigaChat",
        model=config.gigachat_model,
        base_url=config.gigachat_base_url,
        api_key=token.get,     # функция: библиотека openai спрашивает токен перед запросом
        http_client=http_client,
    )
    provider.token = token
    return provider
