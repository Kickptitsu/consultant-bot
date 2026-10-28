"""Фоновая уборка: переписку старше HISTORY_RETENTION_DAYS дней удаляем.

Так обещано клиенту в тексте согласия. Статистика вопросов остаётся,
но тексты старых вопросов стираются и отвязываются от Telegram ID.
"""

import asyncio
import logging

from config import HISTORY_RETENTION_DAYS
from database import dialogs, history, privacy, stats

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 6 * 60 * 60   # раз в 6 часов


async def run_once() -> tuple[int, int, int]:
    messages = await history.delete_older_than(HISTORY_RETENTION_DAYS)
    questions = await stats.anonymize_older_than(HISTORY_RETENTION_DAYS)
    handoffs = await dialogs.anonymize_older_than(HISTORY_RETENTION_DAYS)
    await dialogs.delete_expired_blocks()   # блокировки живут не дольше BLOCK_HOURS
    if messages or questions or handoffs:
        await privacy.log_event(
            0, "retention_cleanup", "auto",
            f"старше {HISTORY_RETENTION_DAYS} дней: удалено сообщений — {messages}, "
            f"обезличено вопросов — {questions}, запросов к операторам — {handoffs}",
        )
        logger.info(
            "Автоудаление: сообщений — %s, вопросов обезличено — %s, запросов — %s",
            messages, questions, handoffs,
        )
    return messages, questions, handoffs


async def watch() -> None:
    while True:
        try:
            await run_once()
        except Exception:
            # Ошибка уборки не должна останавливать бота — попробуем в следующий раз
            logger.exception("Не удалось удалить старую переписку")
        await asyncio.sleep(CHECK_EVERY_SECONDS)
