"""Передача разговора живому оператору (администратору автосервиса).

Как это устроено:
1. Клиент нажал «🧑‍🔧 Позвать оператора» или ИИ не нашёл ответа — бот открывает запрос
   (строка в таблице dialogs) и присылает в группу операторов карточку: кто, почему
   и последние сообщения.
2. Кто из операторов первым нажал «✋ Взять» (или сразу ответил), тот и ведёт диалог.
3. Оператор отвечает реплаем (Reply) на карточку или на сообщение клиента — бот
   пересылает ответ клиенту.
4. Пока запрос открыт, ИИ молчит: сообщения клиента уходят операторам.
5. Оператор нажимает «✅ Закрыть» — снова отвечает ИИ.

Операторы — все участники группы OPERATORS_CHAT_ID. Отдельный список не нужен:
добавили человека в группу — он оператор, удалили — больше нет.

Защита группы от спама: не больше USER_DAILY_HANDOFFS запросов на клиента в день,
не больше OPERATOR_MESSAGES_PER_MINUTE его сообщений в минуту, а оператор может
заблокировать клиента на BLOCK_HOURS часов.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramMigrateToChat, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, Message, ReplyParameters, User

import texts
from config import (
    OPERATOR_CARD_HOURS,
    OPERATOR_CARD_MESSAGES,
    OPERATOR_MESSAGES_PER_MINUTE,
    USER_DAILY_HANDOFFS,
    config,
)
from database import dialogs, history
from database.stats import ANONYMOUS_USER_ID
from keyboards.operators import card_kb
from utils.messages import edit
from utils.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

# Сколько сообщений клиента в минуту пересылаем операторам
client_rate = RateLimiter(OPERATOR_MESSAGES_PER_MINUTE, 60)
# Telegram попросил подождать дольше — не держим клиента, честно говорим «не получилось»
MAX_RETRY_WAIT_SECONDS = 30

REASONS = {
    "button": "клиент позвал оператора",
    "no_answer": "ИИ не нашёл ответа в справочнике",
    "error": "ИИ не смог ответить (ошибка сервиса)",
    "token_limit": "исчерпан дневной лимит ИИ",
}
CLOSED_BY = {
    "client": "клиент отменил запрос",
    "error": "не удалось доставить карточку",
    "privacy": "клиент удалил свои данные",
    "expired": "закрыт автоматически",
}
ROLE_ICONS = {"user": "👤", "assistant": "🤖", "operator": "👨‍🔧"}
MESSAGE_CHARS = 300    # длинные сообщения в карточке обрезаем: у Telegram предел 4096 символов
SCRUBBED = "🗑 Сообщение скрыто: клиент удалил свои данные."


def enabled() -> bool:
    return config.operators_chat_id is not None


def person_name(user: User) -> str:
    name = " ".join(part for part in (user.first_name, user.last_name) if part) or "Без имени"
    return f"{name} (@{user.username})" if user.username else name


def _short(text: str) -> str:
    return text if len(text) <= MESSAGE_CHARS else text[:MESSAGE_CHARS - 1] + "…"


def format_context(messages: list[tuple[str, str]]) -> str:
    """Последние сообщения для карточки — уже в HTML (тексты экранированы)."""
    return "\n".join(f"{ROLE_ICONS.get(role, '•')} {escape(_short(text))}" for role, text in messages)


def format_until(value: str) -> str:
    """'2026-10-29 13:30:00' -> '29.10 13:30'."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d.%m %H:%M")


async def send_to_operators(bot: Bot, text: str, **kwargs) -> Message:
    """Сообщение в группу операторов. Если Telegram просит подождать (в группу можно
    ~20 сообщений в минуту), ждём и пробуем ещё раз — иначе карточка клиента потеряется."""
    try:
        return await bot.send_message(config.operators_chat_id, text, **kwargs)
    except TelegramRetryAfter as error:
        if error.retry_after > MAX_RETRY_WAIT_SECONDS:
            raise
        logger.warning("Чат операторов: Telegram просит подождать %s с — ждём и повторяем", error.retry_after)
        await asyncio.sleep(error.retry_after)
        return await bot.send_message(config.operators_chat_id, text, **kwargs)


def log_delivery_error(error: TelegramAPIError) -> None:
    """Почему не получилось написать в группу операторов — понятным языком."""
    if isinstance(error, TelegramMigrateToChat):
        # Группа стала супергруппой (например, включили темы) — у неё новый ID
        logger.error(
            "Чат операторов сменил ID на %s — укажите его в .env в OPERATORS_CHAT_ID и перезапустите бота",
            error.migrate_to_chat_id,
        )
    else:
        logger.error(
            "Не удалось написать в чат операторов: %s. Проверьте OPERATORS_CHAT_ID и что бот добавлен в группу",
            error,
        )


# ---------- Карточка запроса ----------

async def card(dialog_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    """Карточка собирается из базы в момент показа — в ней всегда свежий статус."""
    dialog = await dialogs.get(dialog_id)
    number = f"Запрос #{dialog_id}"
    if dialog["user_id"] == ANONYMOUS_USER_ID:
        return f"🗑 <b>{number}</b> — клиент удалил свои данные, переписка скрыта.", None

    status = dialog["status"]
    block = None
    if status == "waiting":
        head = f"🆕 <b>{number}</b> — ждёт оператора"
    elif status == "active":
        head = f"💬 <b>{number}</b> — ведёт {escape(dialog['operator_name'])}"
    elif dialog["closed_by"] == "blocked":
        block = await dialogs.active_block(dialog_id)
        until = f" до {format_until(block['until'])}" if block else ", блокировка снята"
        head = f"🚫 <b>{number}</b> — клиент заблокирован{until} ({escape(dialog['closer_name'] or '')})"
    elif dialog["closed_by"] == "operator":
        head = f"✅ <b>{number}</b> — закрыт ({escape(dialog['closer_name'] or '')})"
    else:
        head = f"✅ <b>{number}</b> — закрыт: {CLOSED_BY.get(dialog['closed_by'], 'закрыт')}"

    lines = [
        head,
        "",
        f"<b>Клиент:</b> {escape(dialog['client_name'])}",
        f"<b>Причина:</b> {REASONS.get(dialog['reason'], dialog['reason'])}",
        "",
        "<b>Последние сообщения:</b>",
        dialog["context"] or "<i>клиент ещё ничего не спрашивал</i>",
    ]
    if status != "closed":
        lines += ["", "<i>Ответьте на это сообщение (Reply) — ответ получит клиент.</i>"]
    return "\n".join(lines), card_kb(dialog_id, status, can_unblock=block is not None)


async def refresh_card(bot: Bot, dialog_id: int) -> None:
    """Перерисовать карточку, когда статус поменял не оператор кнопкой на ней."""
    message_id = await dialogs.card_message_id(dialog_id, config.operators_chat_id)
    if message_id is not None:
        await edit(bot, config.operators_chat_id, message_id, lambda: card(dialog_id))


# ---------- Клиент → операторы ----------

@dataclass(frozen=True)
class Handoff:
    # started / waiting, active (уже был открыт) / blocked (оператор заблокировал) /
    # limit (дневной предел запросов) / failed (группа недоступна)
    status: str
    dialog_id: int | None = None


async def start(bot: Bot, user: User, reason: str) -> Handoff:
    """Открывает запрос и присылает карточку операторам."""
    current = await dialogs.get_open(user.id)
    if current is not None:
        return Handoff(current["status"], current["id"])
    if await dialogs.blocked_until(user.id):
        return Handoff("blocked")
    if await dialogs.count_today(user.id) >= USER_DAILY_HANDOFFS:
        logger.info("Клиент %s: дневной предел запросов к оператору", user.id)
        return Handoff("limit")

    recent = await history.recent_messages(user.id, OPERATOR_CARD_MESSAGES, OPERATOR_CARD_HOURS)
    dialog_id = await dialogs.create(user.id, person_name(user), reason, format_context(recent))
    if dialog_id is None:
        # Параллельно (двойное нажатие) запрос уже открыл другой обработчик
        current = await dialogs.get_open(user.id)
        return Handoff(current["status"], current["id"]) if current else Handoff("failed")

    text, markup = await card(dialog_id)
    try:
        sent = await send_to_operators(bot, text, reply_markup=markup)
    except TelegramAPIError as error:
        log_delivery_error(error)
        await dialogs.close(dialog_id, "error")
        return Handoff("failed")
    await dialogs.add_message(dialog_id, config.operators_chat_id, sent.message_id, "card")
    logger.info("Запрос #%s передан операторам (%s)", dialog_id, reason)
    return Handoff("started", dialog_id)


async def forward_from_client(bot: Bot, dialog_id: int, user: User, text: str) -> str:
    """Сообщение клиента во время открытого запроса — операторам, ответом на карточку.

    Возвращает sent (передано), too_fast (слишком часто — предупредить клиента),
    too_fast_quiet (слишком часто, предупреждали только что) или failed.
    """
    if not client_rate.hit(user.id):
        return "too_fast" if client_rate.should_warn(user.id) else "too_fast_quiet"
    await history.add_messages(user.id, [("user", text)])
    card_id = await dialogs.card_message_id(dialog_id, config.operators_chat_id)
    reply = ReplyParameters(message_id=card_id, allow_sending_without_reply=True) if card_id else None
    try:
        sent = await send_to_operators(
            bot,
            f"👤 <b>{escape(person_name(user))}</b> · запрос #{dialog_id}:\n{escape(text)}",
            reply_parameters=reply,
        )
    except TelegramAPIError as error:
        log_delivery_error(error)
        return "failed"
    await dialogs.add_message(dialog_id, config.operators_chat_id, sent.message_id, "client")
    return "sent"


# ---------- Операторы → клиент ----------

async def notify_client(bot: Bot, user_id: int, text: str, **kwargs) -> bool:
    """Сообщение клиенту. Ошибка (клиент заблокировал бота) не должна ломать работу оператора."""
    try:
        await bot.send_message(user_id, text, **kwargs)
        return True
    except TelegramAPIError as error:
        logger.warning("Не удалось написать клиенту %s: %s", user_id, error)
        return False


async def send_answer(bot: Bot, user_id: int, message: Message) -> bool:
    """Ответ оператора клиенту. html_text сохраняет жирный и курсив оператора."""
    if not await notify_client(bot, user_id, texts.OPERATOR_PREFIX + message.html_text):
        return False
    await history.add_messages(user_id, [("operator", message.text)])
    return True


# ---------- Удаление данных клиента ----------

async def scrub(bot: Bot, refs: list[tuple[int, int, str]]) -> None:
    """Клиент удалил свои данные (/privacy) — прячем его сообщения в чате операторов:
    карточку перерисовываем обезличенной, пересланные сообщения заменяем заглушкой.
    Сообщения самих операторов бот изменить не может — это сделают люди."""
    for chat_id, message_id, kind in refs:
        if kind == "card":
            dialog = await dialogs.by_message(chat_id, message_id)
            if dialog is not None:
                await edit(bot, chat_id, message_id, lambda dialog_id=dialog["id"]: card(dialog_id))
        else:
            await edit(bot, chat_id, message_id, _scrubbed)


async def _scrubbed() -> tuple[str, None]:
    return SCRUBBED, None
