"""Администратор: база знаний (/kb) и её обновление файлом без перезапуска бота."""

import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from config import KNOWLEDGE_MAX_FILE_KB, KNOWLEDGE_MAX_TOKENS
from filters.admin import IsAdmin
from keyboards.admin import kb_delete_kb, kb_list_kb
from keyboards.callbacks import KbCB
from services import knowledge
from services.knowledge import KnowledgeError
from utils.formatting import format_number, plural
from utils.messages import fixed, show

logger = logging.getLogger(__name__)

router = Router(name="admin")
# Все хендлеры этого роутера — только для администраторов и только в личном чате
router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(IsAdmin())

KB_HELP = (
    "Чтобы <b>обновить</b> файл, скачайте его кнопкой 📥, поправьте и пришлите боту обратно "
    "с тем же именем. Чтобы <b>добавить</b> раздел — пришлите файл с новым именем.\n"
    f"Формат: .md или .txt в кодировке UTF-8, до {KNOWLEDGE_MAX_FILE_KB} КБ. "
    "Перезапускать бота не нужно — ИИ сразу отвечает по новой версии."
)


def _summary(base: knowledge.Knowledge) -> str:
    return (
        f"{plural(len(base.files), 'файл', 'файла', 'файлов')}, "
        f"около {format_number(base.tokens)} из {format_number(KNOWLEDGE_MAX_TOKENS)} токенов"
    )


async def render_kb() -> tuple[str, InlineKeyboardMarkup]:
    base = knowledge.get()
    lines = [f"📚 <b>База знаний</b>: {_summary(base)}", ""]
    if base.files:
        lines += [
            f"• <code>{escape(file.name)}</code> — {file.size / 1024:.1f} КБ, ~{format_number(file.tokens)} токенов"
            for file in base.files
        ]
    else:
        lines.append("⚠️ База пуста — ИИ на все вопросы будет отвечать «не знаю».")
    lines += ["", KB_HELP]
    return "\n".join(lines), kb_list_kb(base)


@router.message(Command("kb"))
async def cmd_kb(message: Message) -> None:
    text, markup = await render_kb()
    await message.answer(text, reply_markup=markup)


@router.callback_query(KbCB.filter(F.action == "list"))
async def kb_list(callback: CallbackQuery) -> None:
    await callback.answer()
    await show(callback, render_kb)


@router.callback_query(KbCB.filter(F.action == "get"))
async def kb_get(callback: CallbackQuery, callback_data: KbCB) -> None:
    try:
        data = knowledge.read_file(callback_data.name)
    except KnowledgeError as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer_document(BufferedInputFile(data, filename=callback_data.name))


@router.callback_query(KbCB.filter(F.action == "del_ask"))
async def kb_delete_ask(callback: CallbackQuery, callback_data: KbCB) -> None:
    await callback.answer()
    text = (
        f"🗑 Удалить <code>{escape(callback_data.name)}</code>?\n\n"
        "ИИ перестанет знать то, что написано в этом файле. Совет: сначала скачайте его — "
        "чтобы можно было вернуть."
    )
    await show(callback, fixed(text, kb_delete_kb(callback_data.name)))


@router.callback_query(KbCB.filter(F.action == "del"))
async def kb_delete(callback: CallbackQuery, callback_data: KbCB) -> None:
    deleted = knowledge.delete_file(callback_data.name)
    if deleted:
        logger.info("Админ %s удалил файл базы знаний %s", callback.from_user.id, callback_data.name)
    await callback.answer("Файл удалён" if deleted else "Файла уже нет")
    await show(callback, render_kb)


@router.message(F.document)
async def upload(message: Message) -> None:
    """Админ прислал файл — кладём его в базу знаний."""
    document = message.document
    name = document.file_name or ""
    if not name.lower().endswith(knowledge.EXTENSIONS):
        await message.answer("❌ Для базы знаний нужен файл .md или .txt.")
        return
    if (document.file_size or 0) > KNOWLEDGE_MAX_FILE_KB * 1024:
        await message.answer(f"❌ Файл больше {KNOWLEDGE_MAX_FILE_KB} КБ — разделите его на несколько.")
        return

    downloaded = await message.bot.download(document)
    try:
        created, base = knowledge.save_file(name, downloaded.read())
    except KnowledgeError as error:
        await message.answer(f"❌ {error}")
        return

    logger.info("Админ %s %s файл базы знаний %s", message.from_user.id,
                "добавил" if created else "обновил", name)
    action = "добавлен" if created else "обновлён"
    text = (
        f"✅ Файл <code>{escape(name)}</code> {action}. В базе {_summary(base)}.\n"
        "ИИ уже отвечает по новой версии."
    )
    if base.tokens > KNOWLEDGE_MAX_TOKENS * 0.8:
        text += (
            "\n\n⚠️ База почти достигла предела. Каждый вопрос отправляет ИИ всю базу, "
            "поэтому большая база — это дорого. Если разделов станет ещё больше, стоит перейти "
            "на поиск по фрагментам (RAG) — см. README."
        )
    await message.answer(text)
