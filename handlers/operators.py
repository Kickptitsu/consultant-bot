"""Группа операторов: «✋ Взять», «✅ Закрыть» и ответы клиентам реплаем.

Боту в группе не нужны права администратора: даже в режиме приватности (по умолчанию)
Telegram присылает ему команды и ответы (Reply) на его собственные сообщения — этого хватает.
"""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji

import texts
from config import BLOCK_HOURS, config
from database import dialogs, history
from database.stats import ANONYMOUS_USER_ID
from keyboards.callbacks import OperatorCB
from keyboards.operators import block_confirm_kb
from services import handoff
from utils.messages import show

logger = logging.getLogger(__name__)

router = Router(name="operators")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


def in_operators_chat(chat_id: int) -> bool:
    return config.operators_chat_id is not None and chat_id == config.operators_chat_id


def _busy_text(dialog, operator_id: int) -> str:
    """Почему не получилось взять или закрыть запрос — для всплывающего окна."""
    if dialog is None or dialog["status"] == "closed" or dialog["user_id"] == ANONYMOUS_USER_ID:
        return "Этот запрос уже закрыт."
    if dialog["operator_id"] == operator_id:
        return "Вы уже ведёте этот диалог."
    return f"Диалог уже ведёт {dialog['operator_name']}."


@router.message(Command("chat_id"))
async def cmd_chat_id(message: Message) -> None:
    """Подсказка при настройке: какой ID у этой группы (его вписывают в OPERATORS_CHAT_ID)."""
    if message.from_user is None or message.from_user.id not in config.admin_ids:
        return
    if in_operators_chat(message.chat.id):
        await message.reply("✅ Это чат операторов — сюда приходят запросы клиентов.")
    else:
        await message.reply(
            f"ID этого чата: <code>{message.chat.id}</code>\n"
            "Чтобы сюда приходили вопросы клиентов, впишите его в .env → OPERATORS_CHAT_ID "
            "и перезапустите бота."
        )


@router.callback_query(OperatorCB.filter(F.action == "take"))
async def take(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    if not isinstance(callback.message, Message) or not in_operators_chat(callback.message.chat.id):
        await callback.answer()
        return
    operator = callback.from_user
    if not await dialogs.take(callback_data.dialog_id, operator.id, handoff.person_name(operator)):
        await callback.answer(_busy_text(await dialogs.get(callback_data.dialog_id), operator.id), show_alert=True)
        return
    await callback.answer("Диалог ваш. Отвечайте клиенту реплаем на карточку или его сообщения.")
    await show(callback, lambda: handoff.card(callback_data.dialog_id))
    dialog = await dialogs.get(callback_data.dialog_id)
    await handoff.notify_client(callback.bot, dialog["user_id"], texts.OPERATOR_JOINED)


@router.callback_query(OperatorCB.filter(F.action == "close"))
async def close(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    if not isinstance(callback.message, Message) or not in_operators_chat(callback.message.chat.id):
        await callback.answer()
        return
    operator = callback.from_user
    closed = await dialogs.close_by_operator(callback_data.dialog_id, operator.id, handoff.person_name(operator))
    dialog = await dialogs.get(callback_data.dialog_id)
    if not closed:
        text = _busy_text(dialog, operator.id)
        if dialog is not None and dialog["status"] == "active":
            text = f"Закрыть может только тот, кто ведёт диалог: {dialog['operator_name']}."
        await callback.answer(text, show_alert=True)
        return
    await callback.answer("Диалог закрыт")
    await show(callback, lambda: handoff.card(callback_data.dialog_id))
    # Снова отвечает ИИ, и разговор с оператором ему в контекст не попадёт
    await history.new_topic(dialog["user_id"])
    await handoff.notify_client(callback.bot, dialog["user_id"], texts.DIALOG_CLOSED)


def _may_close(dialog, operator_id: int) -> str | None:
    """Может ли оператор закрыть (или заблокировать) запрос. None — может, иначе — почему нет."""
    if dialog is None or dialog["status"] == "closed" or dialog["user_id"] == ANONYMOUS_USER_ID:
        return "Этот запрос уже закрыт."
    if dialog["status"] == "active" and dialog["operator_id"] != operator_id:
        return f"Это может только тот, кто ведёт диалог: {dialog['operator_name']}."
    return None


@router.callback_query(OperatorCB.filter(F.action == "block_ask"))
async def block_ask(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    if not isinstance(callback.message, Message) or not in_operators_chat(callback.message.chat.id):
        await callback.answer()
        return
    refusal = _may_close(await dialogs.get(callback_data.dialog_id), callback.from_user.id)
    if refusal:
        await callback.answer(refusal, show_alert=True)
        return
    await callback.answer()

    async def confirm() -> tuple[str, object]:
        text, _ = await handoff.card(callback_data.dialog_id)
        return (
            f"{text}\n\n<b>Заблокировать клиента на {BLOCK_HOURS} ч?</b> Запрос закроется, клиент не сможет "
            "звать оператора и писать в эту группу. ИИ будет отвечать ему как обычно."
        ), block_confirm_kb(callback_data.dialog_id)

    await show(callback, confirm)


@router.callback_query(OperatorCB.filter(F.action == "back"))
async def block_cancel(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    if not isinstance(callback.message, Message) or not in_operators_chat(callback.message.chat.id):
        await callback.answer()
        return
    await callback.answer()
    await show(callback, lambda: handoff.card(callback_data.dialog_id))


@router.callback_query(OperatorCB.filter(F.action == "block"))
async def block(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    if not isinstance(callback.message, Message) or not in_operators_chat(callback.message.chat.id):
        await callback.answer()
        return
    operator = callback.from_user
    if not await dialogs.block(callback_data.dialog_id, operator.id, handoff.person_name(operator), BLOCK_HOURS):
        await callback.answer(_may_close(await dialogs.get(callback_data.dialog_id), operator.id)
                              or "Не получилось заблокировать.", show_alert=True)
        return
    await callback.answer("Клиент заблокирован")
    await show(callback, lambda: handoff.card(callback_data.dialog_id))
    dialog = await dialogs.get(callback_data.dialog_id)
    logger.info("Запрос #%s: клиент заблокирован на %s ч", dialog["id"], BLOCK_HOURS)
    # Клиенту — как при обычном закрытии: о блокировке не сообщаем, чтобы не провоцировать
    await history.new_topic(dialog["user_id"])
    await handoff.notify_client(callback.bot, dialog["user_id"], texts.DIALOG_CLOSED)


@router.callback_query(OperatorCB.filter(F.action == "unblock"))
async def unblock(callback: CallbackQuery, callback_data: OperatorCB) -> None:
    if not isinstance(callback.message, Message) or not in_operators_chat(callback.message.chat.id):
        await callback.answer()
        return
    unblocked = await dialogs.unblock(callback_data.dialog_id)
    await callback.answer("Блокировка снята" if unblocked else "Блокировка уже снята или истекла")
    await show(callback, lambda: handoff.card(callback_data.dialog_id))


@router.message(F.reply_to_message)
async def operator_reply(message: Message) -> None:
    """Оператор ответил (Reply) на карточку или на сообщение клиента — пересылаем клиенту."""
    if not in_operators_chat(message.chat.id) or message.from_user is None or message.from_user.is_bot:
        return
    dialog = await dialogs.by_message(message.chat.id, message.reply_to_message.message_id)
    if dialog is None:
        return   # ответ на что-то, что не относится к запросам клиентов
    if not message.text:
        await message.reply("Клиенту можно отправить только текст.")
        return

    operator = message.from_user
    if dialog["status"] == "waiting" and await dialogs.take(dialog["id"], operator.id, handoff.person_name(operator)):
        # Ответить сразу, не нажимая «Взять», — тоже значит взять диалог
        await handoff.refresh_card(message.bot, dialog["id"])
        await handoff.notify_client(message.bot, dialog["user_id"], texts.OPERATOR_JOINED)
    dialog = await dialogs.get(dialog["id"])

    if dialog["status"] != "active" or dialog["user_id"] == ANONYMOUS_USER_ID:
        await message.reply(f"Запрос #{dialog['id']} закрыт — сообщение не отправлено.")
        return
    if dialog["operator_id"] != operator.id:
        await message.reply(f"Этот диалог ведёт {dialog['operator_name']} — ответить клиенту может только этот оператор.")
        return

    if not await handoff.send_answer(message.bot, dialog["user_id"], message):
        await message.reply("⚠️ Не доставлено: клиент, скорее всего, заблокировал бота.")
        return
    try:
        # 👍 на сообщении оператора — «доставлено клиенту»
        await message.react([ReactionTypeEmoji(emoji="👍")])
    except TelegramAPIError as error:
        logger.info("Не удалось поставить реакцию: %s", error)
