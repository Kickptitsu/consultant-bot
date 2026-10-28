"""Кнопки для персональных данных: согласие и /privacy."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import config
from keyboards.callbacks import POLICY, ConsentCB, PrivacyCB


def _policy_button(builder: InlineKeyboardBuilder) -> None:
    """Кнопка «Политика»: ссылка, если она задана в .env, иначе бот пришлёт файл PRIVACY.md."""
    text = "📄 Политика обработки данных"
    if config.privacy_policy_url:
        builder.button(text=text, url=config.privacy_policy_url)
    else:
        builder.button(text=text, callback_data=POLICY)


def consent_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _policy_button(builder)
    builder.button(text="✅ Согласен", callback_data=ConsentCB(action="agree"))
    builder.button(text="❌ Отказаться", callback_data=ConsentCB(action="decline"))
    builder.adjust(1, 2)
    return builder.as_markup()


def privacy_kb(can_delete: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    _policy_button(builder)
    if can_delete:
        builder.button(text="🗑 Отозвать согласие и удалить данные", callback_data=PrivacyCB(action="delete_ask"))
    builder.adjust(1)
    return builder.as_markup()


def delete_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, удалить мои данные", callback_data=PrivacyCB(action="delete_confirm"))
    builder.button(text="⬅️ Нет, назад", callback_data=PrivacyCB(action="back"))
    builder.adjust(1)
    return builder.as_markup()
