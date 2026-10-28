"""Вопросы клиентов: любое текстовое сообщение в личном чате с ботом."""

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

import texts
from config import MESSAGE_MAX_CHARS, config
from database import dialogs
from database import privacy as privacy_db
from keyboards.operators import cancel_kb
from keyboards.privacy import consent_kb
from services import consultant, handoff
from utils.locks import user_lock

logger = logging.getLogger(__name__)

router = Router(name="chat")
router.message.filter(F.chat.type == "private")

# Когда ИИ не справился, вопрос сам уходит операторам — с таким текстом для клиента
HANDOFF_TEXTS = {
    "no_answer": texts.HANDOFF_NO_ANSWER,
    "error": texts.HANDOFF_ERROR,
    "token_limit": texts.TOKEN_LIMIT_HANDOFF,
}


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    # Опечатка в команде — не вопрос для ИИ, токены на неё не тратим
    await message.answer(texts.UNKNOWN_COMMAND)


@router.message(F.text)
async def question(message: Message) -> None:
    user_id = message.from_user.id
    if not await privacy_db.has_consent(user_id, texts.consent_version()):
        await message.answer(texts.CONSENT_NEEDED)
        await message.answer(texts.consent_text(), reply_markup=consent_kb())
        return

    if len(message.text) > MESSAGE_MAX_CHARS:
        await message.answer(texts.TOO_LONG)
        return

    async with user_lock(user_id):
        dialog = await dialogs.get_open(user_id) if handoff.enabled() else None
        if dialog is not None:
            # Клиент на связи с оператором — ИИ молчит, сообщение получают операторы
            result = await handoff.forward_from_client(message.bot, dialog["id"], message.from_user, message.text)
            if result == "too_fast":
                await message.answer(texts.TOO_FAST)
            elif result == "failed":
                await message.answer(texts.FORWARD_FAILED)
            return

        # «Печатает…» в шапке чата, пока ИИ думает (обновляется каждые 5 секунд)
        async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
            reply = await consultant.answer(user_id, message.text)

        if reply.status == "token_limit" and consultant.token_alert_due():
            await notify_admins(message.bot, texts.TOKEN_LIMIT_ALERT)
        if reply.status == "user_limit" and handoff.enabled():
            await message.answer(texts.USER_LIMIT + texts.USER_LIMIT_OPERATOR)
            return
        if reply.status in HANDOFF_TEXTS and handoff.enabled():
            result = await handoff.start(message.bot, message.from_user, reply.status)
            if result.status == "started":
                await message.answer(HANDOFF_TEXTS[reply.status], reply_markup=cancel_kb(result.dialog_id))
                return
            # Операторам написать не получилось — честно отвечаем, как без них
        await message.answer(reply.text)


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError as error:
            # Админ не нажимал Start — бот не может написать первым
            logger.warning("Не удалось предупредить админа %s: %s", admin_id, error)


@router.message()
async def not_text(message: Message) -> None:
    await message.answer(texts.NOT_TEXT)
