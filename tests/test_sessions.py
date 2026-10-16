"""Сессии страницы на диске: живые хранятся, просроченные вычищаются."""

from datetime import datetime, timedelta, timezone

from core.accounts import ROLE_ADMIN, make_session
from storage.sessions import SessionStore


def _session(user_id: str = "u1", *, alive_days: int = 1) -> dict:
    now = datetime.now(timezone.utc)
    session = make_session(user_id, "a@b.c", ROLE_ADMIN, now=now)
    if alive_days < 0:
        session["expires"] = (now + timedelta(days=alive_days)).isoformat().replace(
            "+00:00", "Z"
        )
    return session


class TestSessionStore:
    def test_a_saved_session_comes_back(self, tmp_path) -> None:
        store = SessionStore(tmp_path)
        session = _session()

        store.add(session)

        assert store.get(session["id"])["userId"] == "u1"

    def test_an_unknown_id_is_not_a_session(self, tmp_path) -> None:
        assert SessionStore(tmp_path).get("нет-такого") is None
        assert SessionStore(tmp_path).get("") is None

    def test_an_expired_session_is_invisible_and_cleaned(self, tmp_path) -> None:
        store = SessionStore(tmp_path)
        dead = _session(alive_days=-1)
        store.add(dead)

        assert store.get(dead["id"]) is None

        store.add(_session())  # живая запись вычищает просроченные
        import json

        data = json.loads(store.path.read_text(encoding="utf-8"))
        assert dead["id"] not in data

    def test_dropping_one_and_all_of_a_user(self, tmp_path) -> None:
        store = SessionStore(tmp_path)
        first, second = _session("u1"), _session("u1")
        other = _session("u2")
        for session in (first, second, other):
            store.add(session)

        store.drop(first["id"])
        assert store.get(first["id"]) is None
        assert store.get(second["id"]) is not None

        store.drop_user("u1")
        assert store.get(second["id"]) is None
        assert store.get(other["id"]) is not None
