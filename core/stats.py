"""Статистика тестов: сколько пользователей справилось, а сколько нет.

Смысл показателя — не «сколько раз проверили», а «сколько человек смогли
пользоваться». Поэтому считаются именно пользователи, а не попытки.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _parse_when(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def has_data(stats: object) -> bool:
    """Были ли вообще какие-то отчёты по этой модели."""
    if not isinstance(stats, dict):
        return False
    return bool(stats.get("usersOk") or stats.get("usersFail") or stats.get("ok") or stats.get("fail"))


def format_stats(stats: object, *, now: datetime | None = None) -> str | None:
    """«5 (2) · 12.10 21:15» — успешно, с ошибками (в скобках) и когда.

    Возвращает None, если отчётов не было: пустое место честнее нуля.
    """
    if not has_data(stats):
        return None
    assert isinstance(stats, dict)

    ok = int(stats.get("usersOk") or stats.get("ok") or 0)
    fail = int(stats.get("usersFail") or stats.get("fail") or 0)

    label = f"{ok} ({fail})" if fail else f"{ok}"

    moment = _parse_when(stats.get("last"))
    if moment is None:
        return label

    if now is not None:
        # Всё, что старше недели, показываем без времени — оно уже не важно.
        if (now - moment).days >= 7:
            return f"{label} · {moment:%d.%m}"

    return f"{label} · {moment:%d.%m %H:%M}"


def for_provider(payload: object, provider_id: str) -> dict[str, dict]:
    """Счётчики всех моделей провайдера: {имя модели: {...}}.

    ``payload`` — сырой ответ сервера ``GET /api/ai-stats``; всё, что не
    похоже на счётчики, отбрасывается — каталог честнее без статистики,
    чем с мусором.
    """
    if not isinstance(payload, dict):
        return {}
    entry = payload.get(provider_id)
    if not isinstance(entry, dict):
        return {}
    models = entry.get("models")
    if not isinstance(models, dict):
        return {}
    return {str(name): value for name, value in models.items() if isinstance(value, dict)}


def totals(models: dict[str, dict]) -> dict:
    """Сумма по всем моделям — сколько человек смогли пользоваться
    провайдером целиком; время берётся самое свежее из моделей."""
    ok = 0
    fail = 0
    last_moment: datetime | None = None
    last_status = ""

    for value in models.values():
        ok += int(value.get("usersOk") or value.get("ok") or 0)
        fail += int(value.get("usersFail") or value.get("fail") or 0)
        moment = _parse_when(value.get("last"))
        if moment is not None and (last_moment is None or moment > last_moment):
            last_moment = moment
            last_status = str(value.get("lastStatus") or "")

    total: dict = {"ok": ok, "fail": fail, "usersOk": ok, "usersFail": fail}
    if last_moment is not None:
        total["last"] = last_moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        total["lastStatus"] = last_status
    return total


def for_line(payload: object, provider_id: str, configured: str = "") -> dict | None:
    """Счётчик для строки каталога: настроенная модель, иначе сумма.

    Человеку важнее его модель; у провайдера без настройки — общая картина.
    """
    models = for_provider(payload, provider_id)
    if not models:
        return None
    current = models.get(configured)
    if configured and isinstance(current, dict):
        return current
    return totals(models)


def merge_report(current: dict | None, *, ok: bool, when: datetime) -> dict:
    """Новый отчёт поверх старого: счётчики растут, последнее — свежее.

    Сервер хранит и общие счётчики, и последний статус; здесь считается
    та же арифметика, чтобы устройство могло показать результат сразу,
    не дожидаясь ответа сервера.
    """
    base = dict(current or {})
    users_ok = int(base.get("usersOk") or base.get("ok") or 0)
    users_fail = int(base.get("usersFail") or base.get("fail") or 0)

    if ok:
        users_ok += 1
    else:
        users_fail += 1

    return {
        "ok": users_ok,
        "fail": users_fail,
        "usersOk": users_ok,
        "usersFail": users_fail,
        "last": when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "lastStatus": "ok" if ok else "fail",
    }
