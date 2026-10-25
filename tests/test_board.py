"""Доска «Нашёл bug»: общий разговор живёт на сервере.

Проверяем то, на чём стоит доверие к доске: сообщения не теряются,
статус без пояснения не проходит, прошлые причины не исчезают,
а запись выживает при обрыве (временный файл + подмена).
"""

import json

from storage.board import BoardStore, STATUSES, current_status


def _board(tmp_path) -> BoardStore:
    return BoardStore(tmp_path)


class TestPosting:
    def test_a_post_is_stored_with_author_and_time(self, tmp_path) -> None:
        board = _board(tmp_path)

        assert board.add(author="a@b.c", text="кнопка не отвечает") == ""

        (post,) = board.all()
        assert post["author"] == "a@b.c"
        assert post["text"] == "кнопка не отвечает"
        assert post["ts"].endswith("Z")  # время сервера — UTC
        assert post["id"]

    def test_an_empty_post_is_refused_with_a_reason(self, tmp_path) -> None:
        board = _board(tmp_path)

        error = board.add(author="a@b.c", text="   ")

        assert error  # причина, а не просто «нет»
        assert board.all() == []

    def test_posts_from_different_people_live_together(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="первый")
        board.add(author="b@b.c", text="второй")

        assert [p["text"] for p in board.all()] == ["первый", "второй"]

    def test_a_broken_file_is_an_empty_board_not_a_crash(self, tmp_path) -> None:
        (tmp_path / "board.json").write_text("{сломано", encoding="utf-8")

        assert _board(tmp_path).all() == []


class TestComments:
    def test_a_comment_joins_the_post(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="баг")
        post = board.all()[0]

        assert board.comment(post["id"], author="b@b.c", text="вижу тоже") == ""

        comment = board.get(post["id"])["comments"][0]
        assert comment["by"] == "b@b.c"
        assert comment["text"] == "вижу тоже"
        assert comment["ts"].endswith("Z")

    def test_an_empty_comment_is_refused(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="баг")
        post = board.all()[0]

        assert board.comment(post["id"], author="b@b.c", text="  ")
        assert board.get(post["id"])["comments"] == []

    def test_a_comment_to_a_missing_post_explains_itself(self, tmp_path) -> None:
        board = _board(tmp_path)

        error = board.comment("нет-такого", author="b@b.c", text="привет")

        assert "нет такого сообщения" in error


class TestStatuses:
    def test_a_status_needs_a_reason(self, tmp_path) -> None:
        """Кто ставит статус — тот объясняет почему; пустое не проходит."""
        board = _board(tmp_path)
        board.add(author="a@b.c", text="баг")
        post = board.all()[0]

        error = board.set_status(post["id"], author="boss@b.c", key="work", why="  ")

        assert "без пояснения" in error
        assert current_status(board.get(post["id"])) is None

    def test_an_unknown_status_is_refused(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="баг")
        post = board.all()[0]

        error = board.set_status(post["id"], author="b@b.c", key="лишний", why="так вышло")

        assert "неизвестный статус" in error

    def test_a_status_keeps_who_when_and_why(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="баг")
        post = board.all()[0]

        board.set_status(post["id"], author="boss@b.c", key="wait", why="принял")

        cur = current_status(board.get(post["id"]))
        assert (cur["key"], cur["by"], cur["why"]) == ("wait", "boss@b.c", "принял")
        assert cur["ts"].endswith("Z")

    def test_every_listed_status_can_be_set(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="баг")
        post_id = board.all()[0]["id"]

        for key in STATUSES:
            assert board.set_status(post_id, author="b@b.c", key=key, why=f"причина {key}") == ""

        assert current_status(board.get(post_id))["key"] == "wontfix"

    def test_the_whole_status_trail_survives(self, tmp_path) -> None:
        """Прошлые пояснения не теряются: история смены статусов целиком."""
        board = _board(tmp_path)
        board.add(author="a@b.c", text="баг")
        post_id = board.all()[0]["id"]
        board.set_status(post_id, author="b@b.c", key="wait", why="принял")
        board.set_status(post_id, author="b@b.c", key="done", why="чиню завтра")

        history = board.get(post_id)["history"]

        assert [h["key"] for h in history] == ["wait", "done"]
        assert [h["why"] for h in history] == ["принял", "чиню завтра"]


class TestModeration:
    def test_the_admin_drops_a_post_with_its_whole_thread(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="мусор")
        board.add(author="b@b.c", text="норм")
        victim = board.all()[0]["id"]

        assert board.drop(victim) is True

        assert [p["text"] for p in board.all()] == ["норм"]
        assert board.get(victim) is None

    def test_dropping_a_missing_post_says_no(self, tmp_path) -> None:
        assert _board(tmp_path).drop("нет-такого") is False


class TestDurability:
    def test_a_post_survives_a_new_store_instance(self, tmp_path) -> None:
        """Общий разговор: второй заход видит то, что написал первый."""
        _board(tmp_path).add(author="a@b.c", text="живу")

        reopened = _board(tmp_path).all()

        assert reopened[0]["text"] == "живу"

    def test_the_file_is_plain_readable_json(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="просто текст")

        data = json.loads((tmp_path / "board.json").read_text(encoding="utf-8"))

        assert data["posts"][0]["text"] == "просто текст"

    def test_no_temporary_files_are_left_behind(self, tmp_path) -> None:
        board = _board(tmp_path)
        board.add(author="a@b.c", text="раз")
        board.set_status(board.all()[0]["id"], author="b@b.c", key="wait", why="ок")

        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "board.json"]

        assert leftovers == []
