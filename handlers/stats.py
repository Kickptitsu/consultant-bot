"""Администратор: /admin — статистика, расход токенов и вопросы без ответа."""

from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import DAILY_TOKEN_LIMIT
from database import dialogs, stats
from filters.admin import IsAdmin
from keyboards.admin import PERIODS, stats_kb, unanswered_kb
from keyboards.callbacks import AdminCB
from services import handoff, insights
from utils.formatting import format_number, plural
from utils.messages import show

router = Router(name="stats")
router.message.filter(F.chat.type == "private", IsAdmin())
router.callback_query.filter(IsAdmin())

# Статусы вопросов в том порядке и с теми словами, как их видит админ
STATUS_LINES = [
    ("answered", "✅ ИИ ответил"),
    ("no_answer", "🤔 Нет ответа в базе"),
    ("off_topic", "🚫 Не по теме"),
    ("blocked", "🛡 Попытки сломать правила"),
    ("error", "⚠️ Ошибки ИИ"),
    ("user_limit", "⛔ Клиент исчерпал вопросы на день"),
    ("token_limit", "⛔ Исчерпан дневной лимит токенов"),
]
REASON_LINES = [
    ("no_answer", "нет ответа в базе"),
    ("button", "клиент позвал сам"),
    ("error", "ошибка ИИ"),
    ("token_limit", "лимит токенов"),
]
LAST_QUESTIONS = 10


def _period(days: int) -> str:
    return "сегодня" if days == 1 else f"за {PERIODS.get(days, f'{days} дней').lower()}"


def _percent(part: int, total: int) -> str:
    return f"{round(part * 100 / total)}%" if total else "0%"


async def render_stats(days: int) -> tuple[str, InlineKeyboardMarkup]:
    summary = await stats.summary(days)
    statuses = summary["statuses"]
    total = sum(statuses.values())

    lines = [f"📊 <b>Статистика {_period(days)}</b>", ""]
    if total:
        lines.append(f"<b>Вопросов:</b> {total} от {plural(summary['clients'], 'клиента', 'клиентов', 'клиентов')}")
        for status, title in STATUS_LINES:
            if statuses.get(status):
                share = f" ({_percent(statuses[status], total)})" if status == "answered" else ""
                lines.append(f"{title} — {statuses[status]}{share}")
    else:
        lines.append("Вопросов пока не было.")

    if handoff.enabled():
        handoffs = await dialogs.summary(days)
        lines += ["", f"<b>Передано оператору:</b> {sum(handoffs['reasons'].values())}"]
        for reason, title in REASON_LINES:
            if handoffs["reasons"].get(reason):
                lines.append(f"• {title} — {handoffs['reasons'][reason]}")
        lines.append(f"Сейчас: ждут оператора — {handoffs['waiting']}, на связи с оператором — {handoffs['active']}")

    today = await stats.tokens_today()
    lines += [
        "",
        "<b>Токены ИИ</b>",
        f"Сегодня: {format_number(today)} из {format_number(DAILY_TOKEN_LIMIT)} ({_percent(today, DAILY_TOKEN_LIMIT)})",
    ]
    if days > 1:
        lines.append(f"{_period(days).capitalize()}: {format_number(summary['tokens'])}")
        for day, tokens, count in await stats.tokens_by_day(days):
            date = datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m")
            lines.append(f"• {date} — {format_number(tokens)} ({plural(count, 'вопрос', 'вопроса', 'вопросов')})")
    lines.append("<i>Токены — единицы, в которых сервис ИИ считает длину запроса и ответа (и цену).</i>")
    return "\n".join(lines), stats_kb(days)


async def render_unanswered(days: int) -> tuple[str, InlineKeyboardMarkup]:
    questions = await stats.unanswered(days)
    if not questions:
        text = f"🤔 <b>Вопросы без ответа {_period(days)}</b>\n\nВсе вопросы нашли ответ в базе знаний 🎉"
        return text, unanswered_kb(days)

    lines = [f"🤔 <b>Вопросы без ответа {_period(days)}: {len(questions)}</b>", ""]
    topics = insights.topics([question for question, _ in questions])
    if topics:
        lines.append("<b>Чаще всего спрашивают про:</b>")
        lines += [f"• {escape(topic.word)} — {topic.count}" for topic in topics]
        lines.append("")
    lines += [
        "💡 <b>Что сделать:</b> допишите в базу знаний ответы на эти темы: /kb → скачайте файл, "
        "добавьте раздел и пришлите обратно. Если такой услуги нет, так и напишите "
        "(«Покраску не делаем») — тогда ИИ ответит сам, а не позовёт оператора.",
        "",
        "<b>Последние вопросы:</b>",
    ]
    for question, created_at in questions[:LAST_QUESTIONS]:
        when = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m %H:%M")
        short = question if len(question) <= 120 else question[:119] + "…"
        lines.append(f"{when} — {escape(short)}")
    return "\n".join(lines), unanswered_kb(days)


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    text, markup = await render_stats(1)
    await message.answer(text, reply_markup=markup)


@router.callback_query(AdminCB.filter(F.action == "stats"))
async def stats_screen(callback: CallbackQuery, callback_data: AdminCB) -> None:
    await callback.answer()
    await show(callback, lambda: render_stats(callback_data.days))


@router.callback_query(AdminCB.filter(F.action == "unanswered"))
async def unanswered_screen(callback: CallbackQuery, callback_data: AdminCB) -> None:
    await callback.answer()
    await show(callback, lambda: render_unanswered(callback_data.days))
