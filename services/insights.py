"""Подсказки админу: о чём чаще всего спрашивают, а в базе знаний ответа нет.

Вопросы без ответа группируем по темам без ИИ: берём значимые слова (utils/words.py)
и считаем, в скольких вопросах встречается каждое — «покраска», «покрасить»,
«покраску» считаются одним словом. Тема из 3 вопросов — повод дописать базу.
"""

from collections import Counter
from dataclasses import dataclass

from utils.words import PRICE_WORDS, similar, words


@dataclass(frozen=True)
class Topic:
    word: str      # самая частая форма слова: «покраска»
    count: int     # в скольких вопросах встречается


def topics(questions: list[str], limit: int = 8) -> list[Topic]:
    groups: list[Counter[str]] = []   # группа похожих слов -> сколько раз встречалась каждая форма
    counts: list[int] = []            # в скольких вопросах встречается группа
    for question in questions:
        seen: set[int] = set()        # одна группа — один раз на вопрос
        for word in sorted(words(question) - PRICE_WORDS):
            if word.isdigit():
                continue
            index = next((i for i, group in enumerate(groups) if any(similar(word, w) for w in group)), None)
            if index is None:
                groups.append(Counter())
                counts.append(0)
                index = len(groups) - 1
            groups[index][word] += 1
            if index not in seen:
                seen.add(index)
                counts[index] += 1
    # Название темы — самая частая форма слова, при равенстве — короче и раньше по алфавиту
    result = [Topic(min(group, key=lambda w: (-group[w], len(w), w)), count) for group, count in zip(groups, counts)]
    result.sort(key=lambda topic: (-topic.count, topic.word))
    return result[:limit]
