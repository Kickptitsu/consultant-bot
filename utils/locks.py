"""Очередь вопросов одного пользователя.

Если клиент быстро отправил два сообщения, aiogram обработает их параллельно:
оба запроса к ИИ уйдут без истории друг друга, а ответы могут прийти в обратном
порядке. Блокировка на пользователя ставит их в очередь: второй вопрос ждёт ответа
на первый и уже видит его в истории. Разные клиенты друг друга не ждут.
"""

import asyncio
from collections import Counter
from contextlib import asynccontextmanager

_locks: dict[int, asyncio.Lock] = {}
_users: Counter[int] = Counter()   # сколько обработчиков сейчас держат или ждут блокировку


@asynccontextmanager
async def user_lock(user_id: int):
    lock = _locks.setdefault(user_id, asyncio.Lock())
    _users[user_id] += 1
    try:
        async with lock:
            yield
    finally:
        _users[user_id] -= 1
        if not _users[user_id]:
            # Больше никто не ждёт — убираем, чтобы словарь не рос бесконечно
            del _users[user_id]
            _locks.pop(user_id, None)
