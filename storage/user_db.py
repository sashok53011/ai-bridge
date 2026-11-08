"""Персональные SQLite-базы для SQL Playground: провайдеры, модели, токены.

Каждый пользователь получает свою ``learning.db`` с реальными FOREIGN KEY,
чтобы можно было изучать SQL (JOIN, 1:N, N:M) на реальных данных.
Админ видит все базы, пользователь — только свою.
"""

from __future__ import annotations

import json
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

CREATE TABLE IF NOT EXISTS list_models (
    listId TEXT NOT NULL REFERENCES routing_lists(id) ON DELETE CASCADE,
    modelId TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    PRIMARY KEY (listId, modelId)
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


def _read_users(src: sqlite3.Connection) -> dict[str, tuple]:
    """Пользователи из ``users`` **и** ``_superusers`` — иначе FK падает.

    Токены и списки маршрутизации ссылаются на людей из обеих коллекций
    PocketBase, а админ-аккаунты живут только в ``_superusers``. Без них
    вставка ``app_tokens`` нарушала FOREIGN KEY и вся синхронизация
    откатывалась (в логе: FOREIGN KEY constraint failed).
    """
    found: dict[str, tuple] = {}
    for table, fallback_role in (("users", "user"), ("_superusers", "admin")):
        try:
            cols = [row[1] for row in src.execute(f"PRAGMA table_info({table})")]
        except sqlite3.Error:
            continue
        if not cols or "id" not in cols or "email" not in cols:
            continue
        name_c = "name" if "name" in cols else "''"
        role_c = (
            f"COALESCE(NULLIF(role, ''), '{fallback_role}')" if "role" in cols
            else f"'{fallback_role}'"
        )
        ver_c = "verified" if "verified" in cols else "0"
        created_c = "created" if "created" in cols else "''"
        updated_c = "updated" if "updated" in cols else "''"
        try:
            rows = src.execute(
                f"SELECT id, email, {name_c}, {role_c}, {ver_c}, {created_c}, {updated_c} "
                f"FROM {table}"
            ).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            found.setdefault(
                str(row[0]),
                (row[0], row[1], row[2] or "", row[3], row[4], row[5], row[6]),
            )
    return found


def _fill_list_models(conn: sqlite3.Connection) -> None:
    """Связь N:M «список маршрутизации ↔ модели» из поля ``models``.

    В ``routing_lists.models`` лежит список идентификаторов моделей
    (JSON-массив или строка через запятую); каждая строка, которая
    находит свою модель, попадает в связующую таблицу ``list_models``.
    Непонятные значения молча пропускаем — база не должна падать из-за
    мусора в старых записях.
    """
    conn.execute("DELETE FROM list_models")
    for list_id, raw in conn.execute(
        "SELECT id, models FROM routing_lists"
    ).fetchall():
        text = str(raw or "").strip()
        if not text:
            continue
        items: list[str] = []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                items = [str(x) for x in parsed]
        if not items:
            items = [part.strip() for part in text.split(",") if part.strip()]
        for item in items:
            found = conn.execute(
                "SELECT id FROM models WHERE id = ? OR name = ? LIMIT 1",
                (item, item),
            ).fetchone()
            if found:
                conn.execute(
                    "INSERT OR IGNORE INTO list_models (listId, modelId) VALUES (?, ?)",
                    (list_id, found[0]),
                )


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

            # Сначала «дети», потом «родители»: иначе FK падает и весь
            # набор откатывается (в логе было FOREIGN KEY constraint failed).
            conn.execute("DELETE FROM list_models")
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
                    owners = _read_users(src)
                    conn.execute("DELETE FROM list_models")
                    conn.execute("DELETE FROM app_tokens")
                    conn.execute("DELETE FROM routing_lists")
                    conn.execute("DELETE FROM users")
                    for row in owners.values():
                        conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)", row)
                    skipped_tokens = 0
                    for row in src.execute(
                        "SELECT id, token, userId, name, tail, created FROM app_tokens"
                    ):
                        # Токен без владельца (нет ни в users, ни в _superusers)
                        # ломает FK — не вставляем его, а не роняем всю синхронизацию.
                        if str(row[2]) not in owners:
                            skipped_tokens += 1
                            continue
                        conn.execute("INSERT INTO app_tokens VALUES (?,?,?,?,?,?)", row)
                    skipped_lists = 0
                    for row in src.execute(
                        "SELECT id, code, name, models, defaultModel, ownerId FROM routing_lists"
                    ):
                        if str(row[5]) not in owners:
                            skipped_lists += 1
                            continue
                        conn.execute("INSERT INTO routing_lists VALUES (?,?,?,?,?,?)", row)
                    _fill_list_models(conn)
                    if skipped_tokens or skipped_lists:
                        print(
                            f"sync_user_db {db_file.name}: пропущено без владельца — "
                            f"токенов: {skipped_tokens}, списков: {skipped_lists}",
                            flush=True,
                        )
                finally:
                    src.close()

            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        print(f"sync_user_db {db_file.name}: {exc}", flush=True)
