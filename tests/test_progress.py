"""Прогресс загрузки локальной модели: строки потока, буфер и общий счёт."""

from core.progress import (
    ProgressEvent,
    PullTracker,
    describe,
    human_bytes,
    parse_line,
    split_lines,
)


class TestParseLine:
    def test_a_status_without_counts_has_no_fraction(self) -> None:
        event = parse_line('{"status":"pulling manifest"}')
        assert event is not None
        assert event.status == "pulling manifest"
        assert event.fraction is None
        assert event.done is False

    def test_counts_give_a_share(self) -> None:
        event = parse_line('{"status":"downloading","total":200,"completed":50}')
        assert event is not None
        assert event.fraction == 0.25
        assert event.total == 200.0
        assert event.completed == 50.0

    def test_the_layer_digest_is_carried_out(self) -> None:
        event = parse_line('{"digest":"sha256:abc","total":10,"completed":1}')
        assert event is not None and event.digest == "sha256:abc"

    def test_a_finished_download_is_a_full_share(self) -> None:
        event = parse_line('{"status":"downloading","total":200,"completed":200}')
        assert event is not None
        assert event.fraction == 1.0

    def test_the_share_never_leaves_its_range(self) -> None:
        event = parse_line('{"total":10,"completed":50}')
        assert event is not None and event.fraction == 1.0
        event = parse_line('{"total":10,"completed":-5}')
        assert event is not None and event.fraction == 0.0

    def test_success_ends_the_pull(self) -> None:
        event = parse_line('{"status":"success"}')
        assert event is not None and event.done is True

    def test_a_server_error_is_carried_out_verbatim(self) -> None:
        event = parse_line('{"error":"model not found"}')
        assert event is not None
        assert event.error == "model not found"
        assert event.done is False

    def test_empty_and_broken_lines_are_skipped(self) -> None:
        assert parse_line("") is None
        assert parse_line("   ") is None
        assert parse_line("не json") is None
        assert parse_line("[1,2,3]") is None


class TestSplitLines:
    def test_complete_lines_come_out_and_the_tail_stays(self) -> None:
        lines, rest = split_lines(b'{"a":1}\n{"b":')
        assert lines == ['{"a":1}']
        assert rest == b'{"b":'

    def test_a_line_split_across_chunks_is_reassembled(self) -> None:
        lines, rest = split_lines(b'{"status":"pull')
        assert lines == [] and rest == b'{"status":"pull'
        lines, rest = split_lines(rest + b'ing"}\n{"status":"success"}\n')
        assert lines == ['{"status":"pulling"}', '{"status":"success"}']
        assert rest == b""

    def test_windows_line_endings_are_handled(self) -> None:
        lines, rest = split_lines(b'{"a":1}\r\n{"b":2}\r\n')
        assert lines == ['{"a":1}', '{"b":2}']
        assert rest == b""


def _bytes(digest: str, total: float, completed: float) -> ProgressEvent:
    return ProgressEvent(status="downloading", digest=digest, total=total, completed=completed)


class TestPullTracker:
    def test_statuses_alone_leave_the_share_unknown(self) -> None:
        tracker = PullTracker()
        snapshot = tracker.add(ProgressEvent(status="pulling manifest"))
        assert snapshot.fraction is None
        assert snapshot.status == "pulling manifest"
        assert snapshot.done is False

    def test_one_layer_gives_its_share(self) -> None:
        tracker = PullTracker()
        snapshot = tracker.add(_bytes("sha256:a", 100, 25))
        assert snapshot.fraction == 0.25
        assert snapshot.completed_bytes == 25
        assert snapshot.total_bytes == 100

    def test_interleaved_layers_sum_up_instead_of_jumping(self) -> None:
        """Ollama шлёт слои перемежаясь: два слоя по 100 байт с третью
        долей каждого — это половина, а не «прыжок 30% → 60% → 30%»."""
        tracker = PullTracker()
        tracker.add(_bytes("sha256:a", 100, 30))
        snapshot = tracker.add(_bytes("sha256:b", 100, 30))
        assert snapshot.fraction == 0.3
        assert snapshot.completed_bytes == 60
        assert snapshot.total_bytes == 200

    def test_the_latest_word_on_a_layer_wins(self) -> None:
        tracker = PullTracker()
        tracker.add(_bytes("sha256:a", 100, 10))
        snapshot = tracker.add(_bytes("sha256:a", 100, 80))
        assert snapshot.fraction == 0.8

    def test_events_without_a_digest_share_one_bucket(self) -> None:
        """Некоторые серверы не называют digest — тогда последнее слово
        за последним событием, как у одного слоя."""
        tracker = PullTracker()
        tracker.add(_bytes("", 100, 10))
        snapshot = tracker.add(_bytes("", 100, 60))
        assert snapshot.fraction == 0.6

    def test_success_completes_the_share_even_without_all_counts(self) -> None:
        tracker = PullTracker()
        tracker.add(_bytes("sha256:a", 100, 40))
        snapshot = tracker.add(ProgressEvent(status="success", done=True))
        assert snapshot.done is True
        assert snapshot.fraction == 1.0

    def test_an_error_is_carried_to_the_snapshot(self) -> None:
        tracker = PullTracker()
        snapshot = tracker.add(ProgressEvent(status="", error="model not found"))
        assert snapshot.error == "model not found"
        assert snapshot.done is False


class TestHumanBytes:
    def test_small_amounts_stay_whole(self) -> None:
        assert human_bytes(0) == "0 Б"
        assert human_bytes(512) == "512 Б"

    def test_bigger_amounts_get_one_digit(self) -> None:
        assert human_bytes(2048) == "2.0 КБ"
        assert human_bytes(5 * 1024**3) == "5.0 ГБ"
        assert human_bytes(1536 * 1024**2) == "1.5 ГБ"

    def test_a_huge_amount_reaches_terabytes(self) -> None:
        assert human_bytes(3 * 1024**4) == "3.0 ТБ"


class TestDescribe:
    def test_a_full_line_shares_bytes_and_status(self) -> None:
        snapshot = PullTracker().add(_bytes("sha256:a", 100, 45))
        assert describe(snapshot) == "45% · 45 Б/100 Б · downloading"

    def test_a_status_only_event_is_plain_text(self) -> None:
        snapshot = PullTracker().add(ProgressEvent(status="pulling manifest"))
        assert describe(snapshot) == "pulling manifest"

    def test_nothing_known_says_nothing(self) -> None:
        assert describe(PullTracker().snapshot()) == ""
