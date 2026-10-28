"""Клиент: кнопка «🧑‍🔧 Позвать оператора» и отмена запроса, пока его никто не взял."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

import texts
from database import dialogs
from database import privacy as privacy_db
from keyboards.callbacks import HandoffCB
from keyboards.main_menu import BTN_OPERATOR
from keyboards.operators import cancel_kb
from keyboards.privacy import consent_kb
from services import handoff
from utils.locks import user_lock
from utils.messages import fixed, show

router = Router(name="handoff")
router.message.filter(F.chat.type == "private")

# Что ответить клиенту, если новый запрос не открылся. О блокировке клиенту прямо
# не говорим — просто «позвать нельзя, позвоните»: так спорить не с чем
NOT_STARTED = {
    "waiting": texts.HANDOFF_WAITING,
    "active": texts.HANDOFF_ACTIVE,
    "blocked": texts.NO_OPERATORS,
    "limit": texts.HANDOFF_LIMIT,
}


@router.message(F.text == BTN_OPERATOR)
async def call_operator(message: Message) -> None:
    if not handoff.enabled():
        await message.answer(texts.NO_OPERATORS)
        return
    user = message.from_user
    # Оператор увидит переписку — без согласия на обработку данных её передавать нельзя
    if not await privacy_db.has_consent(user.id, texts.consent_version()):
        await message.answer(texts.CONSENT_NEEDED)
        await message.answer(texts.consent_text(), reply_markup=consent_kb())
        return

    async with user_lock(user.id):
        result = await handoff.start(message.bot, user, "button")
    if result.status == "started":
        await message.answer(texts.HANDOFF_STARTED, reply_markup=cancel_kb(result.dialog_id))
    elif result.status in NOT_STARTED:
        await message.answer(NOT_STARTED[result.status])
    else:
        await message.answer(texts.HANDOFF_FAILED)


@router.callback_query(HandoffCB.filter(F.action == "cancel"))
async def cancel(callback: CallbackQuery, callback_data: HandoffCB) -> None:
    dialog = await dialogs.get(callback_data.dialog_id)
    if dialog is None or dialog["user_id"] != callback.from_user.id:
        await callback.answer("Кнопка устарела")
        return
    if not await dialogs.close(callback_data.dialog_id, "client", only_waiting=True):
        dialog = await dialogs.get(callback_data.dialog_id)
        text = texts.HANDOFF_TOO_LATE if dialog["status"] == "active" else "Этот запрос уже закрыт."
        await callback.answer(text, show_alert=True)
        return
    await callback.answer()
    await show(callback, fixed(texts.HANDOFF_CANCELLED))
    await handoff.refresh_card(callback.bot, callback_data.dialog_id)
