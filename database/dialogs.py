"""Запросы для передачи разговора оператору: открыть, взять, закрыть, найти по сообщению.

Смена статуса — одним UPDATE с условием «из какого статуса можно»: если два оператора
одновременно нажали «Взять», база пропустит только первого (второй получит rowcount = 0).
"""

import sqlite3

from aiosqlite import Row

from database.db import connect
from database.stats import ANONYMIZED, ANONYMOUS_USER_ID

OPEN = ("waiting", "active")


async def create(user_id: int, client_name: str, reason: str, context: str) -> int | None:
    """Открывает запрос. None — у клиента уже есть открытый (его не пустил уникальный индекс)."""
    async with connect() as db:
        try:
            cursor = await db.execute(
                "INSERT INTO dialogs (user_id, client_name, reason, context) VALUES (?, ?, ?, ?)",
                (user_id, client_name, reason, context),
            )
        except sqlite3.IntegrityError:
            return None
        await db.commit()
        return cursor.lastrowid


async def get(dialog_id: int) -> Row | None:
    async with connect() as db:
        cursor = await db.execute("SELECT * FROM dialogs WHERE id = ?", (dialog_id,))
        return await cursor.fetchone()


async def get_open(user_id: int) -> Row | None:
    """Открытый запрос клиента (ждёт оператора или оператор уже ведёт диалог)."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT * FROM dialogs WHERE user_id = ? AND status IN ('waiting', 'active')", (user_id,)
        )
        return await cursor.fetchone()


async def take(dialog_id: int, operator_id: int, operator_name: str) -> bool:
    """Оператор берёт запрос. True — взял он; False — кто-то успел раньше или запрос закрыт."""
    async with connect() as db:
        cursor = await db.execute(
            "UPDATE dialogs SET status = 'active', operator_id = ?, operator_name = ?, "
            "taken_at = datetime('now', 'localtime') WHERE id = ? AND status = 'waiting'",
            (operator_id, operator_name, dialog_id),
        )
        await db.commit()
        return cursor.rowcount == 1


# Закрыть (и заблокировать) может любой оператор, пока запрос никто не взял, а потом —
# только тот, кто ведёт диалог
_OPERATOR_MAY_CLOSE = "id = ? AND (status = 'waiting' OR (status = 'active' AND operator_id = ?))"


async def close_by_operator(dialog_id: int, operator_id: int, operator_name: str) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "UPDATE dialogs SET status = 'closed', closed_by = 'operator', closer_name = ?, "
            f"closed_at = datetime('now', 'localtime') WHERE {_OPERATOR_MAY_CLOSE}",
            (operator_name, dialog_id, operator_id),
        )
        await db.commit()
        return cursor.rowcount == 1


async def block(dialog_id: int, operator_id: int, operator_name: str, hours: int) -> bool:
    """Закрывает запрос и блокирует клиента на hours часов — одной транзакцией."""
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "UPDATE dialogs SET status = 'closed', closed_by = 'blocked', closer_name = ?, "
                f"closed_at = datetime('now', 'localtime') WHERE {_OPERATOR_MAY_CLOSE}",
                (operator_name, dialog_id, operator_id),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                "INSERT INTO blocks (user_id, until, operator_name, dialog_id) "
                "SELECT user_id, datetime('now', 'localtime', ?), ?, id FROM dialogs WHERE id = ? "
                "ON CONFLICT (user_id) DO UPDATE SET until = excluded.until, "
                "operator_name = excluded.operator_name, dialog_id = excluded.dialog_id, "
                "created_at = datetime('now', 'localtime')",
                (f"+{hours} hours", operator_name, dialog_id),
            )
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            raise


async def active_block(dialog_id: int) -> Row | None:
    """Действующая блокировка, поставленная из этого запроса (для карточки)."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT * FROM blocks WHERE dialog_id = ? AND until > datetime('now', 'localtime')", (dialog_id,)
        )
        return await cursor.fetchone()


async def blocked_until(user_id: int) -> str | None:
    """До какого времени клиент заблокирован; None — не заблокирован."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT until FROM blocks WHERE user_id = ? AND until > datetime('now', 'localtime')", (user_id,)
        )
        row = await cursor.fetchone()
        return row["until"] if row else None


async def unblock(dialog_id: int) -> bool:
    async with connect() as db:
        cursor = await db.execute(
            "DELETE FROM blocks WHERE dialog_id = ? AND until > datetime('now', 'localtime')", (dialog_id,)
        )
        await db.commit()
        return cursor.rowcount == 1


async def delete_expired_blocks() -> int:
    async with connect() as db:
        cursor = await db.execute("DELETE FROM blocks WHERE until <= datetime('now', 'localtime')")
        await db.commit()
        return cursor.rowcount


async def count_today(user_id: int) -> int:
    """Сколько запросов к оператору клиент открыл сегодня — и отменённые тоже,
    иначе «позвать → отменить» по кругу засыпал бы группу карточками."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM dialogs WHERE user_id = ? AND created_at >= date('now', 'localtime') "
            "AND closed_by IS NOT 'error'",
            (user_id,),
        )
        (count,) = await cursor.fetchone()
        return count


async def close(dialog_id: int, closed_by: str, only_waiting: bool = False) -> bool:
    """Закрытие не оператором: клиент отменил (только пока никто не взял) или ошибка доставки."""
    statuses = ("waiting",) if only_waiting else OPEN
    async with connect() as db:
        cursor = await db.execute(
            f"UPDATE dialogs SET status = 'closed', closed_by = ?, closed_at = datetime('now', 'localtime') "
            f"WHERE id = ? AND status IN ({', '.join('?' * len(statuses))})",
            (closed_by, dialog_id, *statuses),
        )
        await db.commit()
        return cursor.rowcount == 1


async def add_message(dialog_id: int, chat_id: int, message_id: int, kind: str) -> None:
    """Запоминаем сообщение бота в чате операторов: на него можно ответить реплаем."""
    async with connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO operator_messages (chat_id, message_id, dialog_id, kind) VALUES (?, ?, ?, ?)",
            (chat_id, message_id, dialog_id, kind),
        )
        await db.commit()


async def card_message_id(dialog_id: int, chat_id: int) -> int | None:
    async with connect() as db:
        cursor = await db.execute(
            "SELECT message_id FROM operator_messages WHERE dialog_id = ? AND chat_id = ? AND kind = 'card'",
            (dialog_id, chat_id),
        )
        row = await cursor.fetchone()
        return row["message_id"] if row else None


async def by_message(chat_id: int, message_id: int) -> Row | None:
    """Запрос, к которому относится сообщение бота в чате операторов."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT dialogs.* FROM operator_messages JOIN dialogs ON dialogs.id = operator_messages.dialog_id "
            "WHERE operator_messages.chat_id = ? AND operator_messages.message_id = ?",
            (chat_id, message_id),
        )
        return await cursor.fetchone()


async def count_for_user(user_id: int) -> int:
    async with connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM dialogs WHERE user_id = ?", (user_id,))
        (count,) = await cursor.fetchone()
        return count


async def summary(days: int) -> dict:
    """Для /admin: сколько запросов к операторам за период (по причинам) и сколько открыто сейчас."""
    async with connect() as db:
        cursor = await db.execute(
            "SELECT reason, COUNT(*) AS n FROM dialogs WHERE created_at >= date('now', 'localtime', ?) "
            "AND closed_by IS NOT 'error' GROUP BY reason",
            (f"-{days - 1} days",),
        )
        reasons = {row["reason"]: row["n"] for row in await cursor.fetchall()}
        cursor = await db.execute(
            "SELECT status, COUNT(*) AS n FROM dialogs WHERE status IN ('waiting', 'active') GROUP BY status"
        )
        open_now = {row["status"]: row["n"] for row in await cursor.fetchall()}
    return {"reasons": reasons, "waiting": open_now.get("waiting", 0), "active": open_now.get("active", 0)}


async def anonymize_older_than(days: int) -> int:
    """Старые запросы: имя клиента и переписку стираем, открытые — закрываем.
    Возвращает число обезличенных запросов."""
    async with connect() as db:
        cursor = await db.execute(
            "UPDATE dialogs SET client_name = ?, context = ?, user_id = ?, "
            "closed_by = CASE WHEN status = 'closed' THEN closed_by ELSE 'expired' END, "
            "closed_at = COALESCE(closed_at, datetime('now', 'localtime')), status = 'closed' "
            "WHERE created_at < datetime('now', 'localtime', ?) AND user_id != ?",
            (ANONYMIZED, ANONYMIZED, ANONYMOUS_USER_ID, f"-{days} days", ANONYMOUS_USER_ID),
        )
        await db.commit()
        return cursor.rowcount
