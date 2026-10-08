"""Юнит-тесты истории чатов на МОКЕ LLM и изолированной временной history.db.

Проверяют: сохранение user/assistant после /api/chat, создание беседы без
conversation_id, продолжение существующего треда, список бесед (порядок,
message_count), сообщения беседы (порядок, 404), устойчивость к ошибке записи.

history.db перенаправляется на tmp_path через monkeypatch — боевая БД не трогается.
"""
import pytest
from fastapi.testclient import TestClient

from app import config, history
from app.main import app
from app.routers import chat as chat_mod
from app.schemas import SearchHit

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    # Отдельная временная БД истории на каждый тест.
    db = tmp_path / "history.db"
    monkeypatch.setattr(history, "HISTORY_DB_PATH", str(db))
    history.init_schema()

    # Сконфигурированный ассистент + inline-режим (2 LLM-вызова: keywords + ответ).
    monkeypatch.setattr(config, "LLM_CONFIGURED", True)
    monkeypatch.setattr(config, "LLM_TOOL_MODE", "inline")
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET_RUB", 100.0)
    chat_mod._budget["date"] = None
    chat_mod._budget["tokens"] = 0

    # Детерминированный поиск: один хит (для inline-пути, mode=None).
    monkeypatch.setattr(
        chat_mod, "_run_search",
        lambda *a, **k: [
            SearchHit(type="page", source_id=1, source_title="Книга",
                      page_index=4, snippet="<mark>текст</mark>", score=-1.0)
        ],
    )
    # Детерминированная онтология: один хит (для дефолтного mode=ontology).
    monkeypatch.setattr(
        chat_mod, "search_ontology",
        lambda *a, **k: [{
            "source_id": 1, "source_slug": "book1", "source_title": "Книга",
            "chapter_title": "Глава", "page_index": 4, "kind": "chapter",
            "snippet": "<mark>текст</mark>", "score": -1.0,
        }],
    )
    yield


def _mock_llm(monkeypatch, answer="Нашёл источник."):
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": "ключевые слова"}}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        {"choices": [{"message": {"role": "assistant", "content": answer}}],
         "usage": {"prompt_tokens": 20, "completion_tokens": 8}},
    ]
    calls = {"n": 0}

    def fake(messages, tools=None, tool_choice=None):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    monkeypatch.setattr(chat_mod.llm, "chat_completion", fake)


def test_chat_creates_conversation_and_saves_two_messages(monkeypatch):
    # mode=None → inline-путь с текстовым ответом модели (персист проверяем на нём).
    _mock_llm(monkeypatch, "Ответ 1.")
    r = client.post("/api/chat", json={"message": "антибиотик для собаки", "mode": None})
    assert r.status_code == 200
    body = r.json()
    conv_id = body["conversation_id"]
    assert conv_id > 0

    # список видит беседу с 2 сообщениями
    lst = client.get("/api/conversations").json()
    assert len(lst) == 1
    assert lst[0]["id"] == conv_id
    assert lst[0]["message_count"] == 2
    assert lst[0]["title"] == "антибиотик для собаки"

    # сообщения по порядку: user → assistant, у assistant сохранены hits
    detail = client.get(f"/api/conversations/{conv_id}").json()
    msgs = detail["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "антибиотик для собаки"
    assert msgs[1]["content"] == "Ответ 1."
    assert len(msgs[1]["hits"]) == 1
    assert msgs[1]["hits"][0]["source_id"] == 1
    assert msgs[1]["usage"] is not None


def test_ontology_mode_persists_hits_with_empty_answer(monkeypatch):
    # Дефолтный mode=ontology: модель выводов не пишет (answer=""), но user- и
    # assistant-реплики сохраняются, и у assistant в истории лежат хиты.
    _mock_llm(monkeypatch)  # 1-й ответ (ключевые слова) — этого достаточно
    r = client.post("/api/chat", json={"message": "стрептококкоз"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == ""
    assert len(body["hits"]) == 1
    conv_id = body["conversation_id"]

    detail = client.get(f"/api/conversations/{conv_id}").json()
    msgs = detail["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == ""
    assert len(msgs[1]["hits"]) == 1
    assert msgs[1]["hits"][0]["source_id"] == 1


def test_continue_existing_conversation(monkeypatch):
    _mock_llm(monkeypatch, "Первый.")
    first = client.post("/api/chat", json={"message": "вопрос 1", "mode": None}).json()
    conv_id = first["conversation_id"]

    _mock_llm(monkeypatch, "Второй.")
    second = client.post(
        "/api/chat", json={"message": "вопрос 2", "conversation_id": conv_id, "mode": None}
    ).json()
    assert second["conversation_id"] == conv_id

    # один тред, 4 сообщения по порядку
    lst = client.get("/api/conversations").json()
    assert len(lst) == 1
    assert lst[0]["message_count"] == 4

    msgs = client.get(f"/api/conversations/{conv_id}").json()["messages"]
    assert [m["content"] for m in msgs] == ["вопрос 1", "Первый.", "вопрос 2", "Второй."]


def test_unknown_conversation_id_starts_new_thread(monkeypatch):
    _mock_llm(monkeypatch)
    r = client.post("/api/chat", json={"message": "привет", "conversation_id": 9999})
    assert r.status_code == 200
    # несуществующий id → создана новая беседа (не 9999)
    assert r.json()["conversation_id"] != 9999
    assert client.get("/api/conversations/9999").status_code == 404


def test_list_order_newest_first(monkeypatch):
    _mock_llm(monkeypatch)
    a = client.post("/api/chat", json={"message": "первая беседа"}).json()["conversation_id"]
    b = client.post("/api/chat", json={"message": "вторая беседа"}).json()["conversation_id"]
    lst = client.get("/api/conversations").json()
    assert [c["id"] for c in lst] == [b, a]


def test_get_conversation_404(monkeypatch):
    r = client.get("/api/conversations/12345")
    assert r.status_code == 404


def test_history_write_error_does_not_break_response(monkeypatch):
    _mock_llm(monkeypatch, "Ответ несмотря на сбой.")

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(history, "create_conversation", boom)

    r = client.post("/api/chat", json={"message": "тест сбоя записи", "mode": None})
    assert r.status_code == 200
    assert r.json()["answer"] == "Ответ несмотря на сбой."
    # ничего не сохранилось, но ответ отдан
    assert client.get("/api/conversations").json() == []


def test_pagination_limit_validation(monkeypatch):
    r = client.get("/api/conversations", params={"limit": 999})
    assert r.status_code == 422  # limit <= 200
