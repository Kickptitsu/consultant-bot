"""Кнопки администратора: база знаний (/kb) и статистика (/admin)."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.callbacks import AdminCB, KbCB, fits
from services.knowledge import Knowledge


def kb_list_kb(base: Knowledge) -> InlineKeyboardMarkup:
    """У каждого файла две кнопки: скачать (чтобы поправить и прислать обратно) и удалить."""
    builder = InlineKeyboardBuilder()
    rows = []
    for file in base.files:
        get, delete = KbCB(action="get", name=file.name), KbCB(action="del_ask", name=file.name)
        if not (fits(get) and fits(delete)):
            continue   # имя слишком длинное для кнопки — файл всё равно виден в списке
        builder.button(text=f"📥 {file.name}", callback_data=get)
        builder.button(text="🗑", callback_data=delete)
        rows.append(2)
    builder.adjust(*rows)
    return builder.as_markup()


PERIODS = {1: "Сегодня", 7: "7 дней", 30: "30 дней"}


def stats_kb(days: int) -> InlineKeyboardMarkup:
    """Период (текущий отмечен точкой) и переход к вопросам без ответа."""
    builder = InlineKeyboardBuilder()
    for period, title in PERIODS.items():
        builder.button(text=f"• {title} •" if period == days else title,
                       callback_data=AdminCB(action="stats", days=period))
    builder.button(text="🤔 Вопросы без ответа", callback_data=AdminCB(action="unanswered", days=days))
    builder.button(text="🔄 Обновить", callback_data=AdminCB(action="stats", days=days))
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def unanswered_kb(days: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for period, title in PERIODS.items():
        builder.button(text=f"• {title} •" if period == days else title,
                       callback_data=AdminCB(action="unanswered", days=period))
    builder.button(text="⬅️ К статистике", callback_data=AdminCB(action="stats", days=days))
    builder.adjust(3, 1)
    return builder.as_markup()


def kb_delete_kb(name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, удалить", callback_data=KbCB(action="del", name=name))
    builder.button(text="⬅️ Назад", callback_data=KbCB(action="list"))
    builder.adjust(2)
    return builder.as_markup()
