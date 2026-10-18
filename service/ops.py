"""Служебные действия приставки: то, что делает и CLI, и веб-страница.

Здесь живёт оркестрация — «сеть + хранилище + ядро», — которой делятся
консоль и браузер. Каждое действие возвращает структурированный
итог (код выхода, тексты, запись), а не печатает: CLI печатает своё,
страница — своё.

Правила, которые нельзя потерять при переносе:

  * суперпользо­вателю флаги на сервер не отправляются (PUT заменил бы
    каталог), а каталог целиком он шлёт из **свежего серверного** списка —
    устаревший или пустой список унёс бы чужие записи в корзину
    (так 16.10.2026 страница потеряла каталог за одну правку);
  * 401/403 без ключа — просьба ввести ключ, а не поломка: в статистику
    такой итог не уходит;
  * обычный пользователь убирает только своё предложение.
"""

from __future__ import annotations

from core.check import judge
from core.catalogue import (
    STATUS_DEAD,
    can_edit_model,
    can_remove,
    edit_entry as edit_entry_data,
    next_priority,
    propose,
    set_model as set_model_data,
    view,
    with_local_enabled,
)
from core.stats import format_stats, for_provider
from providers.client import (
    NeedsKeyError,
    PocketBase,
    Provider,
    ServerError,
)
from storage.keys import KeyStore
from storage.snapshot import Snapshot


def merge_view(
    server: PocketBase,
    providers: list[dict],
    *,
    can_edit: bool,
    server_flags: dict[str, bool],
    snapshot: Snapshot | None = None,
    viewer_id: str | None = None,
) -> list[dict]:
    """Каталог глазами одного человека: сервер + локальные флаги и выжившие.

    ``snapshot`` — чей снапшот читать и писать (по умолчанию — общий
    устройственный); ``viewer_id`` — кто смотрит (по умолчанию — тот,
    за кем залогинен сервер): от этого зависит, какие записи «мои».
    Итог сохраняется в снапшот — перезапуск не теряет решений человека.
    """
    store = snapshot or Snapshot()
    local = store.load()
    flags = {
        **server_flags,
        **{entry["id"]: bool(entry.get("enabled")) for entry in local},
    }
    listed = view(
        providers,
        flags=flags,
        viewer_id=viewer_id or server.user_id,
        is_superuser=can_edit,
    )
    wanted = with_local_enabled(listed, local)
    store.save(wanted)
    return wanted


def set_enabled(
    server: PocketBase,
    wanted: list[dict],
    ids: list[str],
    *,
    enabled: bool,
    can_edit: bool,
    snapshot: Snapshot | None = None,
    sync: bool = True,
) -> dict:
    """Включает или выключает провайдеров. Возвращает итог.

    ``sync=False`` — хранить флаг только на устройстве, не отправлять
    на сервер (личные переключатели людей: их серверные флаги — это
    история старого проекта, а здесь человек и так вошёл своим аккаунтом).
    """
    by_id = {entry["id"]: entry for entry in wanted}
    unknown = [provider_id for provider_id in ids if provider_id not in by_id]
    if unknown:
        return {
            "code": 2,
            "changed": [],
            "error": (
                f"не найдено: {', '.join(unknown)} — возможно, запись скрыта "
                "(помечена неработающей и не включалась)"
            ),
            "note": None,
        }

    merged = [
        {
            **entry,
            "enabled": enabled if entry["id"] in ids else bool(entry.get("enabled")),
        }
        for entry in wanted
    ]
    (snapshot or Snapshot()).save(merged)
    changed = [(provider_id, by_id[provider_id]["name"]) for provider_id in ids]

    if can_edit:
        return {
            "code": 0,
            "changed": changed,
            "error": None,
            "note": "переключатель сохранён на устройстве: у суперпользо­вателя сервер флаги не хранит",
        }

    if not sync:
        return {
            "code": 0,
            "changed": changed,
            "error": None,
            "note": "переключатель сохранён в вашем пространстве",
        }

    try:
        server.save_flags(merged)
    except ServerError as exc:
        return {
            "code": 1,
            "changed": changed,
            "error": f"на сервер не ушло ({exc}) — переключатель сохранён на устройстве",
            "note": None,
        }
    return {"code": 0, "changed": changed, "error": None, "note": "переключатели отправлены на сервер"}


def check(
    server: PocketBase,
    provider: dict,
    key: str,
    *,
    make_provider=Provider,
    counter: bool = True,
    report_for: str = "",
) -> dict:
    """Проверка провайдера: список моделей, итог, отчёт и счётчик.

    Ключ уходит только к провайдеру; серверу — лишь итог проверки.
    ``counter=False`` пропускает лишний запрос за свежим счётчиком —
    им пользуется фоновая проверка, где счётчику всё равно некому
    смотреться (строку печатает сам раунд). ``report_for`` — чьим
    именем записать успех в статистику сообщества (по умолчанию —
    суперпользо­ватель приставки).
    """
    if provider.get("status") == STATUS_DEAD:
        return {
            "code": 2,
            "dead": "помечена неработающей — проверять нельзя",
            "outcome": None,
            "report_error": None,
            "counter": None,
        }

    models: list[str] | None = None
    error = ""
    needs_key = False
    try:
        models = [entry["name"] for entry in make_provider(provider["url"]).fetch_models(key)]
    except NeedsKeyError:
        needs_key = True
    except ServerError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - показать причину важнее типа исключения
        error = f"не получилось связаться ({exc})"

    outcome = judge(
        models,
        configured=provider.get("model", ""),
        error=error,
        needs_key=needs_key,
    )
    code = 0 if outcome["ok"] else (3 if not outcome["report"] else 1)

    report_error = None
    if outcome["report"]:
        try:
            server.report_stats(
                provider_id=provider["id"],
                model=outcome["model"],
                ok=outcome["ok"],
                error=outcome["error"],
                for_user=report_for,
            )
        except ServerError as exc:
            report_error = str(exc)
            code = 1

    fresh_counter = None
    if counter and outcome["report"] and report_error is None:
        try:
            counters = for_provider(server.fetch_stats(), provider["id"])
            fresh_counter = format_stats(counters.get(outcome["model"]))
        except ServerError:
            fresh_counter = None  # счётчик — украшение; проверка уже состоялась

    return {
        "code": code,
        "dead": None,
        "outcome": outcome,
        "report_error": report_error,
        "counter": fresh_counter,
    }


def add(
    server: PocketBase,
    wanted: list[dict],
    providers: list[dict],
    *,
    name: str,
    url: str,
    model: str,
    provider_id: str = "",
    priority: int | None = None,
    login_url: str = "",
    key_url: str = "",
    key: str = "",
    can_edit: bool,
    snapshot: Snapshot | None = None,
    key_store: KeyStore | None = None,
    sync: bool = True,
    author_id: str = "",
    details: dict | None = None,
) -> dict:
    """Добавляет провайдера: человеку — в личное пространство, админу — в каталог.

    Суперпользо­вателю каталог уходит целиком из **свежего серверного**
    списка (его PUT заменяет каталог). ``sync=False`` — запись живёт только
    на устройстве (личное пространство человека), сервер не трогается.
    ``details`` — дополнительные поля определения (справка, логотипы…).
    """
    raw: dict = {"name": name, "url": url, "model": model}
    if details:
        raw.update(details)
    if provider_id:
        raw["id"] = provider_id
    if priority is not None:
        raw["fallbackPriority"] = priority
    if login_url:
        raw["loginUrl"] = login_url
    if key_url:
        raw["getApiKeyUrl"] = key_url

    entry, error = propose(raw)
    if entry is None:
        return {"code": 2, "error": error, "entry": None, "note": None}

    if author_id:
        entry = {**entry, "authorId": author_id}

    if any(existing["id"] == entry["id"] for existing in wanted):
        return {
            "code": 2,
            "error": f"id «{entry['id']}» уже занят — укажите свой через --id",
            "entry": None,
            "note": None,
        }

    if can_edit:
        # Админский PUT заменяет каталог целиком — база только свежая:
        # устаревший или пустой список унёс бы чужие записи в корзину.
        try:
            providers, _, _, _ = server.fetch_catalogue()
        except ServerError as exc:
            return {
                "code": 1,
                "error": f"каталог не прочитался ({exc}) — не добавляю",
                "entry": None,
                "note": None,
            }

    if priority is None:
        entry["fallbackPriority"] = next_priority(providers)

    # Автору своё сразу нужно: включаем и показываем с пометкой «ваш».
    entry = {**entry, "enabled": True, "mine": True}
    if can_edit:
        # Админ публикует сразу: без пометки «предложение».
        entry["origin"] = "admin"
        entry["mine"] = False

    merged = [*wanted, entry]
    (snapshot or Snapshot()).save(merged)
    if key:
        (key_store or KeyStore()).set(entry["id"], key)

    if not sync:
        # Личное пространство человека: сервер не трогается вовсе.
        return {
            "code": 0,
            "error": None,
            "entry": entry,
            "note": "добавлено в ваше пространство — видят только вы",
        }

    try:
        if can_edit:
            server.push_catalogue([*providers, entry])
            note = "добавлено в каталог — видят все"
        else:
            server.save_flags(merged)
            note = "предложение отправлено — видят только вы и администратор"
    except ServerError as exc:
        return {
            "code": 1,
            "error": f"на сервер не ушло ({exc}) — запись сохранена на устройстве",
            "entry": entry,
            "note": None,
        }
    return {"code": 0, "error": None, "entry": entry, "note": note}


def set_model(
    server: PocketBase,
    wanted: list[dict],
    providers: list[dict],
    provider_id: str,
    model: str,
    *,
    can_edit: bool,
    snapshot: Snapshot | None = None,
) -> dict:
    """Меняет настроенную модель: админ — в каталоге, человек — в своём.

    Сервер определения чужих записей не меняет — команда отказывается
    заранее, вместо ложного «изменено».
    """
    by_id = {entry["id"]: entry for entry in wanted}
    target = by_id.get(provider_id)
    if target is None:
        return {"code": 2, "error": f"провайдер «{provider_id}» не найден", "entry": None}

    if not can_edit_model(target, is_superuser=can_edit):
        return {
            "code": 2,
            "error": (
                f"«{target['name']}» — запись каталога, модель меняет только "
                "администратор (можно своё предложение)"
            ),
            "entry": None,
        }

    updated, error = set_model_data(wanted, provider_id, model)
    if updated is None:
        return {"code": 2, "error": error, "entry": None}

    new_entry = next(entry for entry in updated if entry["id"] == provider_id)
    (snapshot or Snapshot()).save(updated)

    try:
        if can_edit:
            # В каталог — свежий серверный список с заменой одной записи;
            # локальные выжившие в каталог не тащим (их там нет). Записи
            # нет на сервере — правленая добавляется, а не теряется.
            current, _, _, _ = server.fetch_catalogue()
            sent = [
                new_entry if entry["id"] == provider_id else entry
                for entry in current
            ]
            if all(entry["id"] != provider_id for entry in current):
                sent.append(new_entry)
            server.push_catalogue(sent)
        else:
            server.save_flags(updated)
    except ServerError as exc:
        return {
            "code": 1,
            "error": f"на сервер не ушло ({exc}) — на устройстве модель изменена",
            "entry": new_entry,
        }
    return {"code": 0, "error": None, "entry": new_entry}


def edit(
    server: PocketBase,
    wanted: list[dict],
    providers: list[dict],
    provider_id: str,
    changes: dict,
    *,
    can_edit: bool,
    snapshot: Snapshot | None = None,
) -> dict:
    """Правит определение провайдера: админ — каталог, человек — своё предложение.

    Пустые поля формы не стирают значения — правка не должна молча
    ломать то, что человек не трогал.
    """
    by_id = {entry["id"]: entry for entry in wanted}
    target = by_id.get(provider_id)
    if target is None:
        return {"code": 2, "error": f"провайдер «{provider_id}» не найден", "entry": None}

    if not can_edit_model(target, is_superuser=can_edit):
        return {
            "code": 2,
            "error": (
                f"«{target['name']}» — запись каталога, правит только "
                "администратор (можно своё предложение)"
            ),
            "entry": None,
        }

    updated, error = edit_entry_data(wanted, provider_id, changes)
    if updated is None:
        return {"code": 2, "error": error, "entry": None}

    new_entry = next(entry for entry in updated if entry["id"] == provider_id)
    (snapshot or Snapshot()).save(updated)

    try:
        if can_edit:
            # В каталог — свежий серверный список с заменой одной записи;
            # устаревший или пустой список унёс бы чужие записи в корзину.
            # Записи нет на сервере — правленая добавляется, а не теряется.
            current, _, _, _ = server.fetch_catalogue()
            sent = [
                new_entry if entry["id"] == provider_id else entry
                for entry in current
            ]
            if all(entry["id"] != provider_id for entry in current):
                sent.append(new_entry)
            server.push_catalogue(sent)
        else:
            server.save_flags(updated)
    except ServerError as exc:
        return {
            "code": 1,
            "error": f"на сервер не ушло ({exc}) — на устройстве запись изменена",
            "entry": new_entry,
        }
    return {"code": 0, "error": None, "entry": new_entry}


def remove(
    server: PocketBase,
    wanted: list[dict],
    providers: list[dict],
    provider_id: str,
    *,
    can_edit: bool,
    snapshot: Snapshot | None = None,
    key_store: KeyStore | None = None,
) -> dict:
    """Убирает провайдера: админу — из каталога в корзину, человеку — своё.

    Запись каталога обычный пользователь убрать не может — сервер бы её
    сохранил, а команда соврала бы «удалено».
    """
    by_id = {entry["id"]: entry for entry in wanted}
    target = by_id.get(provider_id)
    if target is None:
        return {"code": 2, "error": f"не найдено: {provider_id}", "name": None, "note": None}

    if not can_remove(target, is_superuser=can_edit):
        return {
            "code": 2,
            "error": (
                f"«{target['name']}» — запись каталога, убрать её может только "
                "администратор; выключить можно: --disable"
            ),
            "name": target["name"],
            "note": None,
        }

    remaining = [entry for entry in wanted if entry["id"] != provider_id]
    (snapshot or Snapshot()).save(remaining)
    (key_store or KeyStore()).delete(provider_id)

    try:
        if can_edit:
            # Свежий серверный список: PUT заменяет каталог целиком.
            current, _, _, _ = server.fetch_catalogue()
            server.push_catalogue(
                [entry for entry in current if entry["id"] != provider_id]
            )
            note = "убрано из каталога (в корзине, восстановимо)"
        else:
            server.save_flags(remaining)
            note = "предложение убрано"
    except ServerError as exc:
        return {
            "code": 1,
            "error": f"на сервер не ушло ({exc}) — на устройстве запись убрана",
            "name": target["name"],
            "note": None,
        }
    return {"code": 0, "error": None, "name": target["name"], "note": note}
