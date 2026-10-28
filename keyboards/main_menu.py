"""Главное меню — постоянные кнопки внизу экрана (reply-клавиатура)."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from config import config

# Тексты кнопок вынесены в константы: по ним же хендлеры узнают нажатие
BTN_NEW_TOPIC = "🔄 Новый вопрос"
BTN_ABOUT = "ℹ️ Как я работаю"
BTN_OPERATOR = "🧑‍🔧 Позвать оператора"


def main_menu_kb() -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=BTN_NEW_TOPIC), KeyboardButton(text=BTN_ABOUT)]]
    if config.operators_chat_id is not None:
        # Без чата операторов звать некого — кнопку не показываем
        keyboard.append([KeyboardButton(text=BTN_OPERATOR)])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,  # компактные кнопки вместо огромных
        input_field_placeholder="Напишите вопрос об автосервисе",
    )
