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

    def test_bodies_are_always_on_and_not_stored_anywhere(self, tmp_path) -> None:
        """Переключателя тел больше нет: тела пишутся всегда.

        Регрессия: раньше состояние жило в bodies.state и в окружении
        AI_BRIDGE_LOG_BODIES; теперь журнал пишет тела по решению
        хозяина, и никакой файл не может это отключить.
        """
        log = EventLog(tmp_path)
        assert not hasattr(log, "bodies")  # атрибута-переключателя нет
        assert not (tmp_path / "bodies.state").exists()  # и файла состояния

    def test_base64_is_cut_out_of_bodies(self, tmp_path) -> None:
        """Картинка в теле — десятки килобайт мусора, который не читают.

        Base64 вырезается при записи: в журнале остаётся окружение
        и заметка о размере, а не стена кодировки.
        """
        log = EventLog(tmp_path)
        blob = "A" * 5000
        body = f'{{"image_url": "data:image/png;base64,{blob}"}}'

        event = log.record("чат", "u1", "с картинкой", req=body)

        assert "A" * 100 not in event["req"]  # мусор вырезан
        assert "base64," in event["req"]  # след остался
        assert "вырезано 5000" in event["req"]
        # и в файле тоже
        on_disk = json.loads(log.path.read_text(encoding="utf-8").splitlines()[0])
        assert "A" * 100 not in on_disk["req"]

    def test_short_base64_like_text_is_left_alone(self, tmp_path) -> None:
        """Короткие строки не трогаем — вырезал бы обычный текст."""
        log = EventLog(tmp_path)

        event = log.record("чат", "u1", "обычный ответ", resp="ключ base64,12345")

        assert event["resp"] == "ключ base64,12345"

    def test_base64_in_the_event_text_is_cut_too(self, tmp_path) -> None:
        """Вырезается в любом строковом поле, не только в телах."""
        log = EventLog(tmp_path)

        event = log.record("вход", "u1", f"картинка: data:image/jpeg;base64,{'B' * 300}")

        assert "B" * 100 not in event["text"]
        assert "вырезано 300" in event["text"]
