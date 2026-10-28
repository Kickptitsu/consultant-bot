"""Ограничение темпа: не больше limit событий за period секунд на одного пользователя.

Считаем в памяти, а не в базе: после перезапуска бота счёт начнётся заново,
но для защиты от «200 сообщений за минуту» этого достаточно.
"""

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(self, limit: int, period: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.period = period
        self.clock = clock   # в тестах подменяем, чтобы «прошла минута» без ожидания
        self._events: dict[int, deque[float]] = {}
        self._warned: dict[int, float] = {}

    def hit(self, key: int) -> bool:
        """Засчитать событие. False — лимит исчерпан, событие не засчитано."""
        now = self.clock()
        events = self._events.setdefault(key, deque())
        while events and now - events[0] >= self.period:
            events.popleft()   # старше окна — уже не считаются
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True

    def should_warn(self, key: int) -> bool:
        """Предупредить о лимите — один раз за окно, а не на каждое лишнее сообщение."""
        now = self.clock()
        last = self._warned.get(key)
        if last is not None and now - last < self.period:
            return False
        self._warned[key] = now
        return True

    def reset(self) -> None:
        self._events.clear()
        self._warned.clear()
