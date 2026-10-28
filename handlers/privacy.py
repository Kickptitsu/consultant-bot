"""/privacy: какие данные о пользователе хранятся, отзыв согласия и удаление данных."""

import os
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

import texts
from config import BASE_DIR, HISTORY_RETENTION_DAYS
from database import privacy as privacy_db
from keyboards.callbacks import POLICY, PrivacyCB
from keyboards.privacy import delete_confirm_kb, privacy_kb
from services import handoff
from utils.formatting import format_datetime
from utils.locks import user_lock
from utils.messages import fixed, show

router = Router(name="privacy")
router.message.filter(F.chat.type == "private")

POLICY_FILE = os.path.join(BASE_DIR, "PRIVACY.md")


async def render_privacy(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    consent = await privacy_db.get_last_consent(user_id)
    data = await privacy_db.get_user_data(user_id)

    lines = ["🔐 <b>Ваши данные</b>", "", f"Telegram ID: <code>{user_id}</code>"]
    if consent is None:
        lines.append("Согласие на обработку данных: не давали")
    elif consent["revoked_at"]:
        lines.append(f"Согласие на обработку данных: ⛔ отозвано {format_datetime(consent['revoked_at'])}")
    else:
        lines.append(
            f"Согласие на обработку данных: ✅ дано {format_datetime(consent['accepted_at'])} "
            f"(версия текста {consent['version']})"
        )

    lines.append("")
    if data["messages"] or data["questions"] or data["dialogs"]:
        lines += [
            "<b>Что мы храним:</b>",
            f"• Сообщений в переписке с ботом: {data['messages']} "
            f"(хранятся не дольше {HISTORY_RETENTION_DAYS} дней)",
            f"• Ваших вопросов в статистике: {data['questions']}",
        ]
        if data["dialogs"]:
            lines.append(f"• Обращений к администратору: {data['dialogs']}")
    else:
        lines.append("Переписки с вами мы не храним.")
    if consent is not None:
        # Честно говорим и про запись о согласии: её храним, чтобы подтвердить соблюдение закона
        lines.append(
            "\n<i>Также хранится запись о том, когда вы давали или отзывали согласие "
            "и на какую версию текста, — она нужна, чтобы подтвердить соблюдение закона.</i>"
        )

    has_active_consent = consent is not None and consent["revoked_at"] is None
    can_delete = has_active_consent or bool(data["messages"] or data["questions"] or data["dialogs"])
    return "\n".join(lines), privacy_kb(can_delete)


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    text, markup = await render_privacy(message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == POLICY)
async def send_policy(callback: CallbackQuery) -> None:
    """Ссылка на политику не задана в .env — присылаем сам файл PRIVACY.md.

    Памятку для заказчика (она в HTML-комментарии <!-- ... -->) клиенту не показываем.
    """
    await callback.answer()
    with open(POLICY_FILE, encoding="utf-8") as file:
        policy = re.sub(r"<!--.*?-->", "", file.read(), flags=re.DOTALL).strip()
    await callback.bot.send_document(
        callback.from_user.id,
        BufferedInputFile(policy.encode("utf-8"), filename="Политика обработки персональных данных.md"),
        caption="📄 Политика обработки персональных данных",
    )


@router.callback_query(PrivacyCB.filter(F.action == "back"))
async def back(callback: CallbackQuery) -> None:
    await callback.answer()
    await show(callback, lambda: render_privacy(callback.from_user.id))


@router.callback_query(PrivacyCB.filter(F.action == "delete_ask"))
async def delete_ask(callback: CallbackQuery) -> None:
    await callback.answer()
    text = (
        "🗑 <b>Отозвать согласие и удалить данные?</b>\n\n"
        "• Согласие будет отозвано.\n"
        "• Переписка с ботом будет удалена — ИИ забудет, о чём вы говорили.\n"
        "• Ваши вопросы останутся в статистике автосервиса, но без текста и без привязки к вам.\n"
        "• Сообщения, которые бот передал администраторам, будут скрыты.\n"
        "• Чтобы снова задать вопрос, нужно будет заново дать согласие."
    )
    await show(callback, fixed(text, delete_confirm_kb()))


@router.callback_query(PrivacyCB.filter(F.action == "delete_confirm"))
async def delete_confirm(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    # В очереди с вопросами: если ИИ как раз отвечает этому клиенту, удалим после ответа,
    # иначе ответ запишется в историю уже после удаления
    async with user_lock(user_id):
        result = await privacy_db.delete_user_data(user_id)
    await callback.answer()
    await show(callback, fixed(texts.PRIVACY_DONE))
    # Долгое — после ответа: прячем пересланные операторам сообщения в их группе
    await handoff.scrub(callback.bot, result["operator_messages"])
