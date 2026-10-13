"""AI-приставка — точка входа.

Первый шаг без UI: приложение входит на сервер, забирает каталог и печатает
список. Дальше — модели, загрузка локальных моделей, статистика, и только
потом интерфейс.

Запуск:

    uv run python app.py                  # каталог с сервера и статистика
    uv run python app.py --models ollama  # модели одного провайдера
    uv run python app.py --check ollama   # проверить провайдера и отчитаться
    uv run python app.py --pull ollama    # скачать модель в локальный сервер
    uv run python app.py --serve          # открыть локальный HTTP-сервис

Логин и пароль берутся из переменных AI_BRIDGE_EMAIL / AI_BRIDGE_PASSWORD
(или полей email/password в config.toml). В репозиторий они не попадают.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from core.check import judge
from core.catalogue import (
    STATUS_DEAD,
    STATUS_DISABLED,
    next_priority,
    propose,
    view,
    with_local_enabled,
)
from core.progress import PullTracker, describe
from core.stats import format_stats, for_line, for_provider
from providers.client import (
    NeedsKeyError,
    PocketBase,
    Provider,
    ServerError,
)
from storage.keys import KeyStore
from storage.snapshot import Snapshot

_MARKS = {
    STATUS_DEAD: "[нераб.]",
    STATUS_DISABLED: "[выкл.]",
}


def _configure_console() -> None:
    """На Windows консоль по умолчанию в cp1251, и русский текст ломается."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def load_settings() -> dict:
    """config.toml рядом со скриптом; секреты — только из окружения."""
    settings: dict = {"server": "https://app.devhorizon.online"}
    path = Path(__file__).with_name("config.toml")
    if path.exists():
        with path.open("rb") as handle:
            settings.update(tomllib.load(handle))
    return settings


def _print_catalogue(
    providers: list[dict],
    *,
    updated: str,
    can_edit: bool,
    stats: dict | None = None,
) -> None:
    if not providers:
        print("каталог пуст")
        return

    now = datetime.now(timezone.utc)
    rows: list[tuple[str, str, str, str]] = []
    for entry in providers:
        counter = for_line(stats or {}, entry["id"], entry.get("model", ""))
        label = format_stats(counter, now=now) or ""
        mark = _MARKS.get(entry["status"], "")
        if entry.get("enabled"):
            mark = f"{mark} [вкл]" if mark else "[вкл]"
        rows.append((entry["name"], mark, label, entry["url"]))

    name_width = max(len(name) for name, _, _, _ in rows)
    mark_width = max(len(mark) for _, mark, _, _ in rows)
    label_width = max(len(label) for _, _, label, _ in rows)

    # Статистика — наверху строки: сколько человек смогли пользоваться.
    for name, mark, label, url in rows:
        print(
            f"{name:<{name_width}}  {mark:<{mark_width}}  "
            f"{label:<{label_width}}  {url}"
        )

    role = "суперпользо­ватель" if can_edit else "обычный пользователь"
    print()
    print(f"Всего: {len(providers)} · обновлён: {updated or '—'} · вы вошли как {role}")


def _find_target(providers: list[dict], provider_id: str) -> dict | None:
    """Одна запись каталога по id; жалуется сам, если не нашлось."""
    target = next((entry for entry in providers if entry["id"] == provider_id), None)
    if target is None:
        print(f"провайдер «{provider_id}» в каталоге не найден", file=sys.stderr)
    return target


def _print_models(server_url: str, provider: dict, key: str) -> int:
    """Модели одного провайдера. Возвращает код выхода."""
    try:
        models = Provider(provider["url"]).fetch_models(key)
    except ServerError as exc:
        print(f"{provider['name']}: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - показать причину важнее, чем тип исключения
        print(f"{provider['name']}: не получилось связаться ({exc})")
        return 1

    print(f"{provider['name']}: {len(models)} моделей")
    for model in models:
        chosen = " ← настроена" if model["name"] == provider.get("model") else ""
        print(f"  {model['name']}{chosen}")
    return 0


def _check_provider(server: PocketBase, provider: dict, key: str) -> int:
    """«Кнопка проверки»: список моделей, итог и рост счётчика.

    Ключ остаётся на устройстве: на сервер уходит только итог проверки,
    как и записано в правиле продукта.
    """
    if provider.get("status") == STATUS_DEAD:
        # Правило продукта: помечена неработающей — пользоваться можно,
        # но проверять нельзя.
        print(f"{provider['name']}: помечена неработающей — проверять нельзя")
        return 2

    models: list[str] | None = None
    error = ""
    needs_key = False

    try:
        models = [entry["name"] for entry in Provider(provider["url"]).fetch_models(key)]
    except NeedsKeyError:
        needs_key = True
    except ServerError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - показать причину важнее, чем тип исключения
        error = f"не получилось связаться ({exc})"

    outcome = judge(
        models,
        configured=provider.get("model", ""),
        error=error,
        needs_key=needs_key,
    )

    if outcome["ok"]:
        detail = f"модель {outcome['model']}" if outcome["model"] else "модели есть"
        print(f"{provider['name']}: работает · {detail}")
    else:
        print(f"{provider['name']}: {outcome['error']}")

    if not outcome["report"]:
        # Нужен ключ — введём его и проверим снова; отчёт не растёт.
        return 3

    try:
        server.report_stats(
            provider_id=provider["id"],
            model=outcome["model"],
            ok=outcome["ok"],
            error=outcome["error"],
        )
    except ServerError as exc:
        print(f"отчёт не отправился: {exc}", file=sys.stderr)
        return 1

    print("отчёт отправлен")
    _print_community_counter(server, provider["id"], outcome["model"])
    return 0 if outcome["ok"] else 1


def _print_community_counter(server: PocketBase, provider_id: str, model: str) -> None:
    """Свежий счётчик сообщества после отчёта: сколько человек смогли.

    Если статистика не пришла — не беда: отчёт уже отправлен.
    """
    try:
        counters = for_provider(server.fetch_stats(), provider_id)
    except ServerError:
        return
    label = format_stats(counters.get(model))
    if label:
        print(f"счётчик сообщества: {label}")


def _pull_model(provider: dict, model: str, key: str, *, timeout: float = 180.0) -> int:
    """«Кнопка загрузки»: качает модель в локальный сервер (Ollama).

    Большую модель грузится минутами, поэтому человек должен видеть ход
    и иметь возможность остановить — Ctrl+C не ломает ничего: поток
    закрывается, сервер остаётся целым.
    """
    if provider.get("status") == STATUS_DEAD:
        print(f"{provider['name']}: помечена неработающей — загрузка недоступна")
        return 2

    print(f"{provider['name']}: загружаю {model} (остановить — Ctrl+C)")
    tracker = PullTracker()
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    last_line = ""

    try:
        for event in Provider(provider["url"]).pull_model(model, api_key=key, timeout=timeout):
            snapshot = tracker.add(event)
            line = describe(snapshot)
            if not line or line == last_line:
                continue
            if tty:
                # Одна строка, которая обновляется на месте.
                sys.stdout.write(f"\r{line}{' ' * max(0, len(last_line) - len(line))}")
                sys.stdout.flush()
            else:
                print(line)
            last_line = line
    except KeyboardInterrupt:
        if tty and last_line:
            sys.stdout.write("\n")
        print("остановлено вручную")
        return 130
    except ServerError as exc:
        if tty and last_line:
            sys.stdout.write("\n")
        print(f"{provider['name']}: {exc}")
        return 1

    if tty and last_line:
        sys.stdout.write("\n")

    snapshot = tracker.snapshot()
    if snapshot.error:
        print(f"{provider['name']}: {snapshot.error}")
        return 1
    if not snapshot.done:
        print(f"{provider['name']}: сервер не подтвердил успех")
        return 1

    print(f"{provider['name']}: {model} готова")
    return 0


def _merge_and_store(
    server: PocketBase,
    providers: list[dict],
    *,
    can_edit: bool,
    server_flags: dict[str, bool],
) -> list[dict]:
    """Сводит каталог с локальным снапшотом и сохраняет итог.

    Флаги: локальное решение сильнее серверного (устройство могло
    переключить офлайн), серверные помогают на новом устройстве.
    Включённое человеком запись не удаляется никогда — даже если каталог
    её больше не отдаёт.
    """
    local = Snapshot().load()
    flags = {
        **server_flags,
        **{entry["id"]: bool(entry.get("enabled")) for entry in local},
    }
    listed = view(providers, flags=flags, viewer_id=server.user_id, is_superuser=can_edit)
    wanted = with_local_enabled(listed, local)
    Snapshot().save(wanted)
    return wanted


def _set_enabled(
    server: PocketBase,
    wanted: list[dict],
    ids: list[str],
    *,
    enabled: bool,
    can_edit: bool,
) -> int:
    """«Тумблер»: включает или выключает провайдеров.

    Локально решение применяется сразу; на сервер уходит только для
    обычного пользователя — у суперпользо­вателя сервер флаги не хранит
    (PUT заменил бы каталог, а флагов в каталоге нет вовсе).
    """
    by_id = {entry["id"]: entry for entry in wanted}
    unknown = [provider_id for provider_id in ids if provider_id not in by_id]
    if unknown:
        print(
            f"не найдено: {', '.join(unknown)} — возможно, запись скрыта "
            "(помечена неработающей и не включалась)",
            file=sys.stderr,
        )
        return 2

    merged = [
        {**entry, "enabled": enabled if entry["id"] in ids else bool(entry.get("enabled"))}
        for entry in wanted
    ]
    Snapshot().save(merged)

    word = "включён" if enabled else "выключен"
    for provider_id in ids:
        print(f"{by_id[provider_id]['name']}: {word}")

    if can_edit:
        print("переключатель сохранён на устройстве: у суперпользо­вателя сервер флаги не хранит")
        return 0

    try:
        server.save_flags(merged)
    except ServerError as exc:
        print(
            f"на сервер не ушло ({exc}) — переключатель сохранён на устройстве",
            file=sys.stderr,
        )
        return 1

    print("переключатели отправлены на сервер")
    return 0


def _serve(
    server: PocketBase,
    providers: list[dict],
    *,
    can_edit: bool,
    server_flags: dict[str, bool],
    host: str,
    port: int,
    timeout: float,
) -> int:
    """Запускает локальный HTTP-сервис: другие приложения ходят в приставку.

    Каталог обновляется в фоне (раз в 5 минут), а переключатели читаются
    из снапшота на каждый запрос — `--enable/--disable` подхватывается
    сразу, без перезапуска сервиса.
    """
    import threading
    import time

    from service.gateway import serve as run_server

    state = {"providers": providers, "server_flags": server_flags}

    def refresh_loop() -> None:
        while True:
            time.sleep(300)
            try:
                fresh, _, _, flags = server.fetch_catalogue()
                state["providers"] = fresh
                state["server_flags"] = flags
            except ServerError as exc:
                # Каталог — украшение; без него работают включённые записи.
                print(f"каталог не обновился: {exc}", file=sys.stderr)

    def entries() -> list[dict]:
        local = Snapshot().load()
        flags = {
            **state["server_flags"],
            **{entry["id"]: bool(entry.get("enabled")) for entry in local},
        }
        listed = view(
            state["providers"],
            flags=flags,
            viewer_id=server.user_id,
            is_superuser=can_edit,
        )
        return with_local_enabled(listed, local)

    threading.Thread(target=refresh_loop, daemon=True).start()
    return run_server(entries, KeyStore(), host=host, port=port, timeout=timeout)


def _add_provider(
    server: PocketBase,
    wanted: list[dict],
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
) -> int:
    """«Добавить провайдера»: обычный пользователь добавляет предложение,
    суперпользо­ватель — запись в каталог для всех.

    Предложение видят только автор и администратор (он же — публикатор).
    Ключ, если дали, остаётся на устройстве — на сервер он не уходит.
    """
    raw: dict = {"name": name, "url": url, "model": model}
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
        print(error, file=sys.stderr)
        return 2

    # Свой id должен быть свободен: чужую запись мы не трогаем.
    if any(existing["id"] == entry["id"] for existing in wanted):
        print(
            f"id «{entry['id']}» уже занят — укажите свой через --id",
            file=sys.stderr,
        )
        return 2

    if priority is None:
        entry["fallbackPriority"] = next_priority(wanted)

    # Автору своё сразу нужно: включаем и показываем с пометкой «ваш».
    entry = {**entry, "enabled": True, "mine": True}
    if can_edit:
        # Админ публикует сразу: без пометки «предложение».
        entry["origin"] = "admin"
        entry["mine"] = False

    merged = [*wanted, entry]
    Snapshot().save(merged)
    if key:
        KeyStore().set(entry["id"], key)

    try:
        if can_edit:
            server.push_catalogue(merged)
            print(f"{entry['name']}: добавлено в каталог — видят все")
        else:
            server.save_flags(merged)
            print(f"{entry['name']}: предложение отправлено — видят только вы и администратор")
    except ServerError as exc:
        print(
            f"на сервер не ушло ({exc}) — запись сохранена на устройстве",
            file=sys.stderr,
        )
        return 1

    print(f"id: {entry['id']} · модель: {entry['model']}")
    if key:
        print("ключ сохранён на устройстве")
    print(f"проверить: uv run python app.py --check {entry['id']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(description="AI-приставка: каталог AI-провайдеров")
    parser.add_argument(
        "--models",
        metavar="ID",
        help="показать модели одного провайдера (например: ollama)",
    )
    parser.add_argument(
        "--check",
        metavar="ID",
        help="проверить провайдера и отправить отчёт статистики (например: ollama)",
    )
    parser.add_argument(
        "--pull",
        nargs="+",
        metavar="ID [MODEL]",
        help="скачать модель в локальный сервер (например: ollama llama3.2)",
    )
    parser.add_argument(
        "--enable",
        nargs="+",
        metavar="ID",
        help="включить провайдеров (например: --enable ollama openai)",
    )
    parser.add_argument(
        "--disable",
        nargs="+",
        metavar="ID",
        help="выключить провайдеров (например: --disable ollama)",
    )
    parser.add_argument(
        "--add",
        nargs=3,
        metavar=("ИМЯ", "АДРЕС", "МОДЕЛЬ"),
        help="добавить провайдера: обычному — предложение, админу — в каталог",
    )
    parser.add_argument(
        "--id",
        default="",
        metavar="ID",
        help="свой id для --add (по умолчанию — составляется из имени)",
    )
    parser.add_argument(
        "--priority",
        type=int,
        default=None,
        metavar="N",
        help="приоритет для --add (по умолчанию — следующий свободный)",
    )
    parser.add_argument(
        "--login-url",
        default="",
        metavar="URL",
        help="страница логина для --add",
    )
    parser.add_argument(
        "--key-url",
        default="",
        metavar="URL",
        help="где получить API-ключ, для --add",
    )
    parser.add_argument(
        "--key",
        default="",
        metavar="КЛЮЧ",
        help="API-ключ для --add: останется на устройстве, на сервер не уйдёт",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="запустить локальный HTTP-сервис (OpenAI-совместимый)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        metavar="АДРЕС",
        help="адрес локального сервиса, по умолчанию 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        metavar="ПОРТ",
        help="порт локального сервиса, по умолчанию 8080",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        metavar="СЕК",
        help="таймаут запроса к провайдеру, по умолчанию 180",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    email = os.environ.get("AI_BRIDGE_EMAIL", "") or str(settings.get("email", ""))
    password = os.environ.get("AI_BRIDGE_PASSWORD", "") or str(settings.get("password", ""))
    if not email or not password:
        print(
            "нет логина: задай AI_BRIDGE_EMAIL и AI_BRIDGE_PASSWORD",
            file=sys.stderr,
        )
        return 2

    try:
        with PocketBase(str(settings["server"])) as server:
            server.login(email, password)
            providers, updated, can_edit, server_flags = server.fetch_catalogue()
            wanted = _merge_and_store(
                server, providers, can_edit=can_edit, server_flags=server_flags
            )

            if args.enable:
                return _set_enabled(server, wanted, args.enable, enabled=True, can_edit=can_edit)

            if args.disable:
                return _set_enabled(server, wanted, args.disable, enabled=False, can_edit=can_edit)

            if args.add:
                name, url, model = args.add
                return _add_provider(
                    server,
                    wanted,
                    name=name,
                    url=url,
                    model=model,
                    provider_id=args.id,
                    priority=args.priority,
                    login_url=args.login_url,
                    key_url=args.key_url,
                    key=args.key,
                    can_edit=can_edit,
                )

            if args.serve:
                return _serve(
                    server,
                    providers,
                    can_edit=can_edit,
                    server_flags=server_flags,
                    host=args.host,
                    port=args.port,
                    timeout=args.timeout,
                )

            if args.models:
                target = _find_target(wanted, args.models)
                if target is None:
                    return 2
                keys = KeyStore()
                return _print_models(str(settings["server"]), target, keys.get(target["id"]))

            if args.check:
                target = _find_target(wanted, args.check)
                if target is None:
                    return 2
                keys = KeyStore()
                return _check_provider(server, target, keys.get(target["id"]))

            if args.pull:
                provider_id = args.pull[0]
                target = _find_target(wanted, provider_id)
                if target is None:
                    return 2
                model = args.pull[1] if len(args.pull) > 1 else target["model"]
                if not model:
                    print(
                        f"у провайдера «{provider_id}» не настроена модель — "
                        "назовите её явно: --pull ollama llama3.2",
                        file=sys.stderr,
                    )
                    return 2
                keys = KeyStore()
                return _pull_model(target, model, keys.get(target["id"]), timeout=args.timeout)

            # Статистика — украшение каталога; без неё каталог всё так же честен.
            try:
                stats = server.fetch_stats()
            except ServerError:
                stats = None

            _print_catalogue(wanted, updated=updated, can_edit=can_edit, stats=stats)
            return 0
    except ServerError as exc:
        print(f"сервер: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
