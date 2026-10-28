"""Передача оператору: карточка в группе, «Взять», ответы реплаем, закрытие, ИИ молчит."""

import asyncio

import texts as bot_texts
from conftest import ADMIN, CLIENT, CLIENT2, OPERATOR, OPERATOR2, OPS, texts
from keyboards.callbacks import HandoffCB, OperatorCB, PrivacyCB
from keyboards.main_menu import BTN_NEW_TOPIC, BTN_OPERATOR
from services import cleanup, handoff

TAKE = OperatorCB(action="take", dialog_id=1).pack()
CLOSE = OperatorCB(action="close", dialog_id=1).pack()
CANCEL = HandoffCB(action="cancel", dialog_id=1).pack()


def card(consultant):
    """Карточка запроса #1 — первое сообщение бота в группе операторов."""
    return next(call for call in consultant.telegram.calls
                if call.kind == "send" and call.chat_id == OPS and "Запрос #1" in call.text)


def to(calls, chat_id, kind="send"):
    return texts(calls, kind, chat_id)


async def call_operator(consultant, user_id=CLIENT):
    await consultant.agree(user_id)
    return await consultant.send(user_id, BTN_OPERATOR)


async def take(consultant, operator=OPERATOR):
    return await consultant.press(operator, TAKE, card(consultant).message_id, chat_id=OPS)


async def reply(consultant, operator, text, message_id=None):
    """Оператор отвечает реплаем (по умолчанию — на карточку)."""
    message_id = message_id or card(consultant).message_id
    return await consultant.send(operator, text, chat_type="supergroup", reply_to=message_id)


def dialog(consultant):
    return consultant.db("SELECT status, operator_id, closed_by FROM dialogs WHERE id = 1")[0]


# ---------- Клиент зовёт оператора ----------

async def test_button_sends_card_with_history(consultant, operators):
    await consultant.agree()
    await consultant.ask("Сколько стоит замена масла?")
    calls = await consultant.send(CLIENT, BTN_OPERATOR)

    assert to(calls, CLIENT) == [bot_texts.HANDOFF_STARTED]
    assert [c for c in calls if c.chat_id == CLIENT][0].buttons() == ["↩️ Отменить, вернуться к ИИ"]
    text = card(consultant).text
    for fragment in ("Запрос #1", "ждёт оператора", "Анна", "клиент позвал оператора",
                     "👤 Сколько стоит замена масла?", "🤖 Демо-режим"):
        assert fragment in text
    assert card(consultant).buttons() == ["✋ Взять", "✅ Закрыть", "🚫 Заблокировать"]
    assert dialog(consultant) == ("waiting", None, None)


async def test_menu_button_only_when_operators_chat_is_set(consultant, settings):
    calls = await consultant.send(CLIENT, "/start")
    assert BTN_OPERATOR not in calls[0].buttons()
    await consultant.agree()
    assert to(await consultant.send(CLIENT, BTN_OPERATOR), CLIENT) == [bot_texts.NO_OPERATORS]

    settings(operators_chat_id=OPS)
    calls = await consultant.send(CLIENT, "/start")
    assert BTN_OPERATOR in calls[0].buttons()


async def test_call_operator_needs_consent(consultant, operators):
    calls = await consultant.send(CLIENT, BTN_OPERATOR)
    assert bot_texts.CONSENT_NEEDED in texts(calls)
    assert to(calls, OPS) == []


async def test_no_answer_goes_to_operator(consultant, operators):
    await consultant.agree()
    reply_ = await consultant.ask("Сколько стоит покраска бампера?")
    assert reply_.text == bot_texts.HANDOFF_NO_ANSWER
    text = card(consultant).text
    assert "ИИ не нашёл ответа" in text and "покраска бампера" in text


async def test_ai_error_goes_to_operator(fake_ai, consultant, operators):
    await consultant.agree()
    fake_ai.fail = True
    assert (await consultant.ask("Сколько стоит диагностика?")).text == bot_texts.HANDOFF_ERROR
    assert "ошибка сервиса" in card(consultant).text


async def test_off_topic_is_not_handed_over(fake_ai, consultant, operators):
    await consultant.agree()
    fake_ai.replies = ["OFF_TOPIC"]
    calls = await consultant.send(CLIENT, "Расскажи анекдот")
    assert to(calls, CLIENT) == [bot_texts.OFF_TOPIC]
    assert to(calls, OPS) == []


async def test_double_call_opens_one_request(consultant, operators):
    await consultant.agree()
    start = len(consultant.telegram.calls)
    # Одновременные события видят ответы друг друга, поэтому считаем по общему списку
    await asyncio.gather(consultant.send(CLIENT, BTN_OPERATOR), consultant.send(CLIENT, BTN_OPERATOR))
    answers = sorted(to(consultant.telegram.calls[start:], CLIENT))
    assert answers == sorted([bot_texts.HANDOFF_STARTED, bot_texts.HANDOFF_WAITING])
    assert consultant.db("SELECT COUNT(*) FROM dialogs") == [(1,)]


async def test_operators_chat_unavailable(consultant, operators):
    consultant.telegram.blocked.add(OPS)   # бота удалили из группы
    calls = await call_operator(consultant)
    assert to(calls, CLIENT) == [bot_texts.HANDOFF_FAILED]
    assert dialog(consultant) == ("closed", None, "error")
    # «Нет ответа» — тогда честно отвечаем, как без операторов
    assert (await consultant.ask("Сколько стоит покраска бампера?")).text == bot_texts.NO_ANSWER


# ---------- Пока запрос открыт ----------

async def test_ai_is_silent_while_request_is_open(fake_ai, consultant, operators):
    await call_operator(consultant)
    calls = await consultant.send(CLIENT, "Стучит подвеска <спереди>")

    forwarded = [c for c in calls if c.chat_id == OPS]
    assert [c.text for c in forwarded] == ["👤 <b>Анна</b> · запрос #1:\nСтучит подвеска &lt;спереди&gt;"]
    assert forwarded[0].reply_to == card(consultant).message_id
    assert to(calls, CLIENT) == []
    assert fake_ai.requests == []

    assert to(await consultant.send(CLIENT, BTN_NEW_TOPIC), CLIENT) == [bot_texts.HANDOFF_WAITING]
    # Другой клиент в это время спокойно говорит с ИИ
    await consultant.agree(CLIENT2)
    assert (await consultant.ask("Где вы находитесь?", CLIENT2)).text.startswith("🤖")


async def test_client_cancels_waiting_request(consultant, operators):
    await call_operator(consultant)
    calls = await consultant.press(CLIENT, CANCEL)
    assert to(calls, CLIENT, "edit") == [bot_texts.HANDOFF_CANCELLED]
    assert "клиент отменил запрос" in to(calls, OPS, "edit")[0]
    assert dialog(consultant) == ("closed", None, "client")

    # Снова отвечает ИИ, а позвать оператора можно заново
    assert (await consultant.ask("Где вы находитесь?")).text.startswith("🤖")
    await consultant.send(CLIENT, BTN_OPERATOR)
    assert consultant.db("SELECT id, status FROM dialogs ORDER BY id") == [(1, "closed"), (2, "waiting")]


async def test_cancel_after_operator_took_it(consultant, operators):
    await call_operator(consultant)
    await take(consultant)
    calls = await consultant.press(CLIENT, CANCEL)
    assert calls[0].text == bot_texts.HANDOFF_TOO_LATE and calls[0].alert
    assert dialog(consultant)[0] == "active"


# ---------- Операторы ----------

async def test_first_operator_takes_dialog(consultant, operators):
    await call_operator(consultant)
    start = len(consultant.telegram.calls)
    await asyncio.gather(take(consultant, OPERATOR), take(consultant, OPERATOR2))
    all_calls = consultant.telegram.calls[start:]

    answers = [call for call in all_calls if call.kind == "answer"]
    assert sorted(bool(a.alert) for a in answers) == [False, True]   # один взял, второму — отказ
    assert "Диалог уже ведёт" in next(a.text for a in answers if a.alert)
    status, operator_id, _ = dialog(consultant)
    assert status == "active" and operator_id in (OPERATOR, OPERATOR2)

    assert to(all_calls, CLIENT) == [bot_texts.OPERATOR_JOINED]
    edited = to(all_calls, OPS, "edit")
    assert len(edited) == 1 and "ведёт" in edited[0]


async def test_operator_reply_reaches_client(consultant, operators):
    await call_operator(consultant)
    await take(consultant)
    calls = await reply(consultant, OPERATOR, "Покраску не делаем, но подскажем мастера")

    assert to(calls, CLIENT) == [bot_texts.OPERATOR_PREFIX + "Покраску не делаем, но подскажем мастера"]
    assert [c.text for c in calls if c.kind == "reaction"] == ["👍"]   # «доставлено»
    assert consultant.db("SELECT role FROM messages WHERE role = 'operator'") == [("operator",)]

    # Реплай на пересланное сообщение клиента тоже доходит
    forwarded = [c for c in await consultant.send(CLIENT, "А кто посоветуете?") if c.chat_id == OPS][0]
    calls = await reply(consultant, OPERATOR, "Мастерская на Ленина, 5", forwarded.message_id)
    assert to(calls, CLIENT) == [bot_texts.OPERATOR_PREFIX + "Мастерская на Ленина, 5"]


async def test_other_operator_cannot_answer(consultant, operators):
    await call_operator(consultant)
    await take(consultant, OPERATOR)
    calls = await reply(consultant, OPERATOR2, "Здравствуйте!")
    assert to(calls, CLIENT) == []
    assert "Этот диалог ведёт Иван" in to(calls, OPS)[0]


async def test_reply_without_take_takes_dialog(consultant, operators):
    await call_operator(consultant)
    calls = await reply(consultant, OPERATOR, "Здравствуйте, слушаю вас")
    assert to(calls, CLIENT) == [bot_texts.OPERATOR_JOINED, bot_texts.OPERATOR_PREFIX + "Здравствуйте, слушаю вас"]
    assert "ведёт Иван" in to(calls, OPS, "edit")[0]
    assert dialog(consultant)[:2] == ("active", OPERATOR)


async def test_close_returns_client_to_ai(fake_ai, consultant, operators):
    await consultant.agree()
    await consultant.ask("Сколько стоит ТО-1?")
    await consultant.send(CLIENT, BTN_OPERATOR)
    await take(consultant, OPERATOR)

    # Закрыть чужой диалог нельзя
    calls = await consultant.press(OPERATOR2, CLOSE, card(consultant).message_id, chat_id=OPS)
    assert calls[0].alert and "Закрыть может только" in calls[0].text

    calls = await consultant.press(OPERATOR, CLOSE, card(consultant).message_id, chat_id=OPS)
    assert to(calls, CLIENT) == [bot_texts.DIALOG_CLOSED]
    closed_card = [c for c in calls if c.kind == "edit"][0]
    assert "закрыт (Иван)" in closed_card.text and closed_card.markup is None
    assert dialog(consultant) == ("closed", OPERATOR, "operator")

    # Снова отвечает ИИ, и разговор до оператора ему в контекст не попадает
    assert (await consultant.ask("Где вы находитесь?")).text.startswith("🤖")
    assert len(fake_ai.requests[-1]) == 2


async def test_reply_to_closed_request(consultant, operators):
    await call_operator(consultant)
    await consultant.press(OPERATOR, CLOSE, card(consultant).message_id, chat_id=OPS)
    calls = await reply(consultant, OPERATOR, "Ещё вопрос?")
    assert to(calls, CLIENT) == []
    assert to(calls, OPS) == ["Запрос #1 закрыт — сообщение не отправлено."]


async def test_client_blocked_bot(consultant, operators):
    await call_operator(consultant)
    await take(consultant)
    consultant.telegram.blocked.add(CLIENT)
    calls = await reply(consultant, OPERATOR, "Здравствуйте!")
    assert "Не доставлено" in to(calls, OPS)[0]
    assert [c for c in calls if c.kind == "reaction"] == []


async def test_other_group_messages_are_ignored(consultant, operators):
    await call_operator(consultant)
    # Обычное сообщение в группе (не реплай боту) и реплай на что-то постороннее
    assert await consultant.send(OPERATOR, "Коллеги, кто на смене?", chat_type="supergroup") == []
    assert await reply(consultant, OPERATOR, "ок", message_id=123456) == []


async def test_chat_id_command(consultant, settings):
    calls = await consultant.send(ADMIN, "/chat_id", chat_type="supergroup")
    assert f"<code>{OPS}</code>" in calls[0].text
    assert await consultant.send(CLIENT, "/chat_id", chat_type="supergroup") == []   # не админ

    settings(operators_chat_id=OPS)
    calls = await consultant.send(ADMIN, "/chat_id", chat_type="supergroup")
    assert "Это чат операторов" in calls[0].text


# ---------- Защита группы операторов от спама ----------

async def test_too_many_messages_in_a_minute(consultant, operators):
    now = [1000.0]
    handoff.client_rate.clock = lambda: now[0]
    await call_operator(consultant)

    for number in range(5):
        calls = await consultant.send(CLIENT, f"Сообщение {number}")
        assert len(to(calls, OPS)) == 1
    calls = await consultant.send(CLIENT, "Шестое")
    assert to(calls, OPS) == [] and to(calls, CLIENT) == [bot_texts.TOO_FAST]
    calls = await consultant.send(CLIENT, "Седьмое")
    assert calls == []   # предупредили только что — молчим, чтобы не спамить и клиенту

    now[0] += 61   # прошла минута
    calls = await consultant.send(CLIENT, "Теперь можно")
    assert len(to(calls, OPS)) == 1


async def test_daily_handoff_limit(consultant, operators):
    await consultant.agree()
    for dialog_id in range(1, 6):   # «позвать → отменить» по кругу
        await consultant.send(CLIENT, BTN_OPERATOR)
        await consultant.press(CLIENT, HandoffCB(action="cancel", dialog_id=dialog_id).pack())

    calls = await consultant.send(CLIENT, BTN_OPERATOR)
    assert to(calls, CLIENT) == [bot_texts.HANDOFF_LIMIT] and to(calls, OPS) == []
    # Вопрос без ответа тоже не уходит — честный ответ с телефоном
    assert (await consultant.ask("Сколько стоит покраска бампера?")).text == bot_texts.NO_ANSWER
    assert consultant.db("SELECT COUNT(*) FROM dialogs") == [(5,)]


async def test_card_waits_when_telegram_asks_to(consultant, operators):
    consultant.telegram.retry_after[OPS] = 0   # «подождите 0 секунд» — и повтор проходит
    calls = await call_operator(consultant)
    assert to(calls, CLIENT) == [bot_texts.HANDOFF_STARTED]
    assert "Запрос #1" in card(consultant).text


async def test_too_long_wait_is_a_failure(consultant, operators):
    consultant.telegram.retry_after[OPS] = 600   # ждать 10 минут клиента не заставляем
    calls = await call_operator(consultant)
    assert to(calls, CLIENT) == [bot_texts.HANDOFF_FAILED]


async def test_operator_blocks_client(fake_ai, consultant, operators):
    await call_operator(consultant)
    card_id = card(consultant).message_id
    press = lambda action, operator=OPERATOR: consultant.press(  # noqa: E731
        operator, OperatorCB(action=action, dialog_id=1).pack(), card_id, chat_id=OPS)

    # Сначала — подтверждение, «Нет» возвращает карточку как была
    confirm = [c for c in await press("block_ask") if c.kind == "edit"][0]
    assert "Заблокировать клиента на 24 ч?" in confirm.text
    assert confirm.buttons() == ["🚫 Да, заблокировать на 24 ч", "⬅️ Нет"]
    back = [c for c in await press("back") if c.kind == "edit"][0]
    assert back.buttons() == ["✋ Взять", "✅ Закрыть", "🚫 Заблокировать"]

    calls = await press("block")
    assert to(calls, CLIENT) == [bot_texts.DIALOG_CLOSED]
    blocked_card = [c for c in calls if c.kind == "edit"][0]
    assert "клиент заблокирован до" in blocked_card.text and blocked_card.buttons() == ["🔓 Разблокировать"]
    assert dialog(consultant) == ("closed", None, "blocked")

    # Заблокированный клиент не зовёт оператора, но с ИИ говорит как обычно
    calls = await consultant.send(CLIENT, BTN_OPERATOR)
    assert to(calls, CLIENT) == [bot_texts.NO_OPERATORS] and to(calls, OPS) == []
    fake_ai.replies = ["NO_ANSWER", "ТО-1 стоит 2 500 ₽."]
    assert (await consultant.ask("Сколько стоит покраска?")).text == bot_texts.NO_ANSWER
    assert (await consultant.ask("Сколько стоит ТО-1?")).text == "🤖 ТО-1 стоит 2 500 ₽."

    calls = await press("unblock")
    assert "блокировка снята" in to(calls, OPS, "edit")[0]
    calls = await consultant.send(CLIENT, BTN_OPERATOR)
    assert to(calls, CLIENT) == [bot_texts.HANDOFF_STARTED]


async def test_only_leading_operator_can_block(consultant, operators):
    await call_operator(consultant)
    await take(consultant, OPERATOR)
    calls = await consultant.press(OPERATOR2, OperatorCB(action="block_ask", dialog_id=1).pack(),
                                   card(consultant).message_id, chat_id=OPS)
    assert calls[0].alert and "Это может только тот, кто ведёт диалог: Иван" in calls[0].text


async def test_block_expires(consultant, operators):
    await call_operator(consultant)
    await consultant.press(OPERATOR, OperatorCB(action="block", dialog_id=1).pack(),
                           card(consultant).message_id, chat_id=OPS)
    consultant.db("UPDATE blocks SET until = datetime('now', 'localtime', '-1 minute')")
    assert to(await consultant.send(CLIENT, BTN_OPERATOR), CLIENT) == [bot_texts.HANDOFF_STARTED]
    await cleanup.run_once()
    assert consultant.db("SELECT COUNT(*) FROM blocks") == [(0,)]


# ---------- Персональные данные ----------

async def test_privacy_delete_hides_messages_in_operators_chat(consultant, operators):
    await consultant.agree()
    await consultant.ask("Сколько стоит замена масла?")
    await consultant.send(CLIENT, BTN_OPERATOR)
    await consultant.send(CLIENT, "Мой номер машины А123ВС")

    calls = await consultant.press(CLIENT, PrivacyCB(action="delete_confirm").pack())
    edited = to(calls, OPS, "edit")
    assert len(edited) == 2
    assert any("клиент удалил свои данные" in text for text in edited)
    assert handoff.SCRUBBED in edited
    assert all("А123ВС" not in text and "Анна" not in text for text in edited)
    assert consultant.db("SELECT user_id, status, client_name, context FROM dialogs") == [
        (0, "closed", "[удалено]", "[удалено]")
    ]
    # Оператор не сможет написать «удалённому» клиенту
    calls = await reply(consultant, OPERATOR, "Здравствуйте!")
    assert to(calls, CLIENT) == []


async def test_old_requests_are_anonymized(consultant, operators):
    await call_operator(consultant)
    consultant.db("UPDATE dialogs SET created_at = datetime('now', 'localtime', '-31 days')")
    _, _, handoffs = await cleanup.run_once()
    assert handoffs == 1
    assert consultant.db("SELECT user_id, status, closed_by, client_name FROM dialogs") == [
        (0, "closed", "expired", "[удалено]")
    ]
