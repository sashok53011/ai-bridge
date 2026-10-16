"""Аккаунты: сессии и роли — чистая логика, без файлов и сети."""

from datetime import datetime, timedelta, timezone

from core.accounts import (
    ROLE_ADMIN,
    ROLE_USER,
    is_admin,
    make_session,
    session_alive,
    session_user,
)


def _moment() -> datetime:
    return datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


class TestSessions:
    def test_a_session_belongs_to_its_owner_with_a_role(self) -> None:
        session = make_session("u1", "a@b.c", ROLE_ADMIN, now=_moment())

        assert session["userId"] == "u1"
        assert session["email"] == "a@b.c"
        assert is_admin(session) is True
        assert session_user(session) == "u1"

    def test_a_regular_session_is_not_admin(self) -> None:
        session = make_session("u2", "x@y.z", ROLE_USER, now=_moment())
        assert is_admin(session) is False

    def test_a_session_expires_after_a_month(self) -> None:
        session = make_session("u1", "a@b.c", ROLE_USER, now=_moment())

        later = _moment() + timedelta(days=29)
        almost = _moment() + timedelta(days=31)
        assert session_alive(session, now=later) is True
        assert session_alive(session, now=almost) is False

    def test_garbage_is_never_a_session(self) -> None:
        assert session_alive(None) is False
        assert session_alive({}) is False
        assert session_alive({"expires": "не дата"}) is False
        assert is_admin(None) is False
        assert session_user(None) == ""

    def test_ids_are_unique_and_unguessable(self) -> None:
        first = make_session("u1", "a@b.c", ROLE_USER, now=_moment())
        second = make_session("u1", "a@b.c", ROLE_USER, now=_moment())
        assert first["id"] != second["id"]
        assert len(first["id"]) >= 32
