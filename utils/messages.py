"""Помощник для «перерисовки» сообщения с inline-кнопками."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)

# Функция без аргументов, которая собирает экран: возвращает (текст, кнопки)
Render = Callable[[], Awaitable[tuple[str, InlineKeyboardMarkup | None]]]

# Ключ — (id чата, id сообщения). Блокировка не даёт двум перерисовкам
# одного сообщения идти одновременно и обгонять друг друга.
_locks: defaultdict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)
# Номер последнего запроса на перерисовку каждого сообщения
_latest: dict[tuple[int, int], int] = {}


def fixed(text: str, markup: InlineKeyboardMarkup | None = None) -> Render:
    """Render для экрана, который не зависит от базы: show(callback, fixed("Готово"))."""
    async def render() -> tuple[str, InlineKeyboardMarkup | None]:
        return text, markup
    return render


async def show(callback: CallbackQuery, render: Render) -> None:
    """Меняет текст и кнопки сообщения, на кнопку которого нажал пользователь.

    Так каталог и корзина живут в одном сообщении, а не засоряют чат.

    Экран собирается функцией render уже в момент перерисовки, поэтому
    показываются самые свежие данные. Если пользователь быстро жмёт ➕,
    промежуточные перерисовки пропускаются — рисуется только последняя.
    """
    message = callback.message
    if not isinstance(message, Message):
        # Сообщение слишком старое, Telegram его не отдаёт — шлём новое
        text, markup = await render()
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)
        return
    await _in_turn((message.chat.id, message.message_id), lambda: _edit(callback, message, render))


async def edit(bot: Bot, chat_id: int, message_id: int, render: Render) -> None:
    """Перерисовывает сообщение бота по его номеру — когда его меняет не нажатие на его же
    кнопку (например, карточку запроса в чате операторов меняет отмена от клиента).
    Очередь общая с show(): правки одного сообщения не обгоняют друг друга."""
    await _in_turn((chat_id, message_id), lambda: _edit_by_id(bot, chat_id, message_id, render))


async def _in_turn(key: tuple[int, int], do_edit: Callable[[], Awaitable[None]]) -> None:
    ticket = _latest.get(key, 0) + 1
    _latest[key] = ticket

    async with _locks[key]:
        if _latest[key] != ticket:
            # Пока ждали очереди, пришло нажатие новее — оно и перерисует
            return
        await do_edit()

    # Больше никто не ждёт — убираем записи, чтобы словари не росли бесконечно
    if _latest.get(key) == ticket:
        del _latest[key]
        _locks.pop(key, None)


async def _edit(callback: CallbackQuery, message: Message, render: Render) -> None:
    text, markup = await render()
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramRetryAfter as error:
        # Слишком частые правки — Telegram просит подождать. Ждём и рисуем заново
        await asyncio.sleep(error.retry_after)
        text, markup = await render()
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        # Содержимое не изменилось — это не ошибка
        if "message is not modified" in str(error):
            return
        # Сообщение нельзя отредактировать (например, удалено) — шлём новое
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


async def _edit_by_id(bot: Bot, chat_id: int, message_id: int, render: Render) -> None:
    text, markup = await render()
    try:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
        except TelegramRetryAfter as error:
            await asyncio.sleep(error.retry_after)
            text, markup = await render()
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            logger.warning("Не удалось изменить сообщение %s в чате %s: %s", message_id, chat_id, error)
    except TelegramAPIError as error:
        # Например, бота удалили из группы. Это не должно ломать ответ клиенту
        logger.warning("Не удалось изменить сообщение %s в чате %s: %s", message_id, chat_id, error)
