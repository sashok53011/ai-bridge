"""AI-приставка — точка входа.

Первый шаг без UI: приложение входит на сервер, забирает каталог и печатает
список. Дальше — модели, загрузка локальных моделей, статистика, и только
потом интерфейс.

Запуск:

    uv run python app.py                  # каталог с сервера и статистика
    uv run python app.py --models ollama  # модели одного провайдера
    uv run python app.py --check ollama   # проверить провайдера и отчитаться

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
from core.catalogue import STATUS_DEAD, STATUS_DISABLED, view
from core.stats import format_stats, for_line, for_provider
from providers.client import (
    NeedsKeyError,
    PocketBase,
    Provider,
    ServerError,
)
from storage.keys import KeyStore

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
            providers, updated, can_edit = server.fetch_catalogue()
            wanted = view(
                providers,
                flags={},
                viewer_id=server.user_id,
                is_superuser=can_edit,
            )

            if args.models:
                target = next((entry for entry in wanted if entry["id"] == args.models), None)
                if target is None:
                    print(f"провайдер «{args.models}» в каталоге не найден", file=sys.stderr)
                    return 2
                keys = KeyStore()
                return _print_models(str(settings["server"]), target, keys.get(target["id"]))

            if args.check:
                target = next((entry for entry in wanted if entry["id"] == args.check), None)
                if target is None:
                    print(f"провайдер «{args.check}» в каталоге не найден", file=sys.stderr)
                    return 2
                keys = KeyStore()
                return _check_provider(server, target, keys.get(target["id"]))

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
