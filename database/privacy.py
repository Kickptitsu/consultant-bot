"""Запросы для персональных данных: согласия, удаление переписки, журнал."""

from aiosqlite import Connection, Row

from database.db import connect
from database.stats import ANONYMIZED, ANONYMOUS_USER_ID

# Подписи событий журнала — для людей и для выгрузки в CSV
EVENT_LABELS = {
    "consent_given": "Согласие дано",
    "consent_revoked": "Согласие отозвано",
    "data_deleted": "Переписка удалена, вопросы обезличены",
    "retention_cleanup": "Автоудаление старой переписки",
}


async def _log(db: Connection, user_id: int, event: str, actor: str, details: str = "") -> None:
    """Запись в журнал внутри уже открытого соединения (в той же транзакции)."""
    await db.execute(
        "INSERT INTO privacy_log (user_id, event, details, actor) VALUES (?, ?, ?, ?)",
        (user_id, event, details, actor),
    )


async def log_event(user_id: int, event: str, actor: str, details: str = "") -> None:
    async with connect() as db:
        await _log(db, user_id, event, actor, details)
        await db.commit()


# ---------- Согласие ----------

async def get_last_consent(user_id: int) -> Row | None:
    """Последнее согласие пользователя (действующее или отозванное)."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT * FROM consents WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
        )
        return await cursor.fetchone()


async def has_consent(user_id: int, version: str) -> bool:
    """Есть ли действующее согласие на ТЕКУЩУЮ версию текста.

    Если текст согласия поменяли (и версию вместе с ним), спросим заново.
    """
    async with connect() as db:
        cursor = await db.execute(
            "SELECT 1 FROM consents WHERE user_id = ? AND version = ? AND revoked_at IS NULL",
            (user_id, version),
        )
        return await cursor.fetchone() is not None


async def give_consent(user_id: int, version: str) -> bool:
    """Сохраняет согласие. Повторное нажатие «Согласен» не создаёт дубль.

    Возвращает True, если согласие записано сейчас.
    """
    async with connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO consents (user_id, version)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM consents WHERE user_id = ? AND version = ? AND revoked_at IS NULL
            )
            """,
            (user_id, version, user_id, version),
        )
        created = cursor.rowcount == 1
        if created:
            await _log(db, user_id, "consent_given", "user", f"версия текста: {version}")
        await db.commit()
        return created


# ---------- Что хранится о пользователе ----------

async def get_user_data(user_id: int) -> dict:
    async with connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,))
        (messages,) = await cursor.fetchone()
        cursor = await db.execute("SELECT COUNT(*) FROM questions WHERE user_id = ?", (user_id,))
        (questions,) = await cursor.fetchone()
        cursor = await db.execute("SELECT COUNT(*) FROM dialogs WHERE user_id = ?", (user_id,))
        (dialogs,) = await cursor.fetchone()
    return {"messages": messages, "questions": questions, "dialogs": dialogs}


async def delete_user_data(user_id: int) -> dict:
    """Отзывает согласие, удаляет переписку и обезличивает вопросы — одной транзакцией.

    Вопросы не удаляем, а обезличиваем: текст стираем, user_id обнуляем — статистика
    («сколько вопросов было за день») остаётся, но с человеком больше не связана.
    Так же — с запросами к операторам. В ответе — operator_messages: сообщения бота
    в чате операторов, которые после этого нужно скрыть (handoff.scrub).
    """
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "UPDATE consents SET revoked_at = datetime('now', 'localtime') "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (user_id,),
            )
            if cursor.rowcount:
                await _log(db, user_id, "consent_revoked", "user")
            cursor = await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            messages = cursor.rowcount
            cursor = await db.execute(
                "UPDATE questions SET text = ?, user_id = ? WHERE user_id = ?",
                (ANONYMIZED, ANONYMOUS_USER_ID, user_id),
            )
            questions = cursor.rowcount
            cursor = await db.execute(
                "SELECT chat_id, message_id, kind FROM operator_messages "
                "WHERE dialog_id IN (SELECT id FROM dialogs WHERE user_id = ?)",
                (user_id,),
            )
            operator_messages = [tuple(row) for row in await cursor.fetchall()]
            cursor = await db.execute(
                "UPDATE dialogs SET client_name = ?, context = ?, user_id = ?, "
                "closed_by = CASE WHEN status = 'closed' THEN closed_by ELSE 'privacy' END, "
                "closed_at = COALESCE(closed_at, datetime('now', 'localtime')), status = 'closed' "
                "WHERE user_id = ?",
                (ANONYMIZED, ANONYMIZED, ANONYMOUS_USER_ID, user_id),
            )
            dialogs = cursor.rowcount
            await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await _log(
                db, user_id, "data_deleted", "user",
                f"удалено сообщений — {messages}, обезличено вопросов — {questions}, "
                f"запросов к операторам — {dialogs}",
            )
            await db.commit()
            return {"messages": messages, "questions": questions, "dialogs": dialogs,
                    "operator_messages": operator_messages}
        except Exception:
            await db.rollback()
            raise


async def get_log() -> list[Row]:
    async with connect() as db:
        cursor = await db.execute("SELECT * FROM privacy_log ORDER BY id")
        return await cursor.fetchall()
