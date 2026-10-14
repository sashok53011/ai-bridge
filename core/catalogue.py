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

import re

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
        # Справочная информация о модели — помогает выбирать экономную:
        # цена (текстом, как её пишут провайдеры), контекст и способности.
        "pricing": _text(entry.get("pricing")),
        "context": _text(entry.get("context")),
        "capabilities": _text(entry.get("capabilities")),
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


def propose(raw: object) -> tuple[dict | None, str]:
    """Новое предложение провайдера: (запись, "") или (None, причина).

    Повторяет требования сервера: записи нужны имя, адрес и модель —
    без них сервер молча отбрасывает заявку. ``id`` берётся из данных
    или составляет имени; чужие id не трогаем — это чужие записи.
    """
    if not isinstance(raw, dict):
        return None, "ожидались данные записи"

    name = _text(raw.get("name"))
    if not name:
        return None, "нужно имя — без него запись никому не понятна"

    url = _text(raw.get("url"))
    if not url:
        return None, "без адреса сервер отбросит запись"
    if not url.startswith(("http://", "https://")):
        return None, "адрес должен начинаться с http:// или https://"

    model = _text(raw.get("model"))
    if not model:
        return None, "нужна модель — без неё сервер отбросит запись"

    provider_id = _text(raw.get("id"))
    if not provider_id:
        provider_id = _slug(name)
        if not provider_id:
            return None, f"не получилось составить id из имени «{name}» — укажите --id"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", provider_id):
        return None, f"id «{provider_id}» подходит не весь: нужны буквы, цифры, точка, дефис"

    entry = normalize(
        {
            "id": provider_id,
            "name": name,
            "url": url,
            "model": model,
            "fallbackPriority": raw.get("fallbackPriority"),
            "loginUrl": raw.get("loginUrl"),
            "getApiKeyUrl": raw.get("getApiKeyUrl"),
            "status": STATUS_ACTIVE,
            # Предложение всегда идёт от автора; админ публикует его позже.
            "origin": "user",
        }
    )
    assert entry is not None  # id и адрес уже проверены
    return entry, ""


def _slug(name: str) -> str:
    """Читаемый id из имени: «Мой Ollama» -> «my-ollama» (сервер любит ASCII)."""
    lowered = name.lower()
    # Транслитерация основных букв, чтобы у русских имён остался осмысленный id.
    table = str.maketrans(
        {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
         "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
         "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
         "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
         "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"}
    )
    transliterated = lowered.translate(table)
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return slug[:64].strip("-")


def can_remove(entry: object, *, is_superuser: bool) -> bool:
    """Можно ли убрать запись из этого каталога.

    Суперпользо­ватель убирает любую запись — она уходит в корзину и
    восстановима. Обычный пользователь убирает только своё предложение:
    запись каталога сервер не удалит (он её сохранит), и команда
    солгала бы человеку, сказав «удалено».
    """
    if is_superuser:
        return True
    return _is_own_proposal(entry)


def can_edit_model(entry: object, *, is_superuser: bool) -> bool:
    """Можно ли менять настроенную модель этой записи.

    Админ — любую (это его каталог). Обычный пользователь — только своё
    предложение: сервер определения чужих записей не меняет, и команда
    не должна солгать, сказав «модель изменена».
    """
    if is_superuser:
        return True
    return _is_own_proposal(entry)


def _is_own_proposal(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("origin") == "user"
        and bool(entry.get("mine"))
    )


def set_model(
    entries: list[dict], provider_id: str, model: str
) -> tuple[list[dict] | None, str]:
    """Меняет настроенную модель провайдера: (новый список, "") или (None, причина).

    Новая модель — та, что пойдёт провайдеру по умолчанию, когда клиент
    её не назвал; она же стоит в каталоге и в /v1/models без префикса.
    """
    model = model.strip()
    if not model:
        return None, "модель не может быть пустой"
    if not any(entry.get("id") == provider_id for entry in entries):
        return None, f"провайдер «{provider_id}» не найден"
    return [
        {**entry, "model": model} if entry.get("id") == provider_id else entry
        for entry in entries
    ], ""


#: Поля определения, которые правятся в форме и через CLI.
EDITABLE_FIELDS = (
    "name",
    "url",
    "model",
    "fallbackPriority",
    "loginUrl",
    "getApiKeyUrl",
    "pricing",
    "context",
    "capabilities",
)


def edit_entry(
    entries: list[dict], provider_id: str, changes: dict
) -> tuple[list[dict] | None, str]:
    """Правит определение записи: (новый список, "") или (None, причина).

    ``changes`` — только изменяемые поля (пустые значения отбрасываются:
    правка не должна молча стирать то, что человек не трогал). Итог
    прогоняется через normalize — те же требования, что у сервера:
    без имени и адреса запись жить не будет.
    """
    fields = {
        name: value
        for name, value in changes.items()
        if name in EDITABLE_FIELDS and str(value).strip()
    }
    if "fallbackPriority" in fields:
        try:
            fields["fallbackPriority"] = max(0, int(fields["fallbackPriority"]))
        except (TypeError, ValueError):
            return None, "приоритет должен быть числом"
    if not fields:
        return None, "нечего менять — все поля пустые"

    target = next((entry for entry in entries if entry.get("id") == provider_id), None)
    if target is None:
        return None, f"провайдер «{provider_id}» не найден"

    candidate = normalize({**target, **fields})
    if candidate is None:
        return None, "после правки записи не хватает имени или адреса"

    return [
        {**candidate, **{k: v for k, v in target.items() if k not in candidate}}
        if entry.get("id") == provider_id
        else entry
        for entry in entries
    ], ""


def next_priority(entries: list[dict]) -> int:
    """Следующий свободный приоритет — как в веб-панели: в конец списка."""
    top = max(((_int(entry.get("fallbackPriority"), 0) for entry in entries)), default=0)
    return top + 1


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
