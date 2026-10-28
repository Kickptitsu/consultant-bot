"""Запросы для учёта вопросов и расхода токенов: лимиты и статистика для /admin."""

from database.db import connect

ANONYMIZED = "[удалено]"
# user_id обезличенных вопросов: вопрос больше не связан ни с каким пользователем
ANONYMOUS_USER_ID = 0


async def log_question(
    user_id: int, text: str, status: str, provider: str,
    prompt_tokens: int = 0, completion_tokens: int = 0,
) -> None:
    async with connect() as db:
        await db.execute(
            "INSERT INTO questions (user_id, text, status, provider, prompt_tokens, completion_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, text, status, provider, prompt_tokens, completion_tokens),
        )
        await db.commit()


# Начало периода для SQLite: days=1 — с полуночи сегодня, days=7 — с полуночи 6 дней назад
def _since(days: int) -> str:
    return f"-{days - 1} days"


async def user_questions_today(user_id: int) -> int:
    """Сколько вопросов клиент задал сегодня — для дневного лимита."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM questions WHERE user_id = ? AND created_at >= date('now', 'localtime')",
            (user_id,),
        )
        (count,) = await cursor.fetchone()
        return count


async def tokens_today() -> int:
    """Сколько токенов ИИ потрачено сегодня на всех клиентов."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM questions "
            "WHERE created_at >= date('now', 'localtime')"
        )
        (tokens,) = await cursor.fetchone()
        return tokens


async def summary(days: int) -> dict:
    """Статистика за период: сколько вопросов каждого статуса, клиентов и токенов."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) AS n, SUM(prompt_tokens + completion_tokens) AS tokens FROM questions "
            "WHERE created_at >= date('now', 'localtime', ?) GROUP BY status",
            (_since(days),),
        )
        rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM questions "
            "WHERE created_at >= date('now', 'localtime', ?) AND user_id != ?",
            (_since(days), ANONYMOUS_USER_ID),
        )
        (clients,) = await cursor.fetchone()
    return {
        "statuses": {row["status"]: row["n"] for row in rows},
        "tokens": sum(row["tokens"] or 0 for row in rows),
        "clients": clients,
    }


async def tokens_by_day(days: int) -> list[tuple[str, int, int]]:
    """Расход токенов по дням, от новых к старым: (дата, токены, вопросов)."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT date(created_at) AS day, SUM(prompt_tokens + completion_tokens) AS tokens, COUNT(*) AS n "
            "FROM questions WHERE created_at >= date('now', 'localtime', ?) GROUP BY day ORDER BY day DESC",
            (_since(days),),
        )
        return [(row["day"], row["tokens"], row["n"]) for row in await cursor.fetchall()]


async def unanswered(days: int) -> list[tuple[str, str]]:
    """Вопросы, на которые в базе знаний не нашлось ответа: (текст, когда), от новых к старым.
    Обезличенные (старше 30 дней или удалённые клиентом) — без текста, их не показываем."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT text, created_at FROM questions WHERE status = 'no_answer' AND text != ? "
            "AND created_at >= date('now', 'localtime', ?) ORDER BY id DESC LIMIT 500",
            (ANONYMIZED, _since(days)),
        )
        return [(row["text"], row["created_at"]) for row in await cursor.fetchall()]


async def anonymize_older_than(days: int) -> int:
    """Тексты вопросов старше days дней стираем, а статистику (сколько, чем закончились,
    сколько токенов) оставляем. Возвращает число обезличенных вопросов."""
    async with connect() as db:
        cursor = await db.execute(
            "UPDATE questions SET text = ?, user_id = ? "
            "WHERE created_at < datetime('now', 'localtime', ?) AND user_id != ?",
            (ANONYMIZED, ANONYMOUS_USER_ID, f"-{days} days", ANONYMOUS_USER_ID),
        )
        await db.commit()
        return cursor.rowcount
