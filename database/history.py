"""Запросы для переписки: сохранить сообщения, достать контекст для ИИ, новая тема."""

from database.db import connect


async def add_messages(user_id: int, messages: list[tuple[str, str]]) -> None:
    """Сохраняет реплики (роль, текст) одной транзакцией — вопрос и ответ вместе."""
    async with connect() as db:
        await db.executemany(
            "INSERT INTO messages (user_id, role, text) VALUES (?, ?, ?)",
            [(user_id, role, text) for role, text in messages],
        )
        await db.commit()


async def recent_context(user_id: int, limit: int, ttl_minutes: int) -> list[tuple[str, str]]:
    """Последние сообщения текущей темы, не старше ttl_minutes, от старых к новым."""
    async with connect() as db:
        cursor = await db.execute(
            """
            SELECT role, text FROM messages
            WHERE user_id = ?
              AND role IN ('user', 'assistant')   -- ответы операторов ИИ не показываем
              AND id > COALESCE((SELECT context_from FROM users WHERE user_id = ?), 0)
              AND created_at >= datetime('now', 'localtime', ?)
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, user_id, f"-{ttl_minutes} minutes", limit),
        )
        rows = await cursor.fetchall()
    return [(row["role"], row["text"]) for row in reversed(rows)]


async def recent_messages(user_id: int, limit: int, hours: int) -> list[tuple[str, str]]:
    """Последние сообщения за hours часов, от старых к новым, — для карточки оператора.
    В отличие от recent_context, не обрывается на «Новом вопросе»: оператору полезно всё."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT role, text FROM messages WHERE user_id = ? AND created_at >= datetime('now', 'localtime', ?) "
            "ORDER BY id DESC LIMIT ?",
            (user_id, f"-{hours} hours", limit),
        )
        rows = await cursor.fetchall()
    return [(row["role"], row["text"]) for row in reversed(rows)]


async def new_topic(user_id: int) -> None:
    """«Новый вопрос»: ИИ перестаёт видеть сообщения, написанные до этого момента."""
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO users (user_id, context_from)
            VALUES (?, (SELECT COALESCE(MAX(id), 0) FROM messages WHERE user_id = ?))
            ON CONFLICT (user_id) DO UPDATE SET context_from = excluded.context_from
            """,
            (user_id, user_id),
        )
        await db.commit()


async def delete_older_than(days: int) -> int:
    """Удаляет переписку старше days дней. Возвращает, сколько сообщений удалено."""
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM messages WHERE created_at < datetime('now', 'localtime', ?)", (f"-{days} days",)
        )
        await db.commit()
        return cursor.rowcount
