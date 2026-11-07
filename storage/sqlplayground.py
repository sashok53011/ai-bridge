"""Безопасное чтение схемы и данных локальной базы PocketBase."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


_READ_QUERY = re.compile(
    r"\A\s*(?:(?:--[^\n]*(?:\n|$))|(?:/\*.*?\*/\s*))*"
    r"(SELECT|WITH)\b",
    re.IGNORECASE | re.DOTALL,
)

_WRITE_QUERY = re.compile(
    r"\A\s*(?:(?:--[^\n]*(?:\n|$))|(?:/\*.*?\*/\s*))*"
    r"(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|REPLACE|PRAGMA)\b",
    re.IGNORECASE | re.DOTALL,
)


class SqlPlaygroundError(ValueError):
    """Запрос отклонён или не может быть выполнен."""


class SqlPlayground:
    """Открывает SQLite-файл; ``read_only=True`` — только ``mode=ro``."""

    def __init__(
        self,
        database: str | Path,
        *,
        max_rows: int = 200,
        max_cell_chars: int = 2_000,
        max_result_bytes: int = 2_000_000,
        timeout: float = 3.0,
    ) -> None:
        self._database = Path(database).expanduser()
        self.max_rows = max_rows
        self.max_cell_chars = max_cell_chars
        self.max_result_bytes = max_result_bytes
        self.timeout = timeout

    def _connect(self, *, read_only: bool = True) -> sqlite3.Connection:
        try:
            path = self._database.resolve(strict=True)
            if not path.is_file():
                raise SqlPlaygroundError("путь к базе не является файлом")
            if read_only:
                connection = sqlite3.connect(
                    f"{path.as_uri()}?mode=ro",
                    uri=True,
                    timeout=1.0,
                )
                connection.execute("PRAGMA query_only = ON")
            else:
                connection = sqlite3.connect(str(path), timeout=1.0)
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65_536)
            connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 100)
            return connection
        except SqlPlaygroundError:
            raise
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise SqlPlaygroundError(
                "не удалось открыть базу: проверьте путь и права чтения"
            ) from exc

    @contextmanager
    def _connection(self, *, read_only: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self._connect(read_only=read_only)
        try:
            yield connection
        except sqlite3.Error as exc:
            raise SqlPlaygroundError(str(exc)[:500]) from exc
        finally:
            connection.close()

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    def _display_value(self, value: Any) -> Any:
        if isinstance(value, bytes):
            return f"<BLOB, {len(value)} байт>"
        if isinstance(value, str) and len(value) > self.max_cell_chars:
            return value[: self.max_cell_chars] + "…"
        return value

    def schema(self, *, include_data: bool = False) -> dict[str, list[dict]]:
        """Возвращает таблицы, связи и, по выбору, первые пять строк таблиц."""
        with self._connection() as connection:
            names = connection.execute(
                "SELECT name, type FROM sqlite_schema "
                "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
            tables: list[dict] = []
            known_names = {str(row[0]) for row in names}
            for name, kind in names:
                table_name = str(name)
                quoted = self._quote_identifier(table_name)
                columns = [
                    {
                        "name": str(row[1]),
                        "type": str(row[2] or ""),
                        "not_null": bool(row[3]),
                        "primary_key": bool(row[5]),
                    }
                    for row in connection.execute(f"PRAGMA table_info({quoted})")
                ]
                table: dict[str, Any] = {
                    "name": table_name,
                    "type": str(kind),
                    "columns": columns,
                }
                if include_data:
                    preview = connection.execute(f"SELECT * FROM {quoted} LIMIT 6")
                    table["sample_columns"] = [
                        str(column[0]) for column in preview.description or ()
                    ]
                    rows = []
                    data_size = 0
                    while len(rows) < 5:
                        row = preview.fetchone()
                        if row is None:
                            break
                        display_row = [
                            self._display_value(value) for value in row
                        ]
                        data_size += sum(
                            len(str(value).encode("utf-8")) for value in display_row
                        )
                        if data_size > 64_000:
                            table["sample_truncated"] = True
                            break
                        rows.append(display_row)
                    table["sample_rows"] = rows
                    if len(rows) == 5 and preview.fetchone() is not None:
                        table["sample_truncated"] = True
                tables.append(table)

            relations = self._relations(connection, known_names)
            return {"tables": tables, "relations": relations}

    def _relations(
        self, connection: sqlite3.Connection, known_names: set[str]
    ) -> list[dict[str, str]]:
        relations: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()

        # PocketBase хранит связи в описаниях коллекций, включая множественный
        # выбор, который не обязательно выражен внешним ключом SQLite.
        if "_collections" in known_names:
            metadata_columns = {
                str(row[1])
                for row in connection.execute(
                    'PRAGMA table_info("_collections")'
                )
            }
            if {"id", "name", "fields"} <= metadata_columns:
                collections = connection.execute(
                    'SELECT id, name, fields FROM "_collections"'
                ).fetchall()
                collection_names = {str(row[0]): str(row[1]) for row in collections}
                for collection_id, collection_name, fields_json in collections:
                    try:
                        fields = json.loads(fields_json or "[]")
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if not isinstance(fields, list):
                        continue
                    for field in fields:
                        if not isinstance(field, dict) or field.get("type") != "relation":
                            continue
                        target = collection_names.get(str(field.get("collectionId") or ""))
                        if not target:
                            continue
                        source = str(collection_name)
                        field_name = str(field.get("name") or "")
                        try:
                            multiple = int(field.get("maxSelect") or 1) > 1
                        except (TypeError, ValueError):
                            multiple = False
                        cardinality = "N:M" if multiple else "N:1"
                        key = (source, target, field_name)
                        if key not in seen:
                            relations.append(
                                {
                                    "source": source,
                                    "target": target,
                                    "field": field_name,
                                    "cardinality": cardinality,
                                }
                            )
                            seen.add(key)

        # Дополняем описание внешними ключами, объявленными непосредственно
        # в SQLite, например в junction-таблицах.
        for table_name in sorted(known_names):
            quoted = self._quote_identifier(table_name)
            foreign_keys = connection.execute(
                f"PRAGMA foreign_key_list({quoted})"
            ).fetchall()
            for row in foreign_keys:
                target = str(row[2])
                field_name = str(row[3])
                key = (table_name, target, field_name)
                if target not in known_names or key in seen:
                    continue
                relations.append(
                    {
                        "source": table_name,
                        "target": target,
                        "field": field_name,
                        "cardinality": "N:1",
                    }
                )
                seen.add(key)
        return relations

    def execute(self, query: str, *, read_only: bool = True) -> dict[str, Any]:
        """Выполняет запрос; для админа (``read_only=False``) — любой SQL."""
        if len(query) > 20_000:
            raise SqlPlaygroundError("запрос длиннее 20 000 символов")
        pattern = _READ_QUERY if read_only else _WRITE_QUERY
        if not pattern.match(query):
            if read_only:
                raise SqlPlaygroundError("разрешены только запросы SELECT")
            raise SqlPlaygroundError(
                "разрешены SELECT, WITH, INSERT, UPDATE, DELETE, "
                "CREATE, DROP, ALTER, REPLACE, PRAGMA"
            )

        with self._connection(read_only=read_only) as connection:
            deadline = time.monotonic() + self.timeout
            connection.set_progress_handler(
                lambda: int(time.monotonic() > deadline), 1_000
            )
            try:
                cursor = connection.execute(query)
                columns = [str(column[0]) for column in cursor.description or ()]
                rows = []
                result_size = 0
                truncated = False
                while len(rows) < self.max_rows:
                    row = cursor.fetchone()
                    if row is None:
                        break
                    display_row = [self._display_value(value) for value in row]
                    row_size = sum(
                        len(str(value).encode("utf-8")) for value in display_row
                    )
                    if result_size + row_size > self.max_result_bytes:
                        truncated = True
                        break
                    result_size += row_size
                    rows.append(display_row)
                if len(rows) == self.max_rows and cursor.fetchone() is not None:
                    truncated = True
            except sqlite3.Error as exc:
                message = str(exc).replace(str(self._database), "[база]")
                raise SqlPlaygroundError(message[:500]) from exc
            finally:
                connection.set_progress_handler(None, 0)

        return {
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
        }
