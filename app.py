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
    view,
)
from core.progress import PullTracker, describe
from core.stats import format_stats, for_line
from providers.client import (
    PocketBase,
    Provider,
    ServerError,
)
from service import ops
from storage.keys import KeyStore
from storage.snapshot import Snapshot
from storage import token as token_lib

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
    """Настройки: config.toml рядом со скриптом, поверх — окружение.

    Секреты (логин, пароль) живут только в окружении или config.toml —
    в репозиторий они не попадают. Переменная окружения сильнее файла:
    так служба на сервере переопределяет адрес каталога без правки файлов.
    """
    settings: dict = {"server": "https://app.devhorizon.online"}
    path = Path(__file__).with_name("config.toml")
    if path.exists():
        with path.open("rb") as handle:
            settings.update(tomllib.load(handle))
    if os.environ.get("AI_BRIDGE_SERVER", "").strip():
        settings["server"] = os.environ["AI_BRIDGE_SERVER"].strip()
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

    Вся логика — в service/ops (ею же пользуется страница), здесь только
    печать. Ключ остаётся на устройстве: на сервер уходит только итог.
    """
    result = ops.check(server, provider, key)

    if result["dead"]:
        print(f"{provider['name']}: {result['dead']}")
        return result["code"]

    outcome = result["outcome"]
    if outcome["ok"]:
        detail = f"модель {outcome['model']}" if outcome["model"] else "модели есть"
        print(f"{provider['name']}: работает · {detail}")
    else:
        print(f"{provider['name']}: {outcome['error']}")

    if outcome["report"]:
        if result["report_error"]:
            print(f"отчёт не отправился: {result['report_error']}", file=sys.stderr)
        else:
            print("отчёт отправлен")
            if result["counter"]:
                print(f"счётчик сообщества: {result['counter']}")
    return result["code"]


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
    """Каталог глазами устройства (логика — в service/ops)."""
    return ops.merge_view(
        server, providers, can_edit=can_edit, server_flags=server_flags
    )


def _set_enabled(
    server: PocketBase,
    wanted: list[dict],
    ids: list[str],
    *,
    enabled: bool,
    can_edit: bool,
) -> int:
    """«Тумблер»: логика в service/ops, здесь только печать."""
    result = ops.set_enabled(server, wanted, ids, enabled=enabled, can_edit=can_edit)

    word = "включён" if enabled else "выключен"
    for _, name in result["changed"]:
        print(f"{name}: {word}")

    if result["error"]:
        print(result["error"], file=sys.stderr)
    if result["note"]:
        print(result["note"])
    return result["code"]


def _serve(
    server: PocketBase,
    providers: list[dict],
    *,
    can_edit: bool,
    server_flags: dict[str, bool],
    host: str,
    port: int,
    timeout: float,
    token: str,
    page_auth: bool = False,
) -> int:
    """Запускает локальный HTTP-сервис: другие приложения ходят в приставку,
    а человек следит за ней со страницы в браузере.

    Каталог обновляется в фоне (раз в 5 минут), переключатели читаются
    из снапшота на каждый запрос — `--enable/--disable` подхватывается
    сразу, без перезапуска службы. Live-списки моделей кэшируются
    шлюзом; `token` — токен приставки для /v1/* (пусто — без проверки).
    """
    import threading
    import time

    from service.gateway import serve as run_server
    from service.webui import WebUi

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
        return ops.merge_view(
            server,
            state["providers"],
            can_edit=can_edit,
            server_flags=state["server_flags"],
        )

    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    # Страница закрыта токеном, если привязка не-loopback (LAN) или
    # человек явно попросил (--page-auth — для туннелей и прокси,
    # которые достучатся до loopback).
    ui = WebUi(
        entries,
        KeyStore(),
        server,
        can_edit=can_edit,
        bound_host=host,
        require_token=page_auth or not is_loopback,
        token=token,
    )

    threading.Thread(target=refresh_loop, daemon=True).start()
    return run_server(
        entries, KeyStore(), host=host, port=port, timeout=timeout, ui=ui, token=token
    )


def _remove_provider(
    server: PocketBase,
    wanted: list[dict],
    providers: list[dict],
    provider_id: str,
    *,
    can_edit: bool,
) -> int:
    """«Убрать провайдера»: логика в service/ops, здесь только печать."""
    result = ops.remove(
        server, wanted, providers, provider_id, can_edit=can_edit
    )

    if result["error"]:
        print(result["error"], file=sys.stderr)
        return result["code"]
    print(f"{result['name']}: {result['note']}")
    return result["code"]


def _add_provider(
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
) -> int:
    """«Добавить провайдера»: логика в service/ops, здесь только печать."""
    result = ops.add(
        server,
        wanted,
        providers,
        name=name,
        url=url,
        model=model,
        provider_id=provider_id,
        priority=priority,
        login_url=login_url,
        key_url=key_url,
        key=key,
        can_edit=can_edit,
    )

    if result["entry"] is None:
        print(result["error"], file=sys.stderr)
        return result["code"]

    entry = result["entry"]
    if result["error"]:
        print(result["error"], file=sys.stderr)
        return result["code"]

    print(f"{entry['name']}: {result['note']}")
    print(f"id: {entry['id']} · модель: {entry['model']}")
    if key:
        print("ключ сохранён на устройстве")
    print(f"проверить: uv run python app.py --check {entry['id']}")
    return result["code"]


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
        "--remove",
        metavar="ID",
        help="убрать провайдера: админу — из каталога в корзину, человеку — своё предложение",
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
    parser.add_argument(
        "--token",
        action="store_true",
        help="показать действующий токен приставки (для /v1/* и страницы на LAN)",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="отключить проверку токена на /v1/* (только для чисто локального использования)",
    )
    parser.add_argument(
        "--page-auth",
        action="store_true",
        help="требовать вход по токену на странице, даже на loopback (для туннелей и прокси)",
    )
    args = parser.parse_args(argv)

    settings = load_settings()

    if args.token:
        token, source = token_lib.resolve(
            from_env=os.environ.get("AI_BRIDGE_TOKEN", ""),
            from_config=str(settings.get("token", "")),
        )
        print(token)
        print(f"источник: {source}", file=sys.stderr)
        return 0
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
                    providers,
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

            if args.remove:
                return _remove_provider(
                    server, wanted, providers, args.remove, can_edit=can_edit
                )

            if args.serve:
                token_value = "" if args.no_auth else token_lib.resolve(
                    from_env=os.environ.get("AI_BRIDGE_TOKEN", ""),
                    from_config=str(settings.get("token", "")),
                )[0]
                return _serve(
                    server,
                    providers,
                    can_edit=can_edit,
                    server_flags=server_flags,
                    host=args.host,
                    port=args.port,
                    timeout=args.timeout,
                    token=token_value,
                    page_auth=args.page_auth,
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
