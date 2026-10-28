"""Путь вопроса клиента от сообщения до ответа.

1. «Привет» / «спасибо» — отвечаем сами, ИИ не нужен.
2. Лимиты: клиент исчерпал свои вопросы на сегодня или бот — дневной запас токенов.
3. Явная попытка сломать правила (prompt injection) — вежливый отказ, ИИ не вызываем.
4. ИИ получает правила, всю базу знаний, последние сообщения диалога и вопрос.
5. Ответ проверяется (ai/guard.py): нет ли в нём кусков промта и выдуманных цен.
6. Всё записывается: переписка — для контекста, вопрос — для статистики и учёта токенов.
"""

import logging
from dataclasses import dataclass
from datetime import date

import texts
from ai import guard, prompt
from ai.providers import ProviderError, get_provider
from config import DAILY_TOKEN_LIMIT, HISTORY_MESSAGES, HISTORY_TTL_MINUTES, USER_DAILY_QUESTIONS
from database import history, stats
from services import knowledge
from utils.formatting import ai_to_html

logger = logging.getLogger(__name__)

# Что бот пишет клиенту вместо служебного ответа модели
TEMPLATES = {
    "no_answer": texts.NO_ANSWER,
    "off_topic": texts.OFF_TOPIC,
    "blocked": texts.BLOCKED,
    "error": texts.AI_ERROR,
}


@dataclass(frozen=True)
class Reply:
    # small_talk / answered / no_answer / off_topic / blocked / error / user_limit / token_limit
    status: str
    text: str     # готовый текст для Telegram (HTML)


# В какой день админам уже сообщили, что дневной лимит токенов исчерпан
_token_alert_day: date | None = None


def token_alert_due() -> bool:
    """Предупредить админов об исчерпанном лимите — один раз в день, а не на каждый вопрос."""
    global _token_alert_day
    if _token_alert_day == date.today():
        return False
    _token_alert_day = date.today()
    return True


async def answer(user_id: int, question: str) -> Reply:
    kind = guard.small_talk(question)
    if kind:
        text = texts.GREETING if kind == "greeting" else texts.THANKS
        await history.add_messages(user_id, [("user", question), ("assistant", text)])
        return Reply("small_talk", text)

    # Лимиты записываем в статистику (provider="limit"), но в историю диалога не пишем
    if await stats.user_questions_today(user_id) >= USER_DAILY_QUESTIONS:
        await stats.log_question(user_id, question, "user_limit", provider="limit")
        return Reply("user_limit", texts.USER_LIMIT)
    if await stats.tokens_today() >= DAILY_TOKEN_LIMIT:
        logger.warning("Дневной лимит токенов (%s) исчерпан — ИИ не вызываем", DAILY_TOKEN_LIMIT)
        await stats.log_question(user_id, question, "token_limit", provider="limit")
        return Reply("token_limit", texts.TOKEN_LIMIT)

    if guard.is_injection(question):
        # В историю не пишем: иначе эта фраза попадёт ИИ в контекст следующих вопросов
        logger.info("Пользователь %s: похоже на prompt injection, ИИ не вызываем", user_id)
        await stats.log_question(user_id, question, "blocked", provider="filter")
        return Reply("blocked", texts.BLOCKED)

    base = knowledge.get()
    context = await history.recent_context(user_id, HISTORY_MESSAGES, HISTORY_TTL_MINUTES)
    messages = prompt.build_messages(base.text, context, question)
    provider = get_provider()
    try:
        completion = await provider.complete(messages)
    except ProviderError as error:
        logger.error("ИИ не ответил: %s", error)
        await stats.log_question(user_id, question, "error", provider.name)
        return Reply("error", texts.AI_ERROR)

    verdict = guard.check_answer(completion.text, base.text)
    if verdict.reason:
        logger.warning("Пользователь %s: ответ ИИ отклонён — %s", user_id, verdict.reason)
    await stats.log_question(
        user_id, question, verdict.status, provider.name,
        completion.prompt_tokens, completion.completion_tokens,
    )

    if verdict.status == "answered":
        await history.add_messages(user_id, [("user", question), ("assistant", verdict.text)])
        return Reply("answered", texts.AI_ANSWER_PREFIX + ai_to_html(verdict.text))
    if verdict.status != "blocked":
        # «Не знаю» и «не по теме» сохраняем: на уточнение ИИ поймёт, о чём был разговор
        await history.add_messages(user_id, [("user", question), ("assistant", TEMPLATES[verdict.status])])
    return Reply(verdict.status, TEMPLATES[verdict.status])
