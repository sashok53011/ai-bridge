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

from core.models import offers_model

#: На такие ответы провайдера стоит переключаться дальше. Остальные 4xx —
#: вина клиента, и приставка честно возвращает их как есть.
FALLBACK_STATUSES = (429, 500, 502, 503, 504)


def enabled(entries: list[dict]) -> list[dict]:
    """Включённые провайдеры в порядке приоритета (потом по имени)."""
    return sorted(
        (entry for entry in entries if entry.get("enabled")),
        key=lambda entry: (int(entry.get("fallbackPriority") or 0), entry.get("name") or entry["id"]),
    )


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


def route(entries: list[dict], requested: str = "") -> list[dict]:
    """Цепочка попыток для запроса: [{providerId, model}, ...].

    Пустая цепочка — обслужить нечим: ни одного включённого провайдера
    с подходящей моделью.
    """
    chain = enabled(entries)
    wanted = str(requested or "").strip()
    if not chain:
        return []

    # Модель не названа: каждый включённый со своей настроенной моделью.
    if not wanted:
        return [
            {"providerId": entry["id"], "model": str(entry.get("model") or "").strip()}
            for entry in chain
            if str(entry.get("model") or "").strip()
        ]

    # Полное совпадение (включая «префикс/модель» в имени самой модели)
    # важнее разбора префикса как провайдера: у openrouter модель
    # «anthropic/claude-…» не должна уезжать к провайдеру anthropic.
    matches = [
        entry for entry in chain if offers_model(str(entry.get("model") or ""), [wanted])
    ]
    if matches:
        return [
            {"providerId": entry["id"], "model": str(entry.get("model") or "").strip()}
            for entry in matches
        ]

    # «ollama/qwen3» — явный выбор провайдера для модели не из каталога.
    prefix, slash, rest = wanted.partition("/")
    if slash and rest.strip():
        for entry in chain:
            if entry["id"] == prefix.strip().lower():
                return [{"providerId": entry["id"], "model": rest.strip()}]

    return []


def should_fallback(status: int) -> bool:
    """Переключать ли на следующего провайдера после ответа `status`."""
    return status in FALLBACK_STATUSES
