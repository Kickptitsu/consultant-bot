"""Мелкие помощники для красивого вывода."""

import re
from datetime import datetime
from html import escape


def plural(n: int, one: str, few: str, many: str) -> str:
    """Склонение по числу: plural(3, 'файл', 'файла', 'файлов') -> '3 файла'."""
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def format_number(n: int) -> str:
    """12500 -> '12 500'."""
    return f"{n:,}".replace(",", " ")


def format_datetime(value: str) -> str:
    """'2026-10-25 17:40:12' (так SQLite хранит дату) -> '25.10.2026 17:40'."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")


def ai_to_html(text: str) -> str:
    """Ответ ИИ -> текст для Telegram.

    Сначала экранируем < > &: бот отправляет сообщения в режиме HTML, и без этого
    ответ с «<b>» или «<script>» сломал бы разметку. Потом превращаем привычную
    моделям разметку markdown в HTML: **жирный** и списки «- пункт».
    """
    text = escape(text.strip())
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)
    return text
