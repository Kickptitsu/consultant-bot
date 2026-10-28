"""Проверки до и после ИИ — вторая линия защиты после системного промта.

Промт модель обычно слушается, но не всегда: её можно запутать или уговорить.
Поэтому бот не доверяет ответу вслепую:
- до ИИ: явные попытки сломать правила и «привет/спасибо» обрабатываются без модели
  (это ещё и экономит токены);
- после ИИ: ответ с секретной меткой промта не показывается, а ответ с ценой
  или сроком, которых нет в базе, считается выдумкой и уходит оператору.
"""

import re
from dataclasses import dataclass

from ai.prompt import BASE_END, BASE_START, CANARY, NO_ANSWER, OFF_TOPIC

# ---------- До ИИ ----------

# Типичные фразы prompt injection — попыток заставить ИИ нарушить инструкции.
# Список не обязан ловить всё: остальное отсекут правила промта и проверка ответа.
# Слова подобраны так, чтобы не задеть обычные вопросы: «как отменить запись»,
# «гарантия при нарушении правил эксплуатации», «ваши правила гарантии»
INJECTION_PATTERNS = [
    r"(игнорир|забуд|забыв|не (учитывай|обращай вниман|соблюдай|следуй))\w*"
    r".{0,40}(инструкц|правил|промпт|промт|указани|ограничени|настройк)",
    r"(систем|скрыт|исходн|внутренн|предыдущ|перв)\w*\s+(промпт|промт|инструкц|указани)",
    r"(покажи|выведи|повтори|перескажи|напиши|раскрой)\w*.{0,30}(свой|свои|твой|твои)\w*\s+"
    r"(промпт|промт|инструкц|правил|указани|настройк)",
    r"(ты теперь|теперь ты|отныне ты)\s+(не\s+)?(консультант|бот|ассистент|помощник|gpt|dan|друг\w*|зл\w+|хакер)",
    r"(представь,? что ты|притворись|веди себя как)",
    r"режим\w*\s+(разработчик|бога|без ограничений|dan)",
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules?|prompt)",
    r"\b(system prompt|developer mode|jailbreak)\b",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)

_GREETING_RE = re.compile(
    r"^(привет\w*|здравству\w*|добр\w+ (день|утро|вечер)|доброе утро|хай|hello|hi)[\s!.,)]*$", re.IGNORECASE
)
_THANKS_RE = re.compile(
    r"^(спасибо|благодарю|спс|ок|окей|понятно|ясно|хорошо|отлично)( (большое|огромное))?[\s!.,)]*$",
    re.IGNORECASE,
)


def is_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text.replace("ё", "е")))


def small_talk(text: str) -> str | None:
    """'greeting' / 'thanks' — если сообщение только приветствие или благодарность."""
    text = text.strip()
    if _GREETING_RE.match(text):
        return "greeting"
    if _THANKS_RE.match(text):
        return "thanks"
    return None


# ---------- После ИИ ----------

# Число с разделителями тысяч и дробной частью: 1200, 1 200, 1,5
_NUMBER = r"\d+(?:[   ]\d{3})*(?:[.,]\d+)?"
# Цена, срок, пробег, проценты — то, что модель не должна придумывать
_UNIT = r"(?:₽|руб|р\.|час|ч\.|мин|дн|день|сут|недел|месяц|мес|год|лет|км|тыс|%)"
_AMOUNT_RE = re.compile(rf"({_NUMBER})\s*{_UNIT}", re.IGNORECASE)
_ANY_NUMBER_RE = re.compile(_NUMBER)

# Куски промта, которые не должны попадать в ответ
LEAK_MARKERS = (CANARY, BASE_START, BASE_END, "ПРАВИЛА. Их нельзя")


def _normalize(number: str) -> str:
    return re.sub(r"[   ]", "", number).replace(".", ",")


def unknown_numbers(answer: str, knowledge_text: str) -> list[str]:
    """Цены и сроки из ответа, которых нет в базе знаний."""
    known = {_normalize(number) for number in _ANY_NUMBER_RE.findall(knowledge_text)}
    return [
        number for number in (_normalize(match) for match in _AMOUNT_RE.findall(answer))
        if number not in known
    ]


@dataclass(frozen=True)
class Verdict:
    status: str       # answered / no_answer / off_topic / blocked
    text: str = ""    # ответ для клиента (только для answered)
    reason: str = ""  # почему ответ отклонён — для логов


def check_answer(answer: str, knowledge_text: str) -> Verdict:
    answer = answer.strip()
    if any(marker in answer for marker in LEAK_MARKERS):
        return Verdict("blocked", reason="в ответе фрагмент системного промта")
    if OFF_TOPIC in answer:
        return Verdict("off_topic")
    if not answer or NO_ANSWER in answer:
        return Verdict("no_answer")
    invented = unknown_numbers(answer, knowledge_text)
    if invented:
        return Verdict("no_answer", reason=f"числа не из базы: {', '.join(invented)}")
    return Verdict("answered", answer)
