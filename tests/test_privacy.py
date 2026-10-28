"""Персональные данные: /privacy, удаление переписки, политика, автоудаление, смена провайдера."""

import texts as bot_texts
from conftest import CLIENT
from services import cleanup


async def test_privacy_shows_stored_data_and_deletes_it(consultant):
    await consultant.agree()
    await consultant.ask("Сколько стоит замена масла?")
    await consultant.ask("Где вы находитесь?")

    calls = await consultant.send(CLIENT, "/privacy")
    assert "✅ дано" in calls[0].text
    assert "Сообщений в переписке с ботом: 4" in calls[0].text
    assert "🗑 Отозвать согласие и удалить данные" in calls[0].buttons()

    calls = await consultant.press(CLIENT, "privacy:delete_confirm")
    assert bot_texts.PRIVACY_DONE in calls[-1].text
    assert consultant.db("SELECT * FROM messages") == []
    assert consultant.db("SELECT user_id, text, status FROM questions") == [
        (0, "[удалено]", "answered"), (0, "[удалено]", "answered")
    ]
    assert consultant.db("SELECT revoked_at IS NOT NULL FROM consents") == [(1,)]

    # Без согласия бот снова его спрашивает, а не отвечает
    calls = await consultant.send(CLIENT, "Сколько стоит ТО-1?")
    assert bot_texts.CONSENT_NEEDED in [call.text for call in calls]


async def test_privacy_log_has_counts_but_no_texts(consultant):
    await consultant.agree()
    await consultant.ask("Сколько стоит замена масла?")
    await consultant.press(CLIENT, "privacy:delete_confirm")
    log = consultant.db("SELECT event, details FROM privacy_log ORDER BY id")
    assert [event for event, _ in log] == ["consent_given", "consent_revoked", "data_deleted"]
    assert "масла" not in str(log)


async def test_policy_file_is_sent_without_customer_memo(consultant):
    calls = await consultant.press(CLIENT, "policy")
    document = calls[-1].method.document.data.decode("utf-8")
    assert document.startswith("# Политика обработки персональных данных")
    assert "ПАМЯТКА" not in document


async def test_changing_ai_provider_asks_consent_again(consultant, settings):
    """Сменили демо на YandexGPT — тексты теперь уходят Яндексу, нужно новое согласие."""
    await consultant.agree()
    settings(ai_provider="yandex")
    calls = await consultant.send(CLIENT, "/start")
    assert "YandexGPT" in calls[-1].text and "✅ Согласен" in calls[-1].buttons()


async def test_old_messages_are_deleted_automatically(consultant):
    await consultant.agree()
    await consultant.ask("Сколько стоит замена масла?")
    await consultant.ask("Где вы находитесь?")
    consultant.db("UPDATE messages SET created_at = datetime('now', 'localtime', '-31 days') WHERE id <= 2")
    consultant.db("UPDATE questions SET created_at = datetime('now', 'localtime', '-31 days') WHERE id = 1")

    assert await cleanup.run_once() == (2, 1, 0)   # сообщений, вопросов, запросов к операторам
    assert consultant.db("SELECT COUNT(*) FROM messages") == [(2,)]
    assert consultant.db("SELECT text FROM questions ORDER BY id") == [("[удалено]",), ("Где вы находитесь?",)]
