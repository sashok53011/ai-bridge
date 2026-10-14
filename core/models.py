"""Список моделей провайдера.

Провайдеры отвечают по-разному: OpenAI-совместимые серверы отдают
``{"data": [{"id": "..."}]}``, а Ollama — ``{"models": [{"name": "..."}]}``.
Здесь оба варианта сводятся к одному списку имён.
"""

from __future__ import annotations

import json


def extract_models(payload: object) -> list[str]:
    """Имена моделей из ответа провайдера; пустой список, если не вышло."""
    found: list[str] = []

    def collect(items: object, *keys: str) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                for key in keys:
                    value = item.get(key)
                    if value is not None and str(value).strip():
                        found.append(str(value).strip())
                        break
            elif isinstance(item, str) and item.strip():
                found.append(item.strip())

    if isinstance(payload, dict):
        collect(payload.get("data"), "id", "name")
        if not found:
            collect(payload.get("models"), "name", "id")

    # Порядок не гарантирован, но людям удобнее, когда список отсортирован.
    return sorted(set(found))


def offers_model(configured: str, models: list[str]) -> bool:
    """Есть ли настроенная модель в списке.

    Провайдеры часто добавляют префикс или пространство имён
    (``openai/gpt-4o`` против ``gpt-4o``), поэтому сравниваем и по концу строки.
    """
    wanted = configured.strip().lower()
    if not wanted or not models:
        return False

    lowered = [model.lower() for model in models]
    if wanted in lowered:
        return True

    return any(model.endswith("/" + wanted) or model.endswith(":" + wanted) for model in lowered)


def union_models(
    configured: list[dict],
    live_by_provider: dict[str, list[str]],
) -> list[dict]:
    """Объединённый список моделей приставки: настроенные + все live.

    Настроенные модели включённых провайдеров идут **без префикса** —
    это то, что клиенты используют по умолчанию. Остальное, что провайдер
    реально отдаёт в своём /models, — с префиксом ``providerid/имя``,
    который приставка понимает как «закрепи за этим провайдером».

    Коллизии имени решаются в пользу настроенной модели; полные
    дубликаты (один и тот же live у двух провайдеров) дедуплицируются.
    """
    result: list[dict] = []
    seen: set[str] = set()
    known_by_provider: dict[str, set[str]] = {}

    # Настроенные — без префикса, они главные.
    for entry in configured:
        provider_id = str(entry.get("id") or "")
        model = str(entry.get("model") or "").strip()
        if not provider_id or not model:
            continue
        if model in seen:
            continue
        seen.add(model)
        known_by_provider.setdefault(provider_id, set()).add(model.lower())
        result.append({"id": model, "owned_by": provider_id})

    # Live-списки — с префиксом, чтобы коллизии имён не смешивались.
    for provider_id in sorted(live_by_provider):
        known = known_by_provider.setdefault(provider_id, set())
        for model in live_by_provider[provider_id]:
            model = str(model).strip()
            if not model or model.lower() in known:
                continue
            prefixed = f"{provider_id}/{model}"
            if prefixed in seen:
                continue
            seen.add(prefixed)
            known.add(model.lower())
            result.append({"id": prefixed, "owned_by": provider_id})

    return result


def parse_ndjson(text: str) -> list[dict]:
    """Разбирает поток NDJSON (строка JSON на строку) — пригодится для
    прогресса загрузки локальных моделей и любых потоковых ответов."""
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events
