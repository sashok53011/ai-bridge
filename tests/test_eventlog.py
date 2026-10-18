"""Журнал событий: запись, кольцо в памяти, ротация файла."""

import json

from service.eventlog import EventLog


class TestEventLog:
    def test_an_event_keeps_its_shape(self, tmp_path) -> None:
        log = EventLog(tmp_path)

        event = log.record("вход", "anna@mail", "успешный вход")

        assert event["kind"] == "вход"
        assert event["user"] == "anna@mail"
        assert event["ts"].endswith("Z")
        on_disk = json.loads(log.path.read_text(encoding="utf-8").splitlines()[0])
        assert on_disk["text"] == "успешный вход"

    def test_empty_fields_are_dropped(self, tmp_path) -> None:
        log = EventLog(tmp_path)

        event = log.record("v1", "u1", "чат", provider="", model="qwen3")

        assert "provider" not in event
        assert event["model"] == "qwen3"

    def test_recent_is_newest_first_and_capped(self, tmp_path) -> None:
        import service.eventlog as eventlog_module

        log = EventLog(tmp_path)
        old_cap = eventlog_module.MAX_EVENTS
        try:
            eventlog_module.MAX_EVENTS = 5
            for index in range(7):
                log.record("страница", "u1", f"событие {index}")
        finally:
            eventlog_module.MAX_EVENTS = old_cap

        recent = log.recent()
        assert len(recent) == 5
        assert recent[0]["text"] == "событие 6"  # новые сверху

    def test_a_restarted_journal_remembers_the_file(self, tmp_path) -> None:
        first = EventLog(tmp_path)
        first.record("вход", "u1", "до перезапуска")

        again = EventLog(tmp_path)

        assert again.recent()[0]["text"] == "до перезапуска"

    def test_a_big_file_rotates(self, tmp_path) -> None:
        import service.eventlog as eventlog_module

        log = EventLog(tmp_path)
        log.record("вход", "u1", "первое")
        old_limit = eventlog_module.MAX_FILE
        try:
            eventlog_module.MAX_FILE = 1  # любой мегабайт уже перебор
            log.record("вход", "u1", "второе")
        finally:
            eventlog_module.MAX_FILE = old_limit

        assert (tmp_path / "events.jsonl.old").exists()
        assert "второе" in log.path.read_text(encoding="utf-8")

    def test_a_broken_directory_does_not_crash_the_service(self, tmp_path) -> None:
        blocker = tmp_path / "занято"
        blocker.write_text("я файл, не папка", encoding="utf-8")
        log = EventLog(blocker / "нет")

        event = log.record("вход", "u1", "всё равно пишем")

        assert event["kind"] == "вход"  # в память попало, файл молчит
        assert log.recent()
