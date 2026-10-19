"""Хранилище расходов: запросы, токены и переключения по людям и моделям."""

from __future__ import annotations

import json

from storage.usage import MAX_FILE, UsageStore


class TestUsageStore:
    def test_add_counts_requests_and_tokens(self, tmp_path) -> None:
        store = UsageStore(tmp_path)
        store.add("u1", "ollama", "llama3.2", prompt=120, completion=40)
        store.add("u1", "ollama", "llama3.2", prompt=60, completion=10)

        cell = store.for_user("u1")["ollama/llama3.2"]
        assert cell["requests"] == 2
        assert cell["prompt"] == 180
        assert cell["completion"] == 50
        assert cell["fails"] == 0
        assert cell["switches"] == 0
        assert cell["last"]  # момент последнего запроса записан

    def test_fails_and_switches_are_counted(self, tmp_path) -> None:
        store = UsageStore(tmp_path)
        store.add("u1", "ollama", "llama3.2", ok=False)
        store.add("u1", "ollama", "llama3.2", switched=True)

        cell = store.for_user("u1")["ollama/llama3.2"]
        assert cell["requests"] == 2
        assert cell["fails"] == 1
        assert cell["switches"] == 1

    def test_people_are_separate(self, tmp_path) -> None:
        store = UsageStore(tmp_path)
        store.add("u1", "ollama", "llama3.2", prompt=100)
        store.add("u2", "ollama", "llama3.2", prompt=50)

        assert store.for_user("u1")["ollama/llama3.2"]["prompt"] == 100
        assert store.for_user("u2")["ollama/llama3.2"]["prompt"] == 50
        assert store.for_user("никого") == {}

    def test_survives_restart(self, tmp_path) -> None:
        UsageStore(tmp_path).add("u1", "ollama", "llama3.2", prompt=7)

        again = UsageStore(tmp_path)
        assert again.for_user("u1")["ollama/llama3.2"]["prompt"] == 7

    def test_totals_sums_everyone(self, tmp_path) -> None:
        store = UsageStore(tmp_path)
        store.add("u1", "ollama", "llama3.2", prompt=100, completion=10)
        store.add("u2", "ollama", "llama3.2", prompt=200, completion=20)
        store.add("u2", "openrouter", "gpt-4o", prompt=5)

        totals = store.totals()
        assert totals["ollama/llama3.2"]["requests"] == 2
        assert totals["ollama/llama3.2"]["prompt"] == 300
        assert totals["ollama/llama3.2"]["completion"] == 30
        assert totals["openrouter/gpt-4o"]["requests"] == 1

    def test_an_empty_user_id_is_ignored(self, tmp_path) -> None:
        store = UsageStore(tmp_path)
        store.add("", "ollama", "llama3.2", prompt=1)
        assert store.totals() == {}

    def test_a_broken_file_reads_as_empty(self, tmp_path) -> None:
        (tmp_path / "usage.json").write_text("{сломано", encoding="utf-8")
        store = UsageStore(tmp_path)
        assert store.for_user("u1") == {}

    def test_a_oversized_file_is_rotated(self, tmp_path) -> None:
        store = UsageStore(tmp_path)
        payload = {"u1": {"ollama/llama3.2": {"requests": 1, "prompt": 1}}}
        store._data = payload
        # Старый файл уже больше предела — запись уводит его в .old.
        store.path.write_text(" " * (MAX_FILE + 1), encoding="utf-8")
        store.add("u1", "ollama", "llama3.2", prompt=2)

        old = store.path.with_name(store.path.name + ".old")
        assert old.exists()
        saved = json.loads(store.path.read_text(encoding="utf-8"))
        assert saved["u1"]["ollama/llama3.2"]["prompt"] == 3
