"""База знаний: чтение, перечитывание без перезапуска, загрузка файлом, /kb."""

import os

import pytest

from config import config
from conftest import ADMIN, CLIENT, texts
from services import knowledge
from services.knowledge import KnowledgeError

NEW_FILE = "# Мойка\n\n## Мойка автомобиля\n\nКомплексная мойка кузова и салона — 1 500 ₽.\n".encode()


def test_knowledge_is_split_into_sections(settings):
    base = knowledge.get()
    assert [file.name for file in base.files] == [
        "contacts.md", "faq.md", "schedule.md", "services.md", "warranty.md"
    ]
    titles = [section.title for section in base.sections]
    assert "Услуги и цены → Диагностика" in titles
    assert "=== Файл services.md ===" in base.text


def test_changed_files_are_reloaded(settings):
    assert "Мойка" not in knowledge.get().text
    with open(os.path.join(config.knowledge_dir, "wash.md"), "wb") as file:
        file.write(NEW_FILE)
    assert "Комплексная мойка" in knowledge.get().text


@pytest.mark.parametrize("name", ["../bot.py", "..\\evil.md", "price list.md", "virus.exe", ".md"])
def test_bad_file_names_are_rejected(settings, name):
    with pytest.raises(KnowledgeError):
        knowledge.save_file(name, b"text")


def test_bad_contents_are_rejected(settings, monkeypatch):
    with pytest.raises(KnowledgeError, match="UTF-8"):
        knowledge.save_file("cp1251.md", "Цены".encode("cp1251"))
    with pytest.raises(KnowledgeError, match="пустой"):
        knowledge.save_file("empty.md", b"  \n ")
    monkeypatch.setattr(knowledge, "KNOWLEDGE_MAX_TOKENS", 1000)
    with pytest.raises(KnowledgeError, match="предел"):
        knowledge.save_file("big.md", ("Очень длинный текст. " * 200).encode())
    assert not os.path.exists(os.path.join(config.knowledge_dir, "big.md"))


async def test_admin_uploads_file_and_bot_uses_it_at_once(consultant):
    calls = await consultant.upload(ADMIN, "wash.md", NEW_FILE)
    assert "✅ Файл <code>wash.md</code> добавлен" in calls[-1].text
    assert "6 файлов" in calls[-1].text

    await consultant.agree()
    reply = await consultant.ask("Сколько стоит мойка автомобиля?")
    assert "1 500 ₽" in reply.text

    calls = await consultant.upload(ADMIN, "wash.md", NEW_FILE.replace(b"1 500", b"1 700"))
    assert "обновлён" in calls[-1].text
    reply = await consultant.ask("Сколько стоит мойка автомобиля?")
    assert "1 700 ₽" in reply.text


async def test_upload_errors_are_explained(consultant):
    calls = await consultant.upload(ADMIN, "prices.docx", b"PK...")
    assert "нужен файл .md или .txt" in calls[-1].text
    calls = await consultant.upload(ADMIN, "prices.md", "Цены".encode("cp1251"))
    assert "UTF-8" in calls[-1].text


async def test_client_cannot_upload_or_open_kb(consultant):
    await consultant.upload(CLIENT, "wash.md", NEW_FILE)
    assert not os.path.exists(os.path.join(config.knowledge_dir, "wash.md"))
    calls = await consultant.send(CLIENT, "/kb")
    assert "База знаний" not in calls[-1].text
    calls = await consultant.press(CLIENT, "kb:del:faq.md")
    assert calls[0].text == "Кнопка устарела"
    assert os.path.exists(os.path.join(config.knowledge_dir, "faq.md"))


async def test_kb_download_and_delete(consultant):
    calls = await consultant.send(ADMIN, "/kb")
    assert "5 файлов" in calls[0].text and "services.md" in calls[0].text
    assert "kb:get:services.md" in calls[0].callbacks()

    calls = await consultant.press(ADMIN, "kb:get:services.md")
    document = calls[-1].method.document.data.decode("utf-8")
    assert document.startswith("# Услуги и цены")

    calls = await consultant.press(ADMIN, "kb:del_ask:faq.md")
    assert "Удалить <code>faq.md</code>?" in calls[-1].text
    calls = await consultant.press(ADMIN, "kb:del:faq.md")
    assert texts(calls, "answer") == ["Файл удалён"]
    assert "4 файла" in texts(calls, "edit")[0]
    assert "faq.md" not in [file.name for file in knowledge.get().files]
