"""Демо-режим: «ИИ» без ИИ — ключ API не нужен.

Ищет в базе знаний раздел, где больше всего слов из вопроса, и показывает его.
Ответ проходит те же проверки, что и ответ настоящей модели, поэтому в демо-режиме
можно показать заказчику весь путь: ответ из базы, «не знаю» и передачу оператору.

Слова сравниваются по началу (utils/words.py) — грубо, но для демонстрации хватает.
Настоящая модель понимает смысл, а не слова.
"""

from ai.prompt import REMINDER
from ai.providers.base import Completion, Provider
from services import knowledge
from services.knowledge import Section, estimate_tokens
# PRICE_WORDS в поиске раздела не участвуют, а из подходящих разделов выбирают тот, где есть цены
from utils.words import PRICE_WORDS, similar, words


def score(question: set[str], section: Section) -> int:
    """Слово из вопроса в заголовке раздела — 2 очка, в тексте — 1."""
    title, body = words(section.title), words(section.body)
    total = 0
    for word in question - PRICE_WORDS:
        if any(similar(word, other) for other in title):
            total += 2
        elif any(similar(word, other) for other in body):
            total += 1
    if total and question & PRICE_WORDS and "₽" in section.body:
        total += 1
    return total


def best_section(question: str, sections: tuple[Section, ...]) -> Section | None:
    query = words(question)
    best, best_score = None, 0
    for section in sections:
        value = score(query, section)
        if value > best_score:
            best, best_score = section, value
    # Одно совпадение в тексте — скорее случайность, нужно хотя бы 2 очка
    return best if best_score >= 2 else None


class DemoProvider(Provider):
    name = "demo"

    async def complete(self, messages: list[dict]) -> Completion:
        # Напоминание правил — для настоящей модели, в поиске по словам оно только мешает
        question = messages[-1]["content"].removesuffix(f"\n\n({REMINDER})")
        sections = knowledge.get().sections
        section = best_section(question, sections)
        if section is None:
            # Уточняющий вопрос («а для пенсионеров?») — ищем вместе с предыдущим
            previous = [message["content"] for message in messages[1:-1] if message["role"] == "user"]
            if previous:
                section = best_section(f"{previous[-1]} {question}", sections)

        if section is None:
            text = "NO_ANSWER"
        else:
            text = f"Демо-режим: вот что нашлось в справочнике.\n\n**{section.title}**\n{section.body}"
        # Токены в демо-режиме не тратятся — считаем примерно, чтобы работала статистика
        prompt = "".join(message["content"] for message in messages)
        return Completion(text, estimate_tokens(prompt), estimate_tokens(text))
