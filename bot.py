"""Точка входа. Запуск: python bot.py"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat

from ai.providers import close_provider, get_provider
from config import config
from database.db import init_db
from handlers import routers
from services import cleanup, knowledge


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    # Библиотека openai пишет в лог каждый HTTP-запрос — оставляем только предупреждения
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    await init_db()

    base = knowledge.get()
    logging.info("База знаний: %s файлов, около %s токенов", len(base.files), base.tokens)
    if not base.files:
        logging.warning("Папка базы знаний пуста: %s", config.knowledge_dir)

    # Никогда не печатаем сам ключ — только кто отвечает клиентам
    provider = get_provider()
    if config.ai_provider == "demo":
        logging.info("ИИ: ДЕМО-РЕЖИМ — ключ API не нужен, бот показывает разделы базы знаний")
    else:
        logging.info("ИИ: %s, модель %s", provider.name, provider.model)

    if not config.privacy_policy_url:
        logging.warning(
            "PRIVACY_POLICY_URL не задан: вместо ссылки бот будет присылать файл PRIVACY.md. "
            "Заполните шаблон, опубликуйте политику и укажите ссылку в .env"
        )

    # parse_mode=HTML: в текстах можно использовать <b>жирный</b>, <i>курсив</i> и т.п.
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_routers(*routers)

    # Команды в меню «/» рядом с полем ввода
    commands = [
        BotCommand(command="start", description="Начать сначала"),
        BotCommand(command="privacy", description="Мои данные и согласие"),
    ]
    # Только в личных чатах: в группе операторов /start и /privacy ни к чему
    await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
    # Администраторам — ещё /kb и /admin (видно только в их личных чатах с ботом)
    for admin_id in config.admin_ids:
        try:
            await bot.set_my_commands(
                [*commands, BotCommand(command="admin", description="Статистика"),
                 BotCommand(command="kb", description="База знаний")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except TelegramAPIError as error:
            # Обычно значит, что админ ещё ни разу не открывал бота
            logging.warning("Не удалось задать команды админу %s: %s", admin_id, error)

    # Проверяем чат операторов сразу, а не когда клиент впервые позовёт оператора
    if config.operators_chat_id is None:
        logging.warning(
            "OPERATORS_CHAT_ID не задан: передача вопросов оператору выключена. "
            "Добавьте бота в группу операторов и напишите там /chat_id"
        )
    else:
        try:
            chat = await bot.get_chat(config.operators_chat_id)
            logging.info("Чат операторов: «%s»", chat.title)
        except TelegramAPIError as error:
            logging.error(
                "Чат операторов недоступен (%s): добавьте бота в группу и проверьте OPERATORS_CHAT_ID", error
            )

    # Пропускаем сообщения, которые пришли, пока бот был выключен
    await bot.delete_webhook(drop_pending_updates=True)

    logging.info("Бот запущен. Остановить: Ctrl+C")
    cleaner = asyncio.create_task(cleanup.watch())
    try:
        await dp.start_polling(bot)
    finally:
        cleaner.cancel()
        await close_provider()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен")
