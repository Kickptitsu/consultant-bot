"""База знаний: markdown-файлы из папки knowledge/.

Вся база целиком отправляется ИИ вместе с вопросом клиента — так модель видит все
цены и условия сразу (почему без поиска по фрагментам — в README, «Как бот ищет ответ»).

Файлы перечитываются сами, как только изменились: админ прислал новый файл боту
или поправил его на сервере — перезапускать бота не нужно.
"""

import math
import os
import re
from dataclasses import dataclass

from config import KNOWLEDGE_MAX_FILE_KB, KNOWLEDGE_MAX_TOKENS, config

EXTENSIONS = (".md", ".txt")
# Имя файла: буквы (в том числе русские), цифры, «-» и «_». Никаких «..» и «/» —
# иначе файлом вида ../bot.py можно было бы перезаписать код бота
FILE_NAME_RE = re.compile(r"[\w\-]+\.(md|txt)", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class KnowledgeError(Exception):
    """Файл не подходит для базы знаний. Текст ошибки показываем админу."""


@dataclass(frozen=True)
class Section:
    """Кусок базы под одним заголовком — по ним ищет демо-режим."""
    file: str
    title: str   # «Услуги и цены → Диагностика»
    body: str


@dataclass(frozen=True)
class KnowledgeFile:
    name: str
    size: int    # в байтах
    tokens: int  # примерно


@dataclass(frozen=True)
class Knowledge:
    files: tuple[KnowledgeFile, ...]
    text: str                     # вся база одним текстом — для промта
    sections: tuple[Section, ...]

    @property
    def tokens(self) -> int:
        return sum(file.tokens for file in self.files)


def estimate_tokens(text: str) -> int:
    """Примерное число токенов. Для русского текста токен — это 3–4 символа,
    берём 3 с запасом. Точное число ИИ сообщает после каждого ответа."""
    return math.ceil(len(text) / 3)


def _signature() -> tuple:
    """Что лежит в папке: имена, время изменения и размеры файлов."""
    folder = config.knowledge_dir
    if not os.path.isdir(folder):
        return (folder,)
    entries = []
    for entry in os.scandir(folder):
        if entry.is_file() and entry.name.lower().endswith(EXTENSIONS):
            stat = entry.stat()
            entries.append((entry.name, stat.st_mtime_ns, stat.st_size))
    return (folder, *sorted(entries))


_cache: Knowledge | None = None
_cache_signature: tuple | None = None


def get() -> Knowledge:
    """Текущая база. Если файлы изменились с прошлого раза — перечитывает их."""
    global _cache, _cache_signature
    signature = _signature()
    if _cache is None or signature != _cache_signature:
        _cache = _load()
        _cache_signature = signature
    return _cache


def _load() -> Knowledge:
    folder = config.knowledge_dir
    files, parts, sections = [], [], []
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    for name in names:
        path = os.path.join(folder, name)
        if not (os.path.isfile(path) and name.lower().endswith(EXTENSIONS)):
            continue
        with open(path, encoding="utf-8-sig", errors="replace") as file:
            content = file.read().strip()
        if not content:
            continue
        files.append(KnowledgeFile(name, os.path.getsize(path), estimate_tokens(content)))
        parts.append(f"=== Файл {name} ===\n{content}")
        sections.extend(split_sections(name, content))
    return Knowledge(tuple(files), "\n\n".join(parts), tuple(sections))


def split_sections(file_name: str, content: str) -> list[Section]:
    """Режет markdown по заголовкам. В название раздела входят и заголовки выше:
    «Услуги и цены → Диагностика»."""
    sections: list[Section] = []
    headings: list[tuple[int, str]] = []   # (уровень, текст) — «путь» до текущего раздела
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            title = " → ".join(text for _, text in headings) or file_name
            sections.append(Section(file_name, title, body))
        lines.clear()

    for line in content.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, match.group(2)))
        else:
            lines.append(line)
    flush()
    return sections


def _file_path(name: str) -> str:
    if not FILE_NAME_RE.fullmatch(name or ""):
        raise KnowledgeError(
            "Имя файла должно состоять из букв, цифр, «-» и «_» и заканчиваться на .md или .txt, "
            "например <code>prices.md</code>."
        )
    return os.path.join(config.knowledge_dir, name)


def save_file(name: str, data: bytes) -> tuple[bool, Knowledge]:
    """Сохраняет файл в базу (новый или вместо старого с тем же именем).

    Возвращает (создан ли новый файл, база после сохранения).
    """
    path = _file_path(name)
    if len(data) > KNOWLEDGE_MAX_FILE_KB * 1024:
        raise KnowledgeError(f"Файл больше {KNOWLEDGE_MAX_FILE_KB} КБ — разделите его на несколько.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise KnowledgeError("Файл должен быть в кодировке UTF-8 (в Блокноте: «Сохранить как» → UTF-8).")
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise KnowledgeError("Файл пустой.")

    current = get()
    file_name = os.path.basename(path)
    old = next((file for file in current.files if file.name.lower() == file_name.lower()), None)
    total = current.tokens - (old.tokens if old else 0) + estimate_tokens(text)
    if total > KNOWLEDGE_MAX_TOKENS:
        raise KnowledgeError(
            f"С этим файлом база станет около {total} токенов, а предел — {KNOWLEDGE_MAX_TOKENS}. "
            "Вся база отправляется ИИ с каждым вопросом, поэтому большая база — это дорого. "
            "Сократите текст или удалите ненужные файлы (/kb)."
        )

    os.makedirs(config.knowledge_dir, exist_ok=True)
    # Пишем во временный файл и подменяем одним действием: бот никогда
    # не прочитает наполовину записанный файл
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as file:
        file.write(text + "\n")
    os.replace(temp_path, path)
    return old is None, get()


def read_file(name: str) -> bytes:
    path = _file_path(name)
    if not os.path.isfile(path):
        raise KnowledgeError("Такого файла в базе нет.")
    with open(path, "rb") as file:
        return file.read()


def delete_file(name: str) -> bool:
    path = _file_path(name)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True
