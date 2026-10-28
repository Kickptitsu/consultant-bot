"""Сборка всех роутеров в одном месте.

Порядок важен: aiogram проверяет роутеры сверху вниз и отдаёт апдейт первому
подходящему хендлеру:
- start и handoff — кнопки меню («🔄 Новый вопрос», «🧑‍🔧 Позвать оператора»)
  не должны уйти ИИ как вопрос;
- privacy, admin и stats — до chat, чтобы /privacy, /kb и /admin не приняли за неизвестную команду,
  а файл от админа — за «я понимаю только текст»;
- operators — группа операторов: кнопки карточек и ответы клиентам;
- chat (любой текст — вопрос для ИИ) и common (устаревшие кнопки) — последние.
"""

from handlers import admin, chat, common, handoff, operators, privacy, start, stats

routers = [
    start.router,
    handoff.router,
    privacy.router,
    admin.router,
    stats.router,
    operators.router,
    chat.router,
    common.router,
]
