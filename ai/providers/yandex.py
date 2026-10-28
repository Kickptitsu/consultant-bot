"""YandexGPT через OpenAI-совместимый API Yandex AI Studio.

Нужны ключ API сервисного аккаунта и ID каталога (folder ID) — см. README.
Модель указывается как gpt://<ID каталога>/<имя модели>.
"""

import httpx2

from ai.providers.openai_compat import OpenAICompatibleProvider
from config import config


def create(http_client: httpx2.AsyncClient | None = None) -> OpenAICompatibleProvider:
    model = config.yandex_model
    if not model.startswith("gpt://"):
        model = f"gpt://{config.yandex_folder_id}/{model}"
    return OpenAICompatibleProvider(
        name="YandexGPT",
        model=model,
        base_url=config.yandex_base_url,
        api_key=config.yandex_api_key,
        project=config.yandex_folder_id,
        http_client=http_client,
    )
