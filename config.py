"""Настройки бота. Всё секретное читается из файла .env."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Подхватываем переменные из .env, лежащего рядом с этим файлом
load_dotenv(os.path.join(BASE_DIR, ".env"))

AI_PROVIDERS = ("demo", "yandex", "gigachat")


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: tuple[int, ...]
    db_path: str
    knowledge_dir: str           # папка с базой знаний (markdown-файлы)
    privacy_policy_url: str      # ссылка на опубликованную политику ПД ('' — не задана)
    operators_chat_id: int | None  # группа, куда приходят вопросы для операторов (None — выключено)

    ai_provider: str             # demo / yandex / gigachat
    # YandexGPT (Yandex AI Studio)
    yandex_api_key: str
    yandex_folder_id: str        # ID каталога в Yandex Cloud
    yandex_model: str            # имя модели, например yandexgpt-5.1
    yandex_base_url: str
    # GigaChat
    gigachat_auth_key: str       # «ключ авторизации» из личного кабинета GigaChat
    gigachat_scope: str          # GIGACHAT_API_PERS / GIGACHAT_API_B2B / GIGACHAT_API_CORP
    gigachat_model: str
    gigachat_base_url: str
    gigachat_oauth_url: str
    gigachat_ca_bundle: str      # путь к сертификату Минцифры ('' — системные сертификаты)


def _path(value: str) -> str:
    """Относительный путь считаем от папки проекта, а не от места запуска."""
    return value if os.path.isabs(value) else os.path.join(BASE_DIR, value)


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN. Создайте файл .env по образцу .env.example")

    # "111, 222" -> (111, 222); пустые куски пропускаем
    raw_ids = os.getenv("ADMIN_IDS", "")
    admin_ids = tuple(int(part) for part in raw_ids.replace(" ", "").split(",") if part)

    raw_chat = os.getenv("OPERATORS_CHAT_ID", "").strip()
    try:
        operators_chat_id = int(raw_chat) if raw_chat else None
    except ValueError:
        raise RuntimeError("OPERATORS_CHAT_ID должен быть числом, например -1001234567890")

    provider = os.getenv("AI_PROVIDER", "demo").strip().lower() or "demo"
    if provider not in AI_PROVIDERS:
        raise RuntimeError("AI_PROVIDER в .env должен быть demo, yandex или gigachat")

    config = Config(
        bot_token=token,
        admin_ids=admin_ids,
        db_path=_path(os.getenv("DB_PATH", "consultant.db")),
        knowledge_dir=_path(os.getenv("KNOWLEDGE_DIR", "knowledge")),
        privacy_policy_url=os.getenv("PRIVACY_POLICY_URL", "").strip(),
        operators_chat_id=operators_chat_id,
        ai_provider=provider,
        yandex_api_key=os.getenv("YANDEX_API_KEY", "").strip(),
        yandex_folder_id=os.getenv("YANDEX_FOLDER_ID", "").strip(),
        yandex_model=os.getenv("YANDEX_MODEL", "").strip() or "yandexgpt-5.1",
        yandex_base_url=os.getenv("YANDEX_BASE_URL", "").strip() or "https://ai.api.cloud.yandex.net/v1",
        gigachat_auth_key=os.getenv("GIGACHAT_AUTH_KEY", "").strip(),
        gigachat_scope=os.getenv("GIGACHAT_SCOPE", "").strip() or "GIGACHAT_API_PERS",
        gigachat_model=os.getenv("GIGACHAT_MODEL", "").strip() or "GigaChat-3-Lightning",
        gigachat_base_url=os.getenv("GIGACHAT_BASE_URL", "").strip() or "https://api.giga.chat/v1",
        gigachat_oauth_url=(
            os.getenv("GIGACHAT_OAUTH_URL", "").strip()
            or "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        ),
        gigachat_ca_bundle=os.getenv("GIGACHAT_CA_BUNDLE", "").strip(),
    )

    # Сразу говорим, чего не хватает, — иначе бот упадёт только на первом вопросе клиента
    if provider == "yandex" and not (config.yandex_api_key and config.yandex_folder_id):
        raise RuntimeError("AI_PROVIDER=yandex: заполните в .env YANDEX_API_KEY и YANDEX_FOLDER_ID")
    if provider == "gigachat" and not config.gigachat_auth_key:
        raise RuntimeError("AI_PROVIDER=gigachat: заполните в .env GIGACHAT_AUTH_KEY")
    if config.gigachat_ca_bundle:
        object.__setattr__(config, "gigachat_ca_bundle", _path(config.gigachat_ca_bundle))
        if provider == "gigachat" and not os.path.isfile(config.gigachat_ca_bundle):
            raise RuntimeError(f"GIGACHAT_CA_BUNDLE: файл не найден — {config.gigachat_ca_bundle}")
    return config


config = load_config()

# ---------- Настройки, которые не секретны (поэтому в коде, а не в .env) ----------

# Сколько последних сообщений диалога ИИ видит, чтобы понимать уточняющие вопросы
# («а сколько это по времени?»). Больше — дороже: каждое сообщение — это токены.
HISTORY_MESSAGES = 6
# Сообщения старше этого в контекст не берём: вчерашний разговор только запутает ИИ
HISTORY_TTL_MINUTES = 60
# Через сколько дней переписка удаляется из базы (обещано клиенту в тексте согласия)
HISTORY_RETENTION_DAYS = 30

# Сколько последних сообщений клиента показать оператору в карточке запроса
# и за какой срок (часов): по ним оператор поймёт, о чём шла речь с ИИ
OPERATOR_CARD_MESSAGES = 8
OPERATOR_CARD_HOURS = 24

# Ограничения на файлы базы знаний, которые присылает админ
KNOWLEDGE_MAX_FILE_KB = 100
# Вся база отправляется ИИ с каждым вопросом. Если она больше этого, запрос станет
# дорогим и может не влезть в модель — пора переходить на поиск по фрагментам (RAG)
KNOWLEDGE_MAX_TOKENS = 15000

# ---------- Лимиты: защита от спама и от неожиданного счёта за ИИ ----------

# Самое длинное сообщение клиента (символов). Длиннее — просим сократить:
# вопрос автосервису укладывается в пару абзацев, а длинный текст — это дорогие токены
MESSAGE_MAX_CHARS = 1000
# Сколько вопросов ИИ один клиент может задать за день (приветствия не считаются)
USER_DAILY_QUESTIONS = 30
# Сколько токенов ИИ бот может потратить за день на всех клиентов. Дальше — до полуночи
# вопросы уходят операторам. Ориентир: один вопрос — около 2–2,5 тыс. токенов
DAILY_TOKEN_LIMIT = 300_000

# Защита группы операторов от спама. Telegram пропускает в одну группу ~20 сообщений
# в минуту на ВСЕХ клиентов — один спамер иначе отрезал бы операторов от остальных
OPERATOR_MESSAGES_PER_MINUTE = 5   # сообщений клиента в открытом запросе за минуту
USER_DAILY_HANDOFFS = 5            # запросов к оператору на клиента в день (с отменёнными)
BLOCK_HOURS = 24                   # на сколько оператор может заблокировать клиента

# Параметры ответа ИИ: низкая «температура» — меньше фантазии, больше точности
AI_TEMPERATURE = 0.2
AI_MAX_TOKENS = 600
AI_TIMEOUT_SECONDS = 30
