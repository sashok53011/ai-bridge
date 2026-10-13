"""AI-приставка — точка входа.

Первый шаг без UI: приложение входит на сервер, забирает каталог и печатает
список. Дальше — модели, загрузка локальных моделей, статистика, и только
потом интерфейс.

Запуск:

    uv run python app.py                 # каталог с сервера
    uv run python app.py --models ollama # модели одного провайдера

Логин и пароль берутся из переменных AI_BRIDGE_EMAIL / AI_BRIDGE_PASSWORD
(или полей email/password в config.toml). В репозиторий они не попадают.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

from core.catalogue import STATUS_ACTIVE, STATUS_DEAD, STATUS_DISABLED, view
from providers.client import PocketBase, Provider, ServerError
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


def _print_catalogue(providers: list[dict], *, updated: str, can_edit: bool) -> None:
    if not providers:
        print("каталог пуст")
        return

    width = max(len(entry["name"]) for entry in providers)
    for entry in providers:
        mark = _MARKS.get(entry["status"], "")
        print(f"{entry['name']:<{width}}  {mark:<9} {entry['url']}")

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


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = argparse.ArgumentParser(description="AI-приставка: каталог AI-провайдеров")
    parser.add_argument(
        "--models",
        metavar="ID",
        help="показать модели одного провайдера (например: ollama)",
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
    except ServerError as exc:
        print(f"сервер: {exc}", file=sys.stderr)
        return 1

    if args.models:
        wanted = view(providers, flags={}, viewer_id=server.user_id, is_superuser=can_edit)
        target = next((entry for entry in wanted if entry["id"] == args.models), None)
        if target is None:
            print(f"провайдер «{args.models}» в каталоге не найден", file=sys.stderr)
            return 2
        keys = KeyStore()
        return _print_models(str(settings["server"]), target, keys.get(target["id"]))

    listed = view(providers, flags={}, viewer_id=server.user_id, is_superuser=can_edit)
    _print_catalogue(listed, updated=updated, can_edit=can_edit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
