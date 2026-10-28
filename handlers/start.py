"""/start, согласие на обработку данных и кнопки главного меню."""

from html import escape

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

import texts
from database import dialogs, history
from database import privacy as privacy_db
from keyboards.callbacks import ConsentCB
from keyboards.main_menu import BTN_ABOUT, BTN_NEW_TOPIC, main_menu_kb
from keyboards.privacy import consent_kb
from services import handoff
from utils.messages import fixed, show

router = Router(name="start")
# В группе операторов бот не должен отвечать на эти команды и кнопки
router.message.filter(F.chat.type == "private")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    first_name = message.from_user.first_name
    name = f", {escape(first_name)}" if first_name else ""
    await message.answer(texts.WELCOME.format(name=name), reply_markup=main_menu_kb())
    if not await privacy_db.has_consent(message.from_user.id, texts.consent_version()):
        await message.answer(texts.consent_text(), reply_markup=consent_kb())


@router.callback_query(ConsentCB.filter(F.action == "agree"))
async def consent_agree(callback: CallbackQuery) -> None:
    # Двойное нажатие безопасно: give_consent не создаёт вторую запись
    await privacy_db.give_consent(callback.from_user.id, texts.consent_version())
    await callback.answer()
    await show(callback, fixed(texts.CONSENT_THANKS))


@router.callback_query(ConsentCB.filter(F.action == "decline"))
async def consent_decline(callback: CallbackQuery) -> None:
    await callback.answer()
    await show(callback, fixed(texts.CONSENT_DECLINED))


@router.message(F.text == BTN_NEW_TOPIC)
async def new_topic(message: Message) -> None:
    dialog = await dialogs.get_open(message.from_user.id) if handoff.enabled() else None
    if dialog is not None:
        # Пока открыт запрос к оператору, «новая тема» ничего не меняет — объясняем почему
        text = texts.HANDOFF_ACTIVE if dialog["status"] == "active" else texts.HANDOFF_WAITING
        await message.answer(text, reply_markup=main_menu_kb())
        return
    await history.new_topic(message.from_user.id)
    await message.answer(texts.NEW_TOPIC, reply_markup=main_menu_kb())


@router.message(F.text == BTN_ABOUT)
async def about(message: Message) -> None:
    text = texts.ABOUT + (texts.ABOUT_OPERATOR if handoff.enabled() else "")
    await message.answer(text, reply_markup=main_menu_kb())
