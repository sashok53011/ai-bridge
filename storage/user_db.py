"""Персональные SQLite-базы для SQL Playground: провайдеры, модели, токены.

Каждый пользователь получает свою ``learning.db`` с реальными FOREIGN KEY,
чтобы можно было изучать SQL (JOIN, 1:N, N:M) на реальных данных.
Админ видит все базы, пользователь — только свою.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT,
    enabled INTEGER DEFAULT 1,
    fallbackPriority INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    origin TEXT DEFAULT 'admin',
    pricing TEXT,
    context TEXT,
    capabilities TEXT,
    loginUrl TEXT,
    getApiKeyUrl TEXT,
    logoUrl TEXT,
    logoAsset TEXT,
    authorName TEXT,
    infoUrl TEXT
);

CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    context TEXT,
    pricing TEXT,
    capabilities TEXT,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT,
    role TEXT DEFAULT 'user',
    verified INTEGER DEFAULT 0,
    created TEXT,
    updated TEXT
);

CREATE TABLE IF NOT EXISTS app_tokens (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    userId TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT,
    tail TEXT,
    created TEXT
);

CREATE TABLE IF NOT EXISTS routing_lists (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    models TEXT,
    defaultModel TEXT,
    ownerId TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
);
"""


def db_path_for(user_id: str, root: str | Path) -> Path:
    """Путь к learning.db конкретного пользователя."""
    return Path(root) / "users" / str(user_id) / "learning.db"


def available_dbs(root: str | Path, *, is_admin: bool, user_id: str = "") -> list[dict]:
    """Список доступных баз: [{path, label, owner}]."""
    root = Path(root)
    result: list[dict] = []
    if is_admin:
        # Системные базы.
        cat_db = root / "catalogue.db"
        if cat_db.is_file():
            result.append({"path": str(cat_db), "label": "catalogue (каталог)", "owner": "system"})
        # Персональные базы всех пользователей.
        users_dir = root / "users"
        if users_dir.is_dir():
            for user_dir in sorted(users_dir.iterdir()):
                db = user_dir / "learning.db"
                if db.is_file():
                    result.append({
                        "path": str(db),
                        "label": f"user {user_dir.name}",
                        "owner": user_dir.name,
                    })
    else:
        # Только своя база.
        db = db_path_for(user_id, root)
        if db.is_file():
            result.append({"path": str(db), "label": f"user {user_id} (ваша)", "owner": user_id})
    return result


def sync_user_db(
    db_file: Path,
    providers: list[dict],
    user_id: str = "",
    source_db: Path | None = None,
) -> None:
    """Создать/обновить персональную learning.db."""
    try:
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_file), timeout=5.0)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.executescript(_SCHEMA)

            # Providers + models.
            conn.execute("DELETE FROM models")
            conn.execute("DELETE FROM providers")
            for entry in providers:
                pid = str(entry.get("id") or "")
                if not pid:
                    continue
                conn.execute(
                    "INSERT INTO providers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        pid,
                        str(entry.get("name") or ""),
                        str(entry.get("url") or ""),
                        1 if entry.get("enabled") else 0,
                        int(entry.get("fallbackPriority") or 0),
                        str(entry.get("status") or "active"),
                        str(entry.get("origin") or "admin"),
                        str(entry.get("pricing") or ""),
                        str(entry.get("context") or ""),
                        str(entry.get("capabilities") or ""),
                        str(entry.get("loginUrl") or ""),
                        str(entry.get("getApiKeyUrl") or ""),
                        str(entry.get("logoUrl") or ""),
                        str(entry.get("logoAsset") or ""),
                        str(entry.get("authorName") or ""),
                        str(entry.get("infoUrl") or ""),
                    ),
                )
                model_name = str(entry.get("model") or "").strip()
                if model_name:
                    conn.execute(
                        "INSERT OR IGNORE INTO models VALUES (?,?,?,?,?,?,?)",
                        (f"{pid}/{model_name}", pid, model_name,
                         str(entry.get("context") or ""),
                         str(entry.get("pricing") or ""),
                         str(entry.get("capabilities") or ""), 1),
                    )

            # Копия users + tokens + lists из PocketBase.
            if source_db and source_db.is_file():
                src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
                try:
                    conn.execute("DELETE FROM users")
                    for row in src.execute("SELECT id, email, name, verified, created, updated FROM users"):
                        conn.execute(
                            "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                            (row[0], row[1], row[2] or "", "user", row[3], row[4], row[5]),
                        )
                    conn.execute("DELETE FROM app_tokens")
                    for row in src.execute("SELECT id, token, userId, name, tail, created FROM app_tokens"):
                        conn.execute("INSERT INTO app_tokens VALUES (?,?,?,?,?,?)", row)
                    conn.execute("DELETE FROM routing_lists")
                    for row in src.execute("SELECT id, code, name, models, defaultModel, ownerId FROM routing_lists"):
                        conn.execute("INSERT INTO routing_lists VALUES (?,?,?,?,?,?)", row)
                finally:
                    src.close()

            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        print(f"sync_user_db {db_file.name}: {exc}", flush=True)
