"""Прогресс загрузки локальной модели: строки потока и разрезание буфера."""

from core.progress import parse_line, split_lines


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
