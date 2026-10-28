"""Кнопки передачи оператору: у карточки в чате операторов и у клиента."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BLOCK_HOURS
from keyboards.callbacks import HandoffCB, OperatorCB


def card_kb(dialog_id: int, status: str, can_unblock: bool = False) -> InlineKeyboardMarkup | None:
    builder = InlineKeyboardBuilder()
    if status == "waiting":
        builder.button(text="✋ Взять", callback_data=OperatorCB(action="take", dialog_id=dialog_id))
        builder.button(text="✅ Закрыть", callback_data=OperatorCB(action="close", dialog_id=dialog_id))
    elif status == "active":
        builder.button(text="✅ Закрыть диалог", callback_data=OperatorCB(action="close", dialog_id=dialog_id))
    if status in ("waiting", "active"):
        builder.button(text="🚫 Заблокировать", callback_data=OperatorCB(action="block_ask", dialog_id=dialog_id))
        builder.adjust(2 if status == "waiting" else 1, 1)
    elif can_unblock:
        builder.button(text="🔓 Разблокировать", callback_data=OperatorCB(action="unblock", dialog_id=dialog_id))
    else:
        return None   # закрытый запрос — без кнопок
    return builder.as_markup()


def block_confirm_kb(dialog_id: int) -> InlineKeyboardMarkup:
    """«Точно заблокировать?» — случайное нажатие отрезало бы клиента от операторов на сутки."""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"🚫 Да, заблокировать на {BLOCK_HOURS} ч",
                   callback_data=OperatorCB(action="block", dialog_id=dialog_id))
    builder.button(text="⬅️ Нет", callback_data=OperatorCB(action="back", dialog_id=dialog_id))
    builder.adjust(1)
    return builder.as_markup()


def cancel_kb(dialog_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="↩️ Отменить, вернуться к ИИ", callback_data=HandoffCB(action="cancel", dialog_id=dialog_id))
    return builder.as_markup()
