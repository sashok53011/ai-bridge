"""Хранилище автотестов: прогресс текущего прогона и последний результат."""

import json

from storage.autotest import AutotestStore


class TestProgress:
    def test_begin_starts_clean_and_deletes_old_result(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.finish("old", [{"model": "m", "status": "ok"}])
        assert store.load()["results"]

        store.begin("new")

        status = store.status()
        assert status["running"] is True
        assert status["provider"] == "new"
        assert status["done"] == 0
        assert status["total"] == 0
        assert store.load()["results"] == []  # старый прогон удалён

    def test_plan_tick_advance(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.begin("p")
        store.plan(4)

        store.tick("model-a", "vision")
        assert store.status()["model"] == "model-a"
        assert store.status()["test"] == "vision"

        store.advance()
        store.advance()
        assert store.status()["done"] == 2
        assert store.status()["total"] == 4

    def test_finish_stops_and_saves(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.begin("p")
        store.plan(1)

        saved = store.finish("p", [{"model": "m", "status": "ok"}])

        assert saved["results"][0]["status"] == "ok"
        assert store.status()["running"] is False
        assert store.status()["finished_at"]
        assert store.load()["provider"] == "p"

    def test_fail_stops_the_run(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.begin("p")

        store.fail("boom")

        status = store.status()
        assert status["running"] is False
        assert status["error"] == "boom"

    def test_a_new_run_replaces_not_appends(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.finish("first", [{"model": "a", "status": "ok"}])
        store.begin("second")
        store.finish("second", [{"model": "b", "status": "fail"}])

        data = store.load()
        assert data["provider"] == "second"
        assert [r["model"] for r in data["results"]] == ["b"]

    def test_status_is_a_copy(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.begin("p")
        status = store.status()
        status["provider"] = "hacked"
        assert store.status()["provider"] == "p"

    def test_finish_writes_json_to_disk(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.begin("p")
        store.finish("p", [{"model": "m", "status": "ok"}])

        raw = json.loads((tmp_path / "autotest.json").read_text(encoding="utf-8"))
        assert raw["provider"] == "p"
        assert raw["results"][0]["model"] == "m"

    def test_prompt_is_saved_with_the_run(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.begin("p")

        store.finish("p", [{"model": "m"}], prompt="мой промпт")

        assert store.load()["prompt"] == "мой промпт"

    def test_old_files_without_prompt_are_read(self, tmp_path) -> None:
        """Прогон старого формата (без prompt) не ломает чтение."""
        (tmp_path / "autotest.json").write_text(
            json.dumps({"provider": "p", "timestamp": "t", "results": []}),
            encoding="utf-8",
        )

        data = AutotestStore(tmp_path).load()

        assert data["prompt"] == ""

    def test_request_stop_is_recorded_and_begin_clears_it(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)
        store.begin("p")
        assert store.stop_requested() is False

        store.request_stop()
        assert store.stop_requested() is True
        assert store.status()["stop"] is True  # страница видит «останавливается»

        store.begin("p")
        assert store.stop_requested() is False  # новый прогон — с нуля

    def test_stop_without_a_run_is_a_noop(self, tmp_path) -> None:
        store = AutotestStore(tmp_path)

        store.request_stop()

        assert store.stop_requested() is False
