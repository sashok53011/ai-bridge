"""Ежедневные автосохранения: снимки журнала и статистики по датам."""

from __future__ import annotations

from service.archive import archive_daily, archive_dir


def _seed(directory) -> None:
    (directory / "events.jsonl").write_text('{"kind": "чат"}\n', encoding="utf-8")
    (directory / "usage.json").write_text('{"ollama": {}}', encoding="utf-8")


class TestArchiveDaily:
    def test_snapshots_carry_the_date_in_the_name(self, tmp_path) -> None:
        _seed(tmp_path)

        made = archive_daily(tmp_path, today="2026-10-20")

        assert [p.name for p in made] == [
            "2026-10-20-events.jsonl",
            "2026-10-20-usage.json",
        ]
        archive = archive_dir(tmp_path)
        assert (archive / "2026-10-20-events.jsonl").read_text(
            encoding="utf-8"
        ) == '{"kind": "чат"}\n'
        # Живые файлы не тронуты — снимок копия, а не перенос.
        assert (tmp_path / "events.jsonl").exists()

    def test_the_same_day_is_not_overwritten(self, tmp_path) -> None:
        _seed(tmp_path)
        archive_daily(tmp_path, today="2026-10-20")
        (tmp_path / "events.jsonl").write_text("днём дошло ещё\n", encoding="utf-8")

        made = archive_daily(tmp_path, today="2026-10-20")

        assert made == []  # снимок дня уже лежит — не подменяем
        archived = archive_dir(tmp_path) / "2026-10-20-events.jsonl"
        assert archived.read_text(encoding="utf-8") == '{"kind": "чат"}\n'

    def test_a_new_day_makes_a_new_snapshot(self, tmp_path) -> None:
        _seed(tmp_path)
        archive_daily(tmp_path, today="2026-10-20")

        made = archive_daily(tmp_path, today="2026-10-21")

        assert [p.name for p in made] == [
            "2026-10-21-events.jsonl",
            "2026-10-21-usage.json",
        ]

    def test_a_missing_source_is_skipped_not_fatal(self, tmp_path) -> None:
        (tmp_path / "events.jsonl").write_text("{}\n", encoding="utf-8")

        made = archive_daily(tmp_path, today="2026-10-20")

        assert [p.name for p in made] == ["2026-10-20-events.jsonl"]

    def test_nothing_to_archive_is_quiet(self, tmp_path) -> None:
        assert archive_daily(tmp_path, today="2026-10-20") == []
        assert not archive_dir(tmp_path).exists()
