"""SQL Playground открывает базу только для чтения и ограничивает запросы."""

import json
import sqlite3

import pytest

from storage.sqlplayground import SqlPlayground, SqlPlaygroundError


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "data.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE _collections (id TEXT, name TEXT, fields TEXT);
            CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT);
            CREATE TABLE posts (id TEXT PRIMARY KEY, author TEXT);
            CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT);
            """
        )
        relation = json.dumps(
            [
                {
                    "name": "author",
                    "type": "relation",
                    "collectionId": "users-id",
                    "maxSelect": 1,
                }
            ]
        )
        connection.executemany(
            "INSERT INTO _collections VALUES (?, ?, ?)",
            [
                ("users-id", "users", "[]"),
                ("posts-id", "posts", relation),
                ("tags-id", "tags", "[]"),
            ],
        )
        connection.execute("INSERT INTO users VALUES ('u1', 'user@example.test')")
        connection.execute("INSERT INTO posts VALUES ('p1', 'u1')")
    return path


def test_schema_includes_pocketbase_relationships_and_optional_sample_rows(database):
    playground = SqlPlayground(database)

    schema = playground.schema()
    assert {table["name"] for table in schema["tables"]} == {
        "_collections",
        "posts",
        "tags",
        "users",
    }
    assert schema["relations"] == [
        {
            "source": "posts",
            "target": "users",
            "field": "author",
            "cardinality": "N:1",
        }
    ]
    assert "sample_rows" not in schema["tables"][-1]

    with_data = playground.schema(include_data=True)
    users = next(table for table in with_data["tables"] if table["name"] == "users")
    assert users["sample_rows"] == [["u1", "user@example.test"]]


def test_only_select_queries_run_and_database_is_not_modified(database):
    playground = SqlPlayground(database)

    result = playground.execute("SELECT email FROM users")
    assert result["columns"] == ["email"]
    assert result["rows"] == [["user@example.test"]]

    with pytest.raises(SqlPlaygroundError, match="только запросы SELECT"):
        playground.execute("DELETE FROM users")
    with pytest.raises(SqlPlaygroundError):
        playground.execute(
            "WITH removed AS (SELECT id FROM users) "
            "DELETE FROM users WHERE id IN (SELECT id FROM removed)"
        )
    with pytest.raises(SqlPlaygroundError):
        playground.execute("SELECT 1; DELETE FROM users")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone() == (1,)


def test_result_rows_and_cell_sizes_are_limited(database):
    playground = SqlPlayground(database, max_rows=2)
    result = playground.execute(
        "WITH RECURSIVE numbers(n) AS (VALUES(1) UNION ALL "
        "SELECT n + 1 FROM numbers WHERE n < 4) SELECT n FROM numbers"
    )
    assert result["rows"] == [[1], [2]]
    assert result["truncated"] is True

    with pytest.raises(SqlPlaygroundError, match="string or blob too big"):
        playground.execute("SELECT zeroblob(1000001)")

    bounded = SqlPlayground(database, max_result_bytes=5)
    oversized = bounded.execute("SELECT email FROM users")
    assert oversized["rows"] == []
    assert oversized["truncated"] is True


def test_query_timeout_is_reported(database):
    playground = SqlPlayground(database, timeout=0.005)
    with pytest.raises(SqlPlaygroundError, match="interrupted"):
        playground.execute(
            "WITH RECURSIVE numbers(n) AS (VALUES(1) UNION ALL "
            "SELECT n + 1 FROM numbers WHERE n < 100000000) "
            "SELECT max(n) FROM numbers"
        )


def test_multiple_relation_fields_render_as_many_to_many(database):
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE _collections SET fields = ? WHERE name = 'posts'",
            (
                json.dumps(
                    [
                        {
                            "name": "authors",
                            "type": "relation",
                            "collectionId": "users-id",
                            "maxSelect": 10,
                        }
                    ]
                ),
            ),
        )

    relation = SqlPlayground(database).schema()["relations"][0]
    assert relation["cardinality"] == "N:M"
