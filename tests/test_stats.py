"""Статистика: как выглядит счётчик и как он растёт."""

from datetime import datetime, timezone

from core.stats import (
    format_stats,
    for_line,
    for_provider,
    has_data,
    merge_report,
    totals,
)


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


class TestForProvider:
    def test_counters_come_per_model(self) -> None:
        payload = {"ollama": {"version": 1, "models": {"llama3.2": _stats()}}}
        assert for_provider(payload, "ollama") == {"llama3.2": _stats()}

    def test_a_foreign_or_broken_payload_gives_nothing(self) -> None:
        assert for_provider({}, "ollama") == {}
        assert for_provider({"ollama": {"models": "да"}}, "ollama") == {}
        assert for_provider(None, "ollama") == {}
        assert for_provider("мусор", "ollama") == {}


class TestTotals:
    def test_sums_every_model_and_keeps_the_freshest_moment(self) -> None:
        models = {
            "llama3.2": _stats(ok=1, usersOk=1, fail=2, usersFail=2, last="2026-10-12T21:00:00Z"),
            "qwen3": _stats(ok=4, usersOk=4, fail=0, usersFail=0, last="2026-10-13T09:00:00Z"),
        }
        total = totals(models)
        assert total["usersOk"] == 5
        assert total["usersFail"] == 2
        assert total["last"] == "2026-10-13T09:00:00Z"

    def test_no_moment_at_all_leaves_the_time_out(self) -> None:
        total = totals({"llama3.2": _stats(ok=1, usersOk=1, fail=0, usersFail=0, last="")})
        assert "last" not in total
        assert format_stats(total) == "1"


class TestForLine:
    def test_the_configured_model_wins(self) -> None:
        payload = {
            "ollama": {
                "models": {
                    "llama3.2": _stats(ok=1, usersOk=1, fail=0, usersFail=0),
                    "qwen3": _stats(ok=9, usersOk=9, fail=1, usersFail=1),
                }
            }
        }
        counter = for_line(payload, "ollama", "qwen3")
        assert counter is not None and counter["usersOk"] == 9

    def test_without_a_configured_model_the_total_shows(self) -> None:
        payload = {
            "ollama": {
                "models": {
                    "llama3.2": _stats(ok=1, usersOk=1, fail=0, usersFail=0),
                    "qwen3": _stats(ok=4, usersOk=4, fail=0, usersFail=0),
                }
            }
        }
        counter = for_line(payload, "ollama")
        assert counter is not None and counter["usersOk"] == 5

    def test_an_unlisted_configured_model_falls_back_to_the_total(self) -> None:
        payload = {"ollama": {"models": {"llama3.2": _stats()}}}
        counter = for_line(payload, "ollama", "gpt-4o")
        assert counter is not None and counter["usersOk"] == 3

    def test_no_reports_means_nothing_to_show(self) -> None:
        assert for_line({}, "ollama", "llama3.2") is None
