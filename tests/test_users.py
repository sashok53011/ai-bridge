"""Люди приставки: профили, список для админа, вычищение личного."""

from storage.users import UserStore, user_dir


class TestUserStore:
    def test_a_first_login_creates_the_profile(self, tmp_path) -> None:
        store = UserStore(tmp_path)

        profile = store.remember("u1", "a@b.c", role="admin")

        assert profile["role"] == "admin"
        assert store.get("u1")["email"] == "a@b.c"
        assert (user_dir("u1", tmp_path) / "profile.json").exists()

    def test_a_second_login_keeps_creation_time(self, tmp_path) -> None:
        store = UserStore(tmp_path)
        first = store.remember("u1", "a@b.c")

        second = store.remember("u1", "a@b.c")

        assert second["created"] == first["created"]

    def test_the_admin_sees_everyone_sorted_byseniority(self, tmp_path) -> None:
        store = UserStore(tmp_path)
        store.remember("u2", "second@b.c")
        store.remember("u1", "first@b.c")

        listed = store.list_all()

        assert [item["email"] for item in listed] == ["second@b.c", "first@b.c"]

    def test_purge_removes_the_whole_personal_space(self, tmp_path) -> None:
        store = UserStore(tmp_path)
        store.remember("u1", "a@b.c")
        personal = user_dir("u1", tmp_path)
        (personal / "keys.json").write_text("{}", encoding="utf-8")

        store.purge("u1")

        assert store.get("u1") is None
        assert not personal.exists()
