"""Статистика: как выглядит счётчик и как он растёт."""

from datetime import datetime, timezone

from core.stats import format_stats, has_data, merge_report


def _stats(**kwargs) -> dict:
    base = {
        "ok": 3,
        "fail": 1,
        "usersOk": 3,
        "usersFail": 1,
        "last": "2026-10-12T21:00:00Z",
        "lastStatus": "ok",
    }
    base.update(kwargs)
    return base


class TestFormatStats:
    def test_no_reports_means_no_label(self) -> None:
        """Пустое место честнее нуля: ноль — это уже данные."""
        assert format_stats(None) is None
        assert format_stats({}) is None
        assert format_stats(_stats(usersOk=0, usersFail=0, ok=0, fail=0)) is None
        assert has_data(_stats()) is True

    def test_users_with_and_without_failures(self) -> None:
        assert format_stats(_stats()) == "3 (1) · 12.10 21:00"
        assert format_stats(_stats(usersFail=0, fail=0)) == "3 · 12.10 21:00"

    def test_an_old_report_keeps_only_the_date(self) -> None:
        now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        old = _stats(last="2026-10-01T09:30:00Z")
        assert format_stats(old, now=now) == "3 (1) · 01.10"

    def test_an_unparseable_date_leaves_the_counter_alone(self) -> None:
        assert format_stats(_stats(last="вчера")) == "3 (1)"


class TestMergeReport:
    def test_a_success_grows_the_ok_counter(self) -> None:
        when = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
        merged = merge_report(_stats(), ok=True, when=when)
        assert merged["usersOk"] == 4
        assert merged["usersFail"] == 1
        assert merged["lastStatus"] == "ok"
        assert merged["last"] == "2026-10-13T20:00:00Z"

    def test_a_failure_grows_the_fail_counter(self) -> None:
        when = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
        merged = merge_report(_stats(), ok=False, when=when)
        assert merged["usersOk"] == 3
        assert merged["usersFail"] == 2
        assert merged["lastStatus"] == "fail"

    def test_the_first_report_starts_from_scratch(self) -> None:
        when = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
        merged = merge_report(None, ok=True, when=when)
        assert merged["usersOk"] == 1
        assert merged["usersFail"] == 0

    def test_the_input_is_not_touched(self) -> None:
        original = _stats()
        merge_report(original, ok=True, when=datetime.now(timezone.utc))
        assert original["usersOk"] == 3


class TestClockEdgeCases:
    def test_a_report_from_the_future_does_not_break_formatting(self) -> None:
        now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        future = _stats(last="2026-12-31T23:59:00Z")
        label = format_stats(future, now=now)
        assert label is not None and label.startswith("3 (1) · 31.12")

    def test_a_week_old_report_keeps_its_time(self) -> None:
        now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        fresh = _stats(last="2026-10-07T09:15:00Z")  # шесть дней назад
        assert format_stats(fresh, now=now) == "3 (1) · 07.10 09:15"

    def test_an_older_report_drops_the_time(self) -> None:
        now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
        stale = _stats(last="2026-10-05T09:15:00Z")  # восемь дней назад
        assert format_stats(stale, now=now) == "3 (1) · 05.10"
