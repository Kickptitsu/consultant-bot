"""Ответы «на всё остальное». Этот роутер подключается последним."""

from aiogram import Router
from aiogram.types import CallbackQuery

router = Router(name="common")


@router.callback_query()
async def unknown_callback(callback: CallbackQuery) -> None:
    # Нажатие на кнопку, которую бот уже не знает (например, после обновления),
    # или админскую кнопку у того, кто больше не админ
    await callback.answer("Кнопка устарела")
