"""Данные, которые «зашиты» в inline-кнопки.

Telegram передаёт боту строку callback_data (до 64 байт). Классы CallbackData
сами упаковывают поля в строку вида "kb:get:faq.md" и распаковывают обратно,
а в хендлерах можно фильтровать по ним: KbCB.filter(F.action == "get").
"""

from aiogram.filters.callback_data import CallbackData

POLICY = "policy"   # прислать политику обработки ПД файлом (если ссылка не задана)


class ConsentCB(CallbackData, prefix="consent"):
    """Ответ на запрос согласия: agree / decline."""
    action: str


class PrivacyCB(CallbackData, prefix="privacy"):
    """Кнопки /privacy: delete_ask (спросить «точно?») / delete_confirm / back."""
    action: str


class HandoffCB(CallbackData, prefix="handoff"):
    """Кнопка клиента «Отменить» у запроса к оператору (пока его никто не взял)."""
    action: str
    dialog_id: int


# ---------- Кнопки операторов (в их группе) ----------

class OperatorCB(CallbackData, prefix="op"):
    """Карточка запроса: take (взять) / close (закрыть) / block_ask, block, back
    (заблокировать клиента с подтверждением) / unblock (снять блокировку)."""
    action: str
    dialog_id: int


# ---------- Кнопки администратора ----------

class KbCB(CallbackData, prefix="kb"):
    """База знаний: list (список файлов) / get (прислать файл) / del_ask / del."""
    action: str
    name: str = ""


class AdminCB(CallbackData, prefix="adm"):
    """Панель /admin: stats (статистика) / unanswered (вопросы без ответа) за days дней."""
    action: str
    days: int


def fits(callback: CallbackData) -> bool:
    """Влезает ли кнопка в 64 байта (длинное русское имя файла может не влезть)."""
    return len(callback.pack().encode("utf-8")) <= 64
