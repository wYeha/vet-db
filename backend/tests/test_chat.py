"""Юнит-тесты чат-эндпоинта на МОКЕ LLM (без реального router.ai).

Покрывают: happy-path (модель зовёт search → hits собраны), нет ключа → 503,
лимит петли function-calling, превышение суточного бюджета → 429.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.routers import chat as chat_mod
from app.schemas import SearchHit

client = TestClient(app)

# оригинал до подмены в фикстуре — нужен тесту, который проверяет реальный парсинг
_ORIG_RUN_SEARCH = chat_mod._run_search


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    # включаем «сконфигурированный» ассистент и безопасные лимиты по умолчанию
    monkeypatch.setattr(config, "LLM_CONFIGURED", True)
    monkeypatch.setattr(config, "LLM_MAX_TOOL_CALLS", 3)
    monkeypatch.setattr(config, "LLM_TOOL_MODE", "tools")
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET_RUB", 100.0)
    monkeypatch.setattr(config, "LLM_RUB_PER_1K_TOKENS", 0.2)
    # сброс суточного бюджета между тестами
    chat_mod._budget["date"] = None
    chat_mod._budget["tokens"] = 0
    # детерминированный поиск, не зависящий от содержимого БД
    monkeypatch.setattr(
        chat_mod, "_run_search",
        lambda *a, **k: [
            SearchHit(type="page", source_id=1, source_title="Книга",
                      page_index=4, snippet="<mark>текст</mark>", score=-1.0)
        ],
    )
    yield


def _tool_call_msg(q="ампициллин"):
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search",
                                 "arguments": json.dumps({"q": q})},
                }],
            }
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def _final_msg(text="Нашёл источник, см. ниже."):
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 15},
    }


def test_no_key_returns_503(monkeypatch):
    monkeypatch.setattr(config, "LLM_CONFIGURED", False)
    r = client.post("/api/chat", json={"message": "чем лечить?"})
    assert r.status_code == 503
    assert "сконфигур" in r.json()["detail"].lower()


def test_happy_path_tool_then_answer(monkeypatch):
    responses = [_tool_call_msg(), _final_msg("Нашёл: Книга, стр. 5.")]
    calls = {"n": 0}

    def fake(messages, tools=None, tool_choice=None):
        i = calls["n"]
        calls["n"] += 1
        return responses[i]

    monkeypatch.setattr(chat_mod.llm, "chat_completion", fake)

    r = client.post("/api/chat", json={"message": "антибиотик для собаки"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Нашёл: Книга, стр. 5."
    assert len(body["hits"]) == 1
    assert body["hits"][0]["type"] == "page"
    assert body["usage"]["tool_calls"] == 1
    assert body["usage"]["tokens_prompt"] == 220  # 100 + 120
    assert calls["n"] == 2


def test_loop_limit_forces_finalization(monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_TOOL_CALLS", 2)
    calls = {"n": 0}

    def always_tool(messages, tools=None, tool_choice=None):
        calls["n"] += 1
        # модель упорно зовёт tool; на финальном шаге tools=None → берём content
        msg = _tool_call_msg()
        msg["choices"][0]["message"]["content"] = "Финальный ответ."
        return msg

    monkeypatch.setattr(chat_mod.llm, "chat_completion", always_tool)

    r = client.post("/api/chat", json={"message": "зациклись"})
    assert r.status_code == 200
    # max_iters=2 → 2 итерации с tool + 1 финальная = 3 вызова модели
    assert calls["n"] == 3
    assert r.json()["usage"]["tool_calls"] == 2


def test_budget_exceeded_returns_429(monkeypatch):
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET_RUB", 0.0)

    def fake(messages, tools=None, tool_choice=None):
        return _final_msg()

    monkeypatch.setattr(chat_mod.llm, "chat_completion", fake)

    r = client.post("/api/chat", json={"message": "привет"})
    assert r.status_code == 429
    assert "бюджет" in r.json()["detail"].lower()


def test_llm_error_returns_502(monkeypatch):
    def boom(messages, tools=None, tool_choice=None):
        raise chat_mod.llm.LLMError("network down")

    monkeypatch.setattr(chat_mod.llm, "chat_completion", boom)

    r = client.post("/api/chat", json={"message": "привет"})
    assert r.status_code == 502
    # наружу не утекают внутренние детали ошибки провайдера
    assert "network" not in r.json()["detail"].lower()


def test_bad_tool_args_do_not_crash(monkeypatch):
    # модель присылает нечисловые limit/source_id — не должно быть 500
    responses = [
        {
            "choices": [{
                "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "search", "arguments": json.dumps(
                            {"q": "тест", "limit": "abc", "source_id": "xx"})},
                    }],
                }
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        _final_msg("Готово."),
    ]
    calls = {"n": 0}

    def fake(messages, tools=None, tool_choice=None):
        i = calls["n"]
        calls["n"] += 1
        return responses[i]

    monkeypatch.setattr(chat_mod.llm, "chat_completion", fake)
    # реальный _run_search (не мок), чтобы проверить защиту парсинга аргументов
    monkeypatch.setattr(chat_mod, "_run_search", _ORIG_RUN_SEARCH)
    r = client.post("/api/chat", json={"message": "поиск"})
    assert r.status_code == 200
    assert r.json()["answer"] == "Готово."


def test_inline_extracts_keywords_then_searches(monkeypatch):
    monkeypatch.setattr(config, "LLM_TOOL_MODE", "inline")
    # два LLM-вызова: 1) извлечение ключевых слов, 2) финальный ответ
    responses = [_final_msg("сальмонеллёз птица"), _final_msg("Смотрите источник ниже.")]
    calls = {"n": 0}

    def fake(messages, tools=None, tool_choice=None):
        i = calls["n"]
        calls["n"] += 1
        return responses[i]

    # записываем, с каким q реально идёт поиск
    seen = {}

    def rec_search(db, q, scope="all", source_id=None, limit=10, or_mode=False):
        seen["q"] = q
        seen["or_mode"] = or_mode
        return [SearchHit(type="page", source_id=1, source_title="Книга",
                          page_index=4, snippet="<mark>x</mark>", score=-1.0)]

    monkeypatch.setattr(chat_mod.llm, "chat_completion", fake)
    monkeypatch.setattr(chat_mod, "_run_search", rec_search)

    r = client.post("/api/chat", json={"message": "Что почитать про сальмонеллёз у птицы?"})
    assert r.status_code == 200
    body = r.json()
    # поиск шёл по ИЗВЛЕЧЁННЫМ ключевым словам, а не по сырому вопросу
    assert seen["q"] == "сальмонеллёз птица"
    assert calls["n"] == 2  # извлечение + финал
    assert body["answer"] == "Смотрите источник ниже."
    assert len(body["hits"]) == 1
    assert body["usage"]["tool_calls"] == 1


def test_inline_or_fallback_when_and_empty(monkeypatch):
    monkeypatch.setattr(config, "LLM_TOOL_MODE", "inline")
    responses = [_final_msg("сальмонеллёз птица корма"), _final_msg("Нашлось по OR.")]
    calls = {"n": 0}

    def fake(messages, tools=None, tool_choice=None):
        i = calls["n"]
        calls["n"] += 1
        return responses[i]

    modes = []

    def rec_search(db, q, scope="all", source_id=None, limit=10, or_mode=False):
        modes.append(or_mode)
        if not or_mode:
            return []  # точный AND — пусто
        return [SearchHit(type="page", source_id=2, source_title="Книга2",
                          page_index=1, snippet="<mark>y</mark>", score=-1.0)]

    monkeypatch.setattr(chat_mod.llm, "chat_completion", fake)
    monkeypatch.setattr(chat_mod, "_run_search", rec_search)

    r = client.post("/api/chat", json={"message": "чем кормить при сальмонеллёзе птиц?"})
    assert r.status_code == 200
    body = r.json()
    # сначала AND (пусто), затем OR-фолбэк (нашлось)
    assert modes == [False, True]
    assert len(body["hits"]) == 1
    assert body["hits"][0]["source_id"] == 2


def test_inline_merges_or_when_and_thin(monkeypatch):
    # AND даёт мало (<3) строгих хитов → подмешиваем OR, AND-хиты первыми, дедуп
    monkeypatch.setattr(config, "LLM_TOOL_MODE", "inline")
    responses = [_final_msg("стрептококкоз"), _final_msg("Нашлось, см. ниже.")]
    calls = {"n": 0}

    def fake(messages, tools=None, tool_choice=None):
        i = calls["n"]
        calls["n"] += 1
        return responses[i]

    and_page = SearchHit(type="page", source_id=1, source_title="AND-книга",
                         page_index=4, snippet="<mark>a</mark>", score=-2.0)
    modes = []

    def rec_search(db, q, scope="all", source_id=None, limit=10, or_mode=False):
        modes.append(or_mode)
        if not or_mode:
            return [and_page]  # один AND-хит (< порога 3)
        # OR-хиты: первый дублирует AND (page 1/4), остальные новые
        return [
            and_page,
            SearchHit(type="page", source_id=21, source_title="Диссертация",
                      page_index=2, snippet="<mark>b</mark>", score=-1.0),
            SearchHit(type="disease", ref_id=7, source_title=None,
                      title="Стрептококкоз", snippet="c", score=-0.5),
        ]

    monkeypatch.setattr(chat_mod.llm, "chat_completion", fake)
    monkeypatch.setattr(chat_mod, "_run_search", rec_search)

    r = client.post("/api/chat", json={"message": "стрептококкоз"})
    assert r.status_code == 200
    body = r.json()
    # запускались оба режима: сначала AND, затем OR
    assert modes == [False, True]
    hits = body["hits"]
    # дедуп: AND-страница не повторяется → всего 3 уникальных хита
    assert len(hits) == 3
    # AND-хит первым
    assert hits[0]["source_id"] == 1
    # OR-добавки после и без дублей
    keys = [(h["type"], h.get("source_id"), h.get("page_index"), h.get("ref_id"))
            for h in hits]
    assert len(keys) == len(set(keys))
    assert hits[1]["source_id"] == 21
    assert hits[2]["type"] == "disease"


def test_inline_no_or_when_and_rich(monkeypatch):
    # AND даёт >=3 строгих хитов → OR НЕ вызывается вовсе
    monkeypatch.setattr(config, "LLM_TOOL_MODE", "inline")
    responses = [_final_msg("ампициллин собака доза"), _final_msg("Готово.")]
    calls = {"n": 0}

    def fake(messages, tools=None, tool_choice=None):
        i = calls["n"]
        calls["n"] += 1
        return responses[i]

    modes = []

    def rec_search(db, q, scope="all", source_id=None, limit=10, or_mode=False):
        modes.append(or_mode)
        return [
            SearchHit(type="page", source_id=1, source_title="К1",
                      page_index=i, snippet="<mark>x</mark>", score=-1.0)
            for i in range(3)
        ]

    monkeypatch.setattr(chat_mod.llm, "chat_completion", fake)
    monkeypatch.setattr(chat_mod, "_run_search", rec_search)

    r = client.post("/api/chat", json={"message": "доза ампициллина собаке"})
    assert r.status_code == 200
    body = r.json()
    # OR не запускался: только один (AND) вызов поиска
    assert modes == [False]
    assert len(body["hits"]) == 3


def test_health_reports_llm_configured(monkeypatch):
    monkeypatch.setattr(config, "LLM_CONFIGURED", False)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["llm_configured"] is False
