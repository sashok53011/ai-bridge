"""Каталог провайдеров: что показывать и что делать при синхронизации.

Правила product'а, которые уже приняты и проверены в старом клиенте:

  * включённые человеком записи не удаляются никогда — даже если каталог
    их больше не отдаёт;
  * помеченные как неработающие (``dead``) и выключенные для всех
    (``disabled_for_all``) скрыты, пока пользователь их не включил;
  * включённые помеченные показываются с бейджем, чтобы их можно было
    выключить;
  * предложения (``origin="user"``) видит только их автор и суперпользо­ватель.
"""

from __future__ import annotations

STATUS_ACTIVE = "active"
STATUS_DEAD = "dead"
STATUS_DISABLED = "disabled_for_all"

_STATUSES = (STATUS_ACTIVE, STATUS_DEAD, STATUS_DISABLED)


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def normalize(entry: object) -> dict | None:
    """Приводит запись каталога к виду, который понимает клиент.

    Возвращает None для бесполезной записи — нет ни id, ни адреса. Так сервер
    отбрасывает мусор, и при импорте каталога с устройства надо делать то же.
    """
    if not isinstance(entry, dict):
        return None

    provider_id = _text(entry.get("id"))
    url = _text(entry.get("url"))
    if not provider_id or not url:
        return None

    status = entry.get("status")
    if status not in _STATUSES:
        status = STATUS_ACTIVE

    origin = entry.get("origin")
    if origin not in ("admin", "user"):
        origin = "admin"

    return {
        "id": provider_id,
        "name": _text(entry.get("name")) or provider_id,
        "url": url,
        "model": _text(entry.get("model")),
        "fallbackPriority": _int(entry.get("fallbackPriority")),
        "loginUrl": _text(entry.get("loginUrl")),
        "getApiKeyUrl": _text(entry.get("getApiKeyUrl")),
        "logoUrl": _text(entry.get("logoUrl")),
        "logoAsset": _text(entry.get("logoAsset")),
        "status": status,
        "origin": origin,
        "authorId": _text(entry.get("authorId")),
        "authorName": _text(entry.get("authorName")),
    }


def view(
    catalogue: list[dict],
    flags: dict[str, bool],
    *,
    viewer_id: str = "",
    is_superuser: bool = False,
) -> list[dict]:
    """Каталог глазами одного пользователя.

    ``flags`` — переключатели «включён/выключён», которые хранятся на
    устройстве и отправляются на сервер. Каждая запись получает поля
    ``enabled`` и ``mine``.
    """
    result: list[dict] = []

    for raw in catalogue:
        entry = normalize(raw)
        if entry is None:
            continue

        enabled = bool(flags.get(entry["id"], False))
        mine = entry["origin"] == "user" and entry["authorId"] == viewer_id

        if not is_superuser and entry["origin"] == "user" and not mine:
            continue

        if (
            not is_superuser
            and entry["status"] in (STATUS_DEAD, STATUS_DISABLED)
            and not enabled
        ):
            continue

        result.append({**entry, "enabled": enabled, "mine": mine})

    return result


def reconcile(local: list[dict], remote: list[dict]) -> list[dict]:
    """Что остаётся на устройстве после синхронизации.

    ``local`` — то, что было у человека (включая его переключатели),
    ``remote`` — свежий каталог с сервера. Возвращает новый список,
    ничего не меняя в переданных.
    """
    remote_by_id = {entry["id"]: entry for entry in remote if "id" in entry}
    result: list[dict] = []

    # Включённое человеком не трогаем: если каталог его больше не отдаёт,
    # оно всё равно остаётся, пока человек сам его не выключит.
    for entry in local:
        if not entry.get("enabled"):
            continue
        current = remote_by_id.get(entry["id"], entry)
        result.append({**current, "enabled": True})

    kept_ids = {entry["id"] for entry in result}

    # Выключенные берутся из каталога; помеченные мёртвые или выключенные
    # для всех в список не попадают.
    for entry in remote:
        if entry["id"] in kept_ids:
            continue
        if entry["status"] != STATUS_ACTIVE:
            continue
        result.append({**entry, "enabled": False})

    return sorted(
        result,
        key=lambda entry: (entry.get("fallbackPriority", 0), entry["name"]),
    )


def with_local_enabled(listed: list[dict], local: list[dict]) -> list[dict]:
    """Каталог глазами устройства: что отдал сервер плюс то, что человек
    включил сам и чего каталог больше не отдаёт.

    ``listed`` — свежий каталог с сервера (с переключателями), ``local`` —
    последний сохранённый на устройстве список. Два правила:

    * **включённое человеком не удаляется никогда** — запись, которой нет
      в каталоге, но которую человек включил, остаётся, пока он сам её
      не выключит;
    * **флаг с устройства сильнее серверного** — решение, принятое на этом
      устройстве (хоть включением, хоть выключением), переживает синхронизацию;
      флаги сервера нужны только тем записям, которых устройство ещё не знало.

    Данные (адрес, модель) всегда берутся из ``listed``: каталог новее снапшота.
    """
    local_by_id = {entry["id"]: entry for entry in local}
    seen: set[str] = set()
    result: list[dict] = []

    for entry in listed:
        seen.add(entry["id"])
        copy = local_by_id.get(entry["id"])
        if copy is not None and "enabled" in copy:
            enabled = bool(copy["enabled"])
        else:
            enabled = bool(entry.get("enabled"))
        result.append({**entry, "enabled": enabled})

    # Включённое человеком переживает исчезновение из каталога.
    for entry in local:
        if entry["id"] not in seen and entry.get("enabled"):
            result.append({**entry, "enabled": True})

    return sorted(
        result,
        key=lambda entry: (entry.get("fallbackPriority", 0), entry["name"]),
    )
