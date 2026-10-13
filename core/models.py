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
