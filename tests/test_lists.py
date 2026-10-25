"""Списки моделей: белый (по группам), серый и чёрный."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from storage.lists import (
    BLACKLIST,
    GREYLIST,
    WHITELIST_FILES,
    ModelLists,
    _parse_cycle,
    _parse_until,
)


class TestReading:
    def test_names_are_read_across_groups_and_lowercased(self, tmp_path) -> None:
        tmp_path.joinpath(WHITELIST_FILES["vision"]).write_text(
            "Ollama/Qwen3:0.6b\n", encoding="utf-8"
        )
        tmp_path.joinpath(WHITELIST_FILES["text"]).write_text(
            "GEMINI/models/gemini-2.5-flash\n", encoding="utf-8"
        )

        assert ModelLists(tmp_path).whitelist() == {
            "ollama/qwen3:0.6b",
            "gemini/models/gemini-2.5-flash",
        }

    def test_whitelist_group_reports_where_the_model_lives(self, tmp_path) -> None:
        tmp_path.joinpath(WHITELIST_FILES["vision-tools"]).write_text(
            "groq/x\n", encoding="utf-8"
        )
        lists = ModelLists(tmp_path)

        assert lists.whitelist_group("groq/x") == "vision-tools"
        assert lists.whitelist_group("groq/none") == ""

    def test_whitelist_groups_returns_every_group(self, tmp_path) -> None:
        tmp_path.joinpath(WHITELIST_FILES["text"]).write_text("a/one\n", encoding="utf-8")
        tmp_path.joinpath(WHITELIST_FILES["vision"]).write_text("b/two\n", encoding="utf-8")

        groups = ModelLists(tmp_path).whitelist_groups()

        assert groups["text"] == {"a/one"}
        assert groups["vision"] == {"b/two"}
        assert groups["tools"] == set()
        assert groups["vision-tools"] == set()

    def test_comments_and_blanks_are_skipped(self, tmp_path) -> None:
        tmp_path.joinpath(BLACKLIST).write_text(
            "# плохо ведёт себя\n\nopenrouter/auto  # нет кредитов\n", encoding="utf-8"
        )

        assert ModelLists(tmp_path).blacklist() == {"openrouter/auto"}

    def test_a_missing_file_is_an_empty_list(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        assert lists.whitelist() == set()
        assert lists.blacklist() == set()
        assert lists.grey() == {}
        assert lists.whitelist_groups()["text"] == set()

    def test_a_rewritten_file_is_seen_without_restart(self, tmp_path) -> None:
        path = tmp_path.joinpath(BLACKLIST)
        path.write_text("a/one\n", encoding="utf-8")
        lists = ModelLists(tmp_path)
        assert lists.blacklist() == {"a/one"}

        path.write_text("b/two\n", encoding="utf-8")
        assert lists.blacklist() == {"b/two"}


class TestGreyParsing:
    def test_until_and_cycle_are_parsed_from_the_comment(self) -> None:
        comment = " до 2026-10-21T18:00:00Z: лимит запросов · цикл 2"
        assert _parse_until(comment) == datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
        assert _parse_cycle(comment) == 2

    def test_a_line_without_markers_defaults(self) -> None:
        assert _parse_until("что-то") is None
        assert _parse_cycle("") == 1

    def test_grey_returns_datetimes_and_cycles(self, tmp_path) -> None:
        until = datetime.now(timezone.utc) + timedelta(hours=5)
        iso = until.isoformat().replace("+00:00", "Z")
        tmp_path.joinpath(GREYLIST).write_text(
            f"ollama  # до {iso}: кредиты · цикл 1\n", encoding="utf-8"
        )

        grey = ModelLists(tmp_path).grey()

        assert grey["ollama"]["cycle"] == 1
        assert grey["ollama"]["until"] == until
        assert grey["ollama"]["reason"] == "кредиты"

    def test_a_naive_timestamp_is_read_as_utc(self, tmp_path) -> None:
        """Без Z срок всё равно aware — иначе сравнение упадёт."""
        tmp_path.joinpath(GREYLIST).write_text(
            "ollama  # до 2026-10-21T18:00:00: кредиты\n", encoding="utf-8"
        )

        until = ModelLists(tmp_path).grey()["ollama"]["until"]

        assert until.tzinfo is not None

    def test_old_model_lines_collapse_to_the_provider(self, tmp_path) -> None:
        """Старый формат «провайдер/модель» читается как провайдер.

        Спит теперь провайдер целиком: берётся самый поздний срок.
        """
        until = datetime.now(timezone.utc) + timedelta(hours=5)
        iso = until.isoformat().replace("+00:00", "Z")
        tmp_path.joinpath(GREYLIST).write_text(
            f"ollama/qwen  # до {iso}: кредиты · цикл 1\n"
            f"ollama/llama  # до {iso}: лимит · цикл 2\n",
            encoding="utf-8",
        )

        grey = ModelLists(tmp_path).grey()

        assert set(grey) == {"ollama"}  # свёрнуты обе модели
        assert grey["ollama"]["cycle"] == 2  # берётся самый большой цикл


class TestWriting:
    def test_whitelist_place_puts_into_group_and_removes_elsewhere(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        lists.whitelist_place("groq/x", "text", "подтверждена успехом")

        assert lists.whitelist_group("groq/x") == "text"

        # Доказала vision — переложена, в text не осталась.
        lists.whitelist_place("groq/x", "vision", "подтверждена успехом")

        assert lists.whitelist_group("groq/x") == "vision"
        assert "groq/x" not in lists.whitelist_groups()["text"]

    def test_place_is_idempotent(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        first = lists.whitelist_place("a/one", "tools", "x")
        second = lists.whitelist_place("a/one", "tools", "x")

        assert first is True
        assert second is False  # уже в этой группе — не переписываем
        assert lists.whitelist_groups()["tools"] == {"a/one"}

    def test_an_unknown_group_falls_back_to_text(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        lists.whitelist_place("a/one", "просто-непонятно")

        assert lists.whitelist_group("a/one") == "text"

    def test_blacklist_add_keeps_the_reason(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        lists.blacklist_add("ollama/x", "HTTP 500 — взорвался")

        text = lists.text(BLACKLIST)
        assert "ollama/x" in text
        assert "HTTP 500" in text  # причина видна админу

    def test_grey_add_sets_the_deadline_and_cycle(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)

        until = lists.grey_add("ollama", "лимит", cycle=2)

        cell = lists.grey()["ollama"]
        assert cell["cycle"] == 2
        assert abs((cell["until"] - until).total_seconds()) < 2
        assert "лимит" in lists.text(GREYLIST)

    def test_grey_add_normalizes_an_old_style_key(self, tmp_path) -> None:
        """«провайдер/модель» сворачивается к провайдеру и заменяет строку."""
        lists = ModelLists(tmp_path)
        lists.grey_add("ollama/qwen", "кредиты", cycle=1)

        lists.grey_add("ollama/llama", "лимит", cycle=2)

        assert set(lists.grey()) == {"ollama"}  # одна строка на провайдера
        assert lists.grey()["ollama"]["cycle"] == 2

    def test_grey_add_replaces_an_existing_line(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        lists.grey_add("ollama", "лимит", cycle=1)

        lists.grey_add("ollama", "кредиты", cycle=2)

        assert lists.grey()["ollama"]["cycle"] == 2
        assert lists.text(GREYLIST).count("ollama") == 1

    def test_drops_remove_the_lines(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        lists.whitelist_place("a/one", "text")
        lists.blacklist_add("b/two")
        lists.grey_add("c", "лимит")

        lists.whitelist_drop("a/one")  # из всех групп разом
        lists.blacklist_drop("b/two")
        lists.grey_drop("c")

        assert lists.whitelist() == set()
        assert lists.blacklist() == set()
        assert lists.grey() == {}

    def test_a_dropped_line_keeps_the_others(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        lists.grey_add("a", "x")
        lists.grey_add("b", "y")

        lists.grey_drop("a")

        assert set(lists.grey()) == {"b"}

    def test_refused_vision_and_tools_are_separate(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)

        assert lists.refused_vision_add("ollama/x") is True
        assert lists.refused_tools_add("ollama/y") is True

        assert "ollama/x" in lists.refused_vision()
        assert "ollama/y" in lists.refused_tools()
        assert "ollama/y" not in lists.refused_vision()
        assert "ollama/x" not in lists.refused_tools()

    def test_refused_drop_cleans_the_line(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)
        lists.refused_vision_add("ollama/x")
        lists.refused_tools_add("groq/y")

        assert lists.refused_vision_drop("ollama/x") is True
        assert lists.refused_vision() == set()
        assert "groq/y" in lists.refused_tools()

    def test_refused_lists_are_allowed_for_write_text(self, tmp_path) -> None:
        lists = ModelLists(tmp_path)

        assert lists.write_text("novision.txt", "ollama/x  # доказано: не умеет vision\n")
        assert lists.write_text("notools.txt", "ollama/y  # доказано: не умеет tools\n")

        assert "ollama/x" in lists.refused_vision()
        assert "ollama/y" in lists.refused_tools()


class TestMigration:
    def test_old_flat_file_is_split_by_groups(self, tmp_path) -> None:
        tmp_path.joinpath("whitelist.txt").write_text(
            "groq/text-model  # подтверждена успехом\n"
            "ollama/vision-model\n",
            encoding="utf-8",
        )
        lists = ModelLists(tmp_path)
        group_of = lambda name: "vision" if "vision" in name else "text"  # noqa: E731

        moved = lists.migrate_old_whitelist(group_of)

        assert sorted(moved) == ["groq/text-model", "ollama/vision-model"]
        assert lists.whitelist_group("ollama/vision-model") == "vision"
        assert lists.whitelist_group("groq/text-model") == "text"

    def test_the_old_file_becomes_migrated_not_deleted(self, tmp_path) -> None:
        tmp_path.joinpath("whitelist.txt").write_text("a/one\n", encoding="utf-8")
        lists = ModelLists(tmp_path)

        lists.migrate_old_whitelist()

        assert not tmp_path.joinpath("whitelist.txt").exists()
        assert "a/one" in tmp_path.joinpath("whitelist.txt.migrated").read_text(
            encoding="utf-8"
        )

    def test_migration_is_safe_to_repeat(self, tmp_path) -> None:
        tmp_path.joinpath("whitelist.txt").write_text("a/one\n", encoding="utf-8")
        lists = ModelLists(tmp_path)

        lists.migrate_old_whitelist()
        second = lists.migrate_old_whitelist()  # файла уже нет

        assert second == []
        assert lists.whitelist() == {"a/one"}  # не задублировалась

    def test_a_broken_resolver_falls_back_to_text(self, tmp_path) -> None:
        tmp_path.joinpath("whitelist.txt").write_text("a/one\n", encoding="utf-8")
        lists = ModelLists(tmp_path)

        def explode(name: str) -> str:
            raise RuntimeError("boom")

        lists.migrate_old_whitelist(explode)

        assert lists.whitelist_group("a/one") == "text"

    def test_old_grey_model_lines_collapse_to_providers(self, tmp_path) -> None:
        """Миграция серого: «провайдер/модель» → «провайдер», срок сохраняется."""
        until = datetime.now(timezone.utc) + timedelta(hours=3)
        iso = until.isoformat().replace("+00:00", "Z")
        tmp_path.joinpath(GREYLIST).write_text(
            f"ollama/qwen  # до {iso}: кредиты · цикл 1\n"
            f"ollama/llama  # до {iso}: лимит · цикл 2\n"
            f"groq/x  # до {iso}: квота · цикл 1\n",
            encoding="utf-8",
        )
        lists = ModelLists(tmp_path)

        moved = lists.migrate_old_grey()

        assert sorted(moved) == ["groq/x", "ollama/llama", "ollama/qwen"]
        grey = lists.grey()
        assert set(grey) == {"groq", "ollama"}
        assert grey["ollama"]["cycle"] == 2  # самый большой цикл
        assert abs((grey["ollama"]["until"] - until).total_seconds()) < 2

    def test_grey_migration_is_safe_to_repeat(self, tmp_path) -> None:
        tmp_path.joinpath(GREYLIST).write_text(
            "ollama/x  # до 2026-10-21T18:00:00Z: кредиты · цикл 1\n",
            encoding="utf-8",
        )
        lists = ModelLists(tmp_path)

        lists.migrate_old_grey()
        second = lists.migrate_old_grey()  # строк с «/» уже нет

        assert second == []
        assert set(lists.grey()) == {"ollama"}  # не задублировалась
