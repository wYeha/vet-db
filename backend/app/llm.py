"""OpenAI-совместимый клиент чат-модели (router.ai).

Клиент читает конфиг из :mod:`app.config` (всё за env). Секреты (ключ) в код
не попадают и НЕ логируются: заголовок Authorization нигде не печатается.

Единственная публичная функция — :func:`chat_completion`. Если модель не
сконфигурирована (нет ключа/URL/модели), бросается :class:`LLMNotConfigured`,
которую роутер маппит в HTTP 503.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from . import config

log = logging.getLogger("vetai.llm")


class LLMNotConfigured(RuntimeError):
    """Чат-модель не сконфигурирована (нет ключа/URL/модели)."""


class LLMError(RuntimeError):
    """Ошибка обращения к чат-модели (сеть/таймаут/HTTP не-2xx)."""


class LLMBadRequest(LLMError):
    """Не-ретраибельная ошибка провайдера (HTTP 4xx: ключ, лимит запроса и т.п.)."""


# сколько раз повторить при сетевой/5xx-ошибке (backoff 1-2)
_MAX_RETRIES = 2


def chat_completion(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    *,
    tool_choice: Optional[str] = None,
) -> Dict[str, Any]:
    """Один вызов chat/completions. Возвращает JSON ответа модели как dict.

    Бросает :class:`LLMNotConfigured`, если модель не настроена, и
    :class:`LLMError` при сетевых/HTTP-ошибках после ретраев.
    """
    if not config.LLM_CONFIGURED:
        raise LLMNotConfigured("чат-ассистент не сконфигурирован (LLM_* не заданы)")

    payload: Dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "max_tokens": config.LLM_MAX_TOKENS,
        "temperature": config.LLM_TEMPERATURE,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    if config.LLM_DISABLE_REASONING:
        # deepseek-flash — reasoning-модель: без отключения весь max_tokens уходит
        # в reasoning, content приходит пустым. Для наших коротких grounded-ответов
        # рассуждения не нужны — так дешевле и content не теряется.
        payload["reasoning"] = {"enabled": False}

    headers = {
        # Ключ берём только из конфига; заголовок нигде не логируем.
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{config.LLM_BASE_URL}/chat/completions"

    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=config.LLM_TIMEOUT) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code >= 500:
                # серверная ошибка — имеет смысл ретраить
                raise LLMError(f"LLM HTTP {resp.status_code}")
            if resp.status_code >= 400:
                # клиентская ошибка (401/400/…) — ретрай бесполезен, выходим сразу.
                # Тело провайдера не пробрасываем наружу (может содержать лишнее).
                raise LLMBadRequest(f"LLM HTTP {resp.status_code}")
            return resp.json()
        except LLMBadRequest:
            # не ретраим клиентские ошибки
            raise
        except (httpx.TimeoutException, httpx.TransportError, LLMError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                sleep_s = attempt + 1  # backoff 1, 2
                log.warning("LLM call failed (attempt %d), retry in %ds: %s",
                            attempt + 1, sleep_s, exc)
                time.sleep(sleep_s)
                continue
            break

    raise LLMError(f"LLM request failed: {last_exc}")
