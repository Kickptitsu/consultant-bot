"""Подключение к SQLite и создание таблиц."""

from contextlib import asynccontextmanager

import aiosqlite

from config import config

# Схема базы. CREATE TABLE IF NOT EXISTS не трогает уже созданные таблицы,
# поэтому init_db() можно спокойно вызывать при каждом запуске.
SCHEMA = """
-- Клиенты бота. context_from — с какого сообщения начинается текущая тема:
-- кнопка «Новый вопрос» сдвигает его, и ИИ перестаёт видеть старые сообщения
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    context_from INTEGER NOT NULL DEFAULT 0
);

-- Переписка: вопросы клиента и ответы бота. Последние сообщения ИИ видит
-- как контекст. Старше HISTORY_RETENTION_DAYS дней — удаляются
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT    NOT NULL,                   -- 'user' (клиент), 'assistant' (ИИ) или 'operator'
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);

-- Каждый вопрос клиента и чем он закончился — для статистики и расхода токенов.
-- status: answered (ИИ ответил), no_answer (в базе нет ответа), off_topic (не по теме),
--         blocked (попытка сломать правила), error (ИИ недоступен)
CREATE TABLE IF NOT EXISTS questions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    text              TEXT    NOT NULL,
    status            TEXT    NOT NULL,
    provider          TEXT    NOT NULL,            -- demo / YandexGPT / GigaChat / filter
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_questions_created ON questions(created_at);
CREATE INDEX IF NOT EXISTS idx_questions_user ON questions(user_id);

-- ---------- Передача живому оператору ----------

-- Запросы к операторам. status: waiting (ждёт, кто возьмёт), active (оператор ведёт
-- диалог), closed (закрыт). Пока запрос открыт, ИИ клиенту не отвечает.
-- reason: button (клиент позвал сам), no_answer (в базе нет ответа), error (ИИ недоступен)
-- closed_by: operator / blocked (оператор закрыл и заблокировал клиента) / client (отменил) /
--            error (карточку не доставить) / privacy / expired
CREATE TABLE IF NOT EXISTS dialogs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    client_name   TEXT    NOT NULL DEFAULT '',
    reason        TEXT    NOT NULL,
    context       TEXT    NOT NULL DEFAULT '',     -- последние сообщения на момент запроса (HTML)
    status        TEXT    NOT NULL DEFAULT 'waiting',
    operator_id   INTEGER,
    operator_name TEXT,
    closed_by     TEXT,
    closer_name   TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    taken_at      TEXT,
    closed_at     TEXT
);
-- Не больше одного открытого запроса на клиента: второй INSERT база просто не пустит,
-- даже если клиент нажал кнопку дважды, а ИИ в тот же момент не нашёл ответа
CREATE UNIQUE INDEX IF NOT EXISTS idx_dialogs_open ON dialogs(user_id) WHERE status IN ('waiting', 'active');
CREATE INDEX IF NOT EXISTS idx_dialogs_created ON dialogs(created_at);

-- Сообщения бота в чате операторов и к какому запросу они относятся.
-- Оператор отвечает реплаем на любое из них — по этой таблице бот находит клиента.
-- kind: card (карточка запроса) / client (пересланное сообщение клиента)
CREATE TABLE IF NOT EXISTS operator_messages (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    dialog_id  INTEGER NOT NULL,
    kind       TEXT    NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_operator_messages_dialog ON operator_messages(dialog_id);

-- Клиенты, которых оператор заблокировал за спам: до until они не могут звать оператора
-- (ИИ им отвечает как обычно). Истёкшие блокировки удаляет фоновая уборка
CREATE TABLE IF NOT EXISTS blocks (
    user_id       INTEGER PRIMARY KEY,
    until         TEXT    NOT NULL,
    operator_name TEXT    NOT NULL,
    dialog_id     INTEGER NOT NULL,                -- из какого запроса заблокировали
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ---------- Персональные данные ----------

-- Согласия на обработку ПД. Строки не удаляются: при отзыве ставится revoked_at.
-- Так остаётся доказательство, когда и на какую версию текста согласился человек.
CREATE TABLE IF NOT EXISTS consents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    version     TEXT    NOT NULL,                  -- версия текста согласия
    accepted_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    revoked_at  TEXT                               -- NULL, пока согласие действует
);
CREATE INDEX IF NOT EXISTS idx_consents_user ON consents(user_id);

-- Журнал событий с персональными данными: чтобы заказчик мог подтвердить,
-- что выполнил требование. В details — только количества, без текстов сообщений.
CREATE TABLE IF NOT EXISTS privacy_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    user_id    INTEGER NOT NULL,
    event      TEXT    NOT NULL,
    details    TEXT    NOT NULL DEFAULT '',
    actor      TEXT    NOT NULL                    -- 'user' или 'auto'
);
"""

# Колонки, которые появятся в уже существующих таблицах позже первой версии.
# CREATE TABLE IF NOT EXISTS не добавляет колонки в созданную таблицу,
# поэтому init_db() дописывает недостающие через ALTER TABLE — данные не теряются.
MIGRATIONS: dict[str, dict[str, str]] = {}


@asynccontextmanager
async def connect():
    """Открывает соединение с базой. Использование:

    async with connect() as db:
        await db.execute(...)
    """
    async with aiosqlite.connect(config.db_path) as db:
        # Строки результата можно читать по имени колонки: row["text"]
        db.row_factory = aiosqlite.Row
        yield db


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет, и добавляет недостающие колонки."""
    async with connect() as db:
        await db.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            cursor = await db.execute(f"PRAGMA table_info({table})")
            existing = {row["name"] for row in await cursor.fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        await db.commit()
