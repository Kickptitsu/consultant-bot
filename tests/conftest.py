"""Общая подготовка для всех тестов (pytest читает этот файл первым).

Тесты не трогают ваш .env, базу и папку knowledge/ и не ходят в интернет:
- настройки подменяются выдуманными ДО импорта кода бота;
- у каждого теста своя временная база и своя копия базы знаний;
- Telegram заменён FakeTelegram — он запоминает, что бот «отправил»;
- ИИ — демо-режим или FakeAI, который отвечает заранее заданным текстом.

Фикстура — это то, что pytest готовит для теста: достаточно указать её имя
в аргументах, например async def test_...(consultant): ...
"""

import asyncio
import itertools
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# load_dotenv() в config.py не перезаписывает уже заданные переменные,
# поэтому настоящие токены и ключи из .env в тесты не попадут
TEST_ENV = {
    "BOT_TOKEN": "123456:" + "A" * 35,
    "ADMIN_IDS": "42,43",
    "DB_PATH": os.path.join(tempfile.gettempdir(), "consultant_tests_unused.db"),
    "KNOWLEDGE_DIR": os.path.join(tempfile.gettempdir(), "consultant_tests_unused_kb"),
    "PRIVACY_POLICY_URL": "",
    "AI_PROVIDER": "demo",
    "YANDEX_API_KEY": "",
    "YANDEX_FOLDER_ID": "",
    "YANDEX_MODEL": "",
    "GIGACHAT_AUTH_KEY": "",
    "GIGACHAT_CA_BUNDLE": "",
    "OPERATORS_CHAT_ID": "",   # включается фикстурой operators
}
os.environ.update(TEST_ENV)

from aiogram import Bot, Dispatcher  # noqa: E402  (импорт после настройки окружения)
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery,
    EditMessageText,
    GetFile,
    GetMe,
    SendChatAction,
    SendDocument,
    SendMessage,
    SetMessageReaction,
    SetMyCommands,
)
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Document, File, Message, Update, User  # noqa: E402

from ai.providers import Completion, Provider, ProviderError, set_provider  # noqa: E402
from config import config  # noqa: E402
from database.db import init_db  # noqa: E402
from handlers import routers  # noqa: E402
from services import handoff  # noqa: E402

CLIENT = 555   # Telegram ID клиента
CLIENT2 = 556
ADMIN = 42     # администраторы из ADMIN_IDS
ADMIN2 = 43
OPS = -100     # группа операторов
OPERATOR = 77  # операторы — участники этой группы
OPERATOR2 = 78

# Роутер можно подключить только к одному диспетчеру, поэтому он один на все тесты.
# Память FSM сбрасывается перед каждым тестом.
DISPATCHER = Dispatcher()
DISPATCHER.include_routers(*routers)


# ---------- Имитация Telegram ----------

@dataclass
class Sent:
    """Одно действие бота: kind = send / edit / answer / document / typing / reaction."""
    kind: str
    chat_id: int | None = None
    text: str = ""
    markup: object = None
    message_id: int | None = None
    alert: bool | None = None
    method: object = None
    reply_to: int | None = None   # на какое сообщение бот ответил (reply)

    def buttons(self) -> list[str]:
        rows = getattr(self.markup, "inline_keyboard", None) or getattr(self.markup, "keyboard", None) or []
        return [button.text for row in rows for button in row]

    def callbacks(self) -> list[str]:
        rows = getattr(self.markup, "inline_keyboard", None) or []
        return [button.callback_data for row in rows for button in row]


class FakeTelegram(BaseSession):
    """Вместо серверов Telegram: запоминает запросы бота и отвечает правдоподобно."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Sent] = []
        self.files: dict[str, bytes] = {}   # file_id -> содержимое (для bot.download)
        self.blocked: set[int] = set()      # чаты, куда бот писать не может (клиент заблокировал бота)
        self.retry_after: dict[int, int] = {}   # чат -> «подождите N секунд» на ближайшую отправку
        self._ids = itertools.count(1000)

    def _message(self, chat_id: int, text: str | None = None) -> Message:
        return Message(message_id=next(self._ids), date=datetime.now(),
                       chat=Chat(id=chat_id, type="supergroup" if chat_id < 0 else "private"), text=text)

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="Гараж 18", username="garage_test_bot")
        if isinstance(method, (SetMyCommands,)):
            return True
        if isinstance(method, SendChatAction):
            self.calls.append(Sent("typing", method.chat_id))
            return True
        if getattr(method, "chat_id", None) in self.blocked:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if isinstance(method, SendMessage) and method.chat_id in self.retry_after:
            # Ограничение Telegram: «слишком много сообщений, подождите»
            raise TelegramRetryAfter(method=method, message="Too Many Requests",
                                     retry_after=self.retry_after.pop(method.chat_id))
        if isinstance(method, SendMessage):
            message = self._message(method.chat_id, method.text)
            reply_to = method.reply_parameters.message_id if method.reply_parameters else None
            self.calls.append(Sent("send", method.chat_id, method.text, method.reply_markup,
                                   message.message_id, reply_to=reply_to))
            return message
        if isinstance(method, SetMessageReaction):
            self.calls.append(Sent("reaction", method.chat_id, method.reaction[0].emoji,
                                   message_id=method.message_id))
            return True
        if isinstance(method, EditMessageText):
            self.calls.append(Sent("edit", method.chat_id, method.text, method.reply_markup, method.message_id))
            return True
        if isinstance(method, AnswerCallbackQuery):
            self.calls.append(Sent("answer", text=method.text or "", alert=method.show_alert))
            return True
        if isinstance(method, SendDocument):
            self.calls.append(Sent("document", method.chat_id, method.document.filename, method=method))
            return self._message(method.chat_id)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id=method.file_id,
                        file_path=f"documents/{method.file_id}")
        raise NotImplementedError(f"FakeTelegram не умеет {type(method).__name__}")

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.files[url.rsplit("/", 1)[-1]]

    async def close(self) -> None:
        pass


# ---------- Имитация ИИ ----------

class FakeAI(Provider):
    """ИИ, который отвечает заранее заданными текстами и запоминает, что у него спросили."""
    name = "fake"

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.requests: list[list[dict]] = []
        self.fail = False      # True — «ИИ недоступен»
        self.delay = 0.0       # задержка ответа (имитация медленной модели)

    async def complete(self, messages: list[dict]) -> Completion:
        self.requests.append(messages)
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ProviderError("fake: сервис недоступен")
        text = self.replies.pop(0) if self.replies else "Ответ по базе."
        return Completion(text, prompt_tokens=100, completion_tokens=20)


# ---------- Помощник для тестов ----------

class Consultant:
    """Бот в тестовом окружении: пишем ему сообщения, жмём кнопки и смотрим, что он ответил."""

    def __init__(self, bot: Bot, telegram: FakeTelegram) -> None:
        self.bot = bot
        self.telegram = telegram
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(1)

    @staticmethod
    def _user(user_id: int) -> User:
        names = {OPERATOR: "Иван", OPERATOR2: "Пётр"}
        return User(id=user_id, is_bot=False, first_name=names.get(user_id, "Анна"))

    async def _feed(self, update: Update) -> list[Sent]:
        """Отдаёт событие диспетчеру и возвращает всё, что бот сделал в ответ.

        «Печатает…» (typing) отправляется в фоне, поэтому его в ответ не включаем.
        """
        start = len(self.telegram.calls)
        await DISPATCHER.feed_update(self.bot, update)
        return [call for call in self.telegram.calls[start:] if call.kind != "typing"]

    async def send(self, user_id: int, text: str | None = None, chat_type: str = "private",
                   reply_to: int | None = None, **message_fields) -> list[Sent]:
        """Пользователь пишет сообщение (или присылает файл). В группе (chat_type="supergroup")
        сообщение приходит в чат операторов OPS; reply_to — ответ на сообщение бота с этим id."""
        chat_id = user_id if chat_type == "private" else OPS
        chat = Chat(id=chat_id, type=chat_type)
        if reply_to is not None:
            message_fields["reply_to_message"] = Message(
                message_id=reply_to, date=datetime.now(), chat=chat,
                from_user=User(id=1, is_bot=True, first_name="Гараж 18"), text="…",
            )
        message = Message(message_id=next(self._message_ids), date=datetime.now(),
                          chat=chat, from_user=self._user(user_id), text=text, **message_fields)
        return await self._feed(Update(update_id=next(self._update_ids), message=message))

    async def press(self, user_id: int, data: str, message_id: int = 999, chat_id: int | None = None) -> list[Sent]:
        """Пользователь нажимает inline-кнопку с callback_data = data
        (chat_id=OPS — кнопку в чате операторов)."""
        chat_id = user_id if chat_id is None else chat_id
        message = Message(message_id=message_id, date=datetime.now(),
                          chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"), text="…")
        query = CallbackQuery(id=str(next(self._update_ids)), from_user=self._user(user_id),
                              chat_instance="test", data=data, message=message)
        return await self._feed(Update(update_id=next(self._update_ids), callback_query=query))

    async def upload(self, user_id: int, file_name: str, data: bytes) -> list[Sent]:
        """Пользователь присылает боту файл."""
        file_id = f"file{next(self._message_ids)}"
        self.telegram.files[file_id] = data
        document = Document(file_id=file_id, file_unique_id=file_id, file_name=file_name, file_size=len(data))
        return await self.send(user_id, document=document)

    async def agree(self, user_id: int = CLIENT) -> None:
        await self.press(user_id, "consent:agree")

    async def ask(self, text: str, user_id: int = CLIENT) -> Sent:
        """Вопрос от клиента; возвращает ответ бота (последнее отправленное сообщение)."""
        calls = await self.send(user_id, text)
        return [call for call in calls if call.kind == "send"][-1]

    def db(self, sql: str, *args) -> list[tuple]:
        """Прямой запрос к временной базе теста — проверить, что записалось."""
        with sqlite3.connect(config.db_path) as connection:
            return connection.execute(sql, args).fetchall()


def texts(calls: list[Sent], kind: str | None = None, chat_id: int | None = None) -> list[str]:
    """Тексты действий бота, при желании — только нужного вида и чата."""
    return [call.text for call in calls
            if (kind is None or call.kind == kind) and (chat_id is None or call.chat_id == chat_id)]


# ---------- Фикстуры ----------

@pytest.fixture
def settings(tmp_path):
    """Своя временная база, своя копия базы знаний и возможность поменять настройки
    на время теста: settings(ai_provider="yandex", yandex_api_key="test")."""
    original = {field.name: getattr(config, field.name) for field in fields(config)}

    def change(**values) -> None:
        for name, value in values.items():
            # Config «заморожен» (frozen=True), чтобы бот случайно его не менял.
            # В тестах меняем осознанно и в конце теста возвращаем как было.
            object.__setattr__(config, name, value)

    knowledge_dir = tmp_path / "knowledge"
    shutil.copytree(ROOT / "knowledge", knowledge_dir)
    change(db_path=str(tmp_path / "test.db"), knowledge_dir=str(knowledge_dir))
    yield change
    change(**original)


@pytest.fixture
async def consultant(settings):
    """Готовый бот: пустая база, демо-режим ИИ, имитация Telegram."""
    await init_db()
    DISPATCHER.fsm.storage = MemoryStorage()
    handoff.client_rate.reset()   # счётчик темпа живёт в памяти — у каждого теста свой
    handoff.client_rate.clock = time.monotonic
    set_provider(None)   # создастся заново по настройке AI_PROVIDER (в тестах — demo)
    telegram = FakeTelegram()
    bot = Bot(TEST_ENV["BOT_TOKEN"], session=telegram,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    yield Consultant(bot, telegram)
    set_provider(None)


@pytest.fixture
def operators(settings):
    """Включает передачу оператору: вопросы уходят в группу OPS."""
    settings(operators_chat_id=OPS)


@pytest.fixture
def fake_ai(consultant):
    """Вместо демо-режима — FakeAI: ответ задаём сами, fake_ai.replies = ["..."]."""
    fake = FakeAI()
    set_provider(fake)
    return fake
