"""Маршрутизация приставки: кому достаётся запрос и в каком порядке.

Приставка экспонирует **настроенные модели** включённых провайдеров.
Если клиент назвал модель — идём к провайдеру, у которого она настроена;
не назвал — берём первый по приоритету. Отказ провайдера (сеть, 429, 5xx)
уводит запрос к следующему включённому; 4xx — это сам запрос плох,
смена провайдера не поможет.

Имя модели можно уточнить провайдером: «ollama/qwen3» — к ollama
с моделью qwen3, даже если она не настроена в каталоге.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from core.models import offers_model

#: На такие ответы провайдера стоит переключаться дальше. 401/403 —
#: ключ провайдера X не годится, но у Y он другой и может быть валидным;
#: 429 — лимит или исчерпанная квота (OpenAI так отвечает и за то, и за
#: другое), 402 — пустой баланс у провайдеров вроде OpenRouter;
#: остальные 4xx — вина клиента, и приставка честно возвращает их как есть.
FALLBACK_STATUSES = (401, 402, 403, 429, 500, 502, 503, 504)


def is_local_url(url: str) -> bool:
    """Локальный ли адрес провайдера (своя сеть, не интернет).

    У локальных моделей (Ollama, LM Studio) первый запрос может ждать,
    пока модель загрузится в память, — до пяти минут; для таких
    провайдеров таймаут чата берётся длиннее.
    """
    host = (urlparse(str(url or "")).hostname or "").strip().lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def enabled(entries: list[dict]) -> list[dict]:
    """Включённые провайдеры в порядке приоритета (потом по имени)."""
    return sorted(
        (entry for entry in entries if entry.get("enabled")),
        key=lambda entry: (int(entry.get("fallbackPriority") or 0), entry.get("name") or entry["id"]),
    )


def request_needs(data: object) -> tuple[str, ...]:
    """Что просит запрос: ("vision",) и/или ("tools",); пусто — ничего особого.

    Распознаём стандартные признаки OpenAI-формата: сообщения с картинками
    (image_url / input_image) и инструменты (tools / tool_choice). Требование
    сохраняется при переключении провайдеров: кормить картинкой модель
    без vision — пустая трата времени.
    """
    if not isinstance(data, dict):
        return ()
    needs: list[str] = []
    if _asks_for_images(data):
        needs.append("vision")
    if _asks_for_tools(data):
        needs.append("tools")
    return tuple(needs)


def _asks_for_images(data: dict) -> bool:
    messages = data.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and str(part.get("type") or "") in (
                    "image_url",
                    "input_image",
                ):
                    return True
    return False


def _asks_for_tools(data: dict) -> bool:
    tools = data.get("tools")
    if isinstance(tools, list) and tools:
        return True
    return bool(data.get("tool_choice"))


def _capable(entry: dict, needs: tuple[str, ...]) -> bool:
    """Есть ли у записи все нужные способности (текст «Способности»)."""
    caps = str(entry.get("capabilities") or "").lower()
    return all(word in caps for word in needs)


def advertised_models(entries: list[dict]) -> list[dict]:
    """Модели, которые приставка предлагает клиентам.

    Это настроенные модели включённых провайдеров; при совпадении моделей
    побеждает провайдер с более высоким приоритетом.
    """
    seen: dict[str, str] = {}
    for entry in enabled(entries):
        model = str(entry.get("model") or "").strip()
        if model and model not in seen:
            seen[model] = entry["id"]
    return [{"id": model, "owned_by": provider_id} for model, provider_id in seen.items()]


def route(entries: list[dict], requested: str = "", needs: tuple[str, ...] = ()) -> list[dict]:
    """Цепочка попыток для запроса: [{providerId, model}, ...].

    ``needs`` — требования самого запроса (vision/tools): когда модель
    отказала и мы переключаемся, дальше по приоритету идут только модели
    с нужными способностями — кормить картинкой модель без vision глупо.

    Пустая цепочка — обслужить нечим: ни одного включённого провайдера
    с подходящей моделью.
    """
    chain = enabled(entries)
    wanted = str(requested or "").strip()
    if not chain:
        return []

    def attempt(entry: dict) -> dict:
        return {"providerId": entry["id"], "model": str(entry.get("model") or "").strip()}

    # Модель не названа: каждый включённый со своей настроенной моделью;
    # при наличии требований — только подходящие по способностям.
    if not wanted:
        pool = [entry for entry in chain if str(entry.get("model") or "").strip()]
        if needs:
            pool = [entry for entry in pool if _capable(entry, needs)]
        return [attempt(entry) for entry in pool]

    # Полное совпадение (включая «префикс/модель» в имени самой модели)
    # важнее разбора префикса как провайдера: у openrouter модель
    # «anthropic/claude-…» не должна уезжать к провайдеру anthropic.
    matches = [
        entry for entry in chain if offers_model(str(entry.get("model") or ""), [wanted])
    ]

    # «ollama/qwen3» — явный выбор провайдера для модели не из каталога.
    if not matches:
        prefix, slash, rest = wanted.partition("/")
        if slash and rest.strip():
            for entry in chain:
                if entry["id"] == prefix.strip().lower():
                    return [{"providerId": entry["id"], "model": rest.strip()}]

    attempts = [attempt(entry) for entry in matches]
    seen = {item["providerId"] for item in attempts}

    # Требование запроса сохраняется при переключении: дальше по
    # приоритету — только модели с нужными способностями.
    if needs:
        for entry in chain:
            if entry["id"] in seen or not str(entry.get("model") or "").strip():
                continue
            if _capable(entry, needs):
                attempts.append(attempt(entry))
                seen.add(entry["id"])
    return attempts


def should_fallback(status: int) -> bool:
    """Переключать ли на следующего провайдера после ответа `status`."""
    return status in FALLBACK_STATUSES
