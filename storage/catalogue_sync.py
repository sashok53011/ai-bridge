"""Синхронизация каталога провайдеров в отдельный SQLite-файл.

Каталог живёт на сервере и приходит по API; здесь мы держим локальную
копию в ``catalogue.db``, чтобы SQL Playground показывала провайдеров.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Колонки таблицы ``providers`` — поля каталога.
_COLUMNS = (
    "id TEXT PRIMARY KEY",
    "name TEXT",
    "enabled INTEGER",
    "url TEXT",
    "model TEXT",
    "fallbackPriority INTEGER",
    "loginUrl TEXT",
    "getApiKeyUrl TEXT",
    "logoUrl TEXT",
    "logoAsset TEXT",
    "status TEXT",
    "origin TEXT",
    "authorName TEXT",
    "pricing TEXT",
    "context TEXT",
    "capabilities TEXT",
    "infoUrl TEXT",
)


def sync_catalogue(data_dir: str | Path, providers: list[dict]) -> None:
    """Записать каталог в ``<data_dir>/catalogue.db``.

    Полная замена: старые строки удаляются, новые вставляются.
    Ошибка не должна ронять службу — печатаем и выходим.
    """
    db_path = Path(data_dir).expanduser() / "catalogue.db"
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS providers ("
                + ", ".join(_COLUMNS)
                + ")"
            )
            conn.execute("DELETE FROM providers")
            for entry in providers:
                conn.execute(
                    "INSERT INTO providers VALUES ("
                    + ",".join("?" * len(_COLUMNS))
                    + ")",
                    (
                        str(entry.get("id") or ""),
                        str(entry.get("name") or ""),
                        1 if entry.get("enabled") else 0,
                        str(entry.get("url") or ""),
                        str(entry.get("model") or ""),
                        int(entry.get("fallbackPriority") or 0),
                        str(entry.get("loginUrl") or ""),
                        str(entry.get("getApiKeyUrl") or ""),
                        str(entry.get("logoUrl") or ""),
                        str(entry.get("logoAsset") or ""),
                        str(entry.get("status") or "active"),
                        str(entry.get("origin") or "admin"),
                        str(entry.get("authorName") or ""),
                        str(entry.get("pricing") or ""),
                        str(entry.get("context") or ""),
                        str(entry.get("capabilities") or ""),
                        str(entry.get("infoUrl") or ""),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        print(f"каталог не записан: {exc}", flush=True)
