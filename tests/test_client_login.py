"""Вход: суперпользо­вель живёт в отдельной коллекции, а отказ сервера
не должен терять своё сообщение.

Реальный вывод, с которого началась эта история:

    uv run python app.py
    сервер: POST /api/collections/users/auth-with-password -> HTTP 400

400 здесь — не поломка и не опечатка: суперпользо­ватель PocketBase
(>= 0.23) входит через коллекцию `_superusers`, а сервер к тому же
присылает в ответе {"message": "Failed to authenticate."}, который
клиент раньше выбрасывал.
"""

import httpx
import pytest

from providers.client import PocketBase, ServerError, _failure


def _server(handler) -> PocketBase:
    return PocketBase("https://server.test", transport=httpx.MockTransport(handler))


class TestLoginCollections:
    @staticmethod
    def _users_only(request: httpx.Request) -> httpx.Response:
        """Так отвечает сервер на вход обычного пользователя."""
        if "/_superusers/" in request.url.path:
            return httpx.Response(404, json={"message": "The requested resource wasn't found."})
        return httpx.Response(
            200,
            json={"token": "user-token", "record": {"id": "u1", "email": "a@example.com"}},
        )

    def test_a_regular_user_logs_in_through_users(self) -> None:
        server = _server(self._users_only)
        server.login("a@example.com", "secret")
        assert server.user_id == "u1"
        assert server.user_name == "a@example.com"

    def test_a_superuser_falls_back_to_the_superuser_collection(self) -> None:
        """Ровно тот случай из жизни: 400 на входе у админского аккаунта."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "/_superusers/" in request.url.path:
                return httpx.Response(
                    200,
                    json={"token": "root-token", "record": {"id": "s1", "email": "root@example.com"}},
                )
            return httpx.Response(400, json={"message": "Failed to authenticate."})

        server = _server(handler)
        server.login("root@example.com", "secret")
        assert server.user_id == "s1"
        assert server.user_name == "root@example.com"

    def test_both_refusals_keep_the_server_messages(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"message": "Failed to authenticate."})

        server = _server(handler)
        with pytest.raises(ServerError) as excinfo:
            server.login("a@example.com", "wrong")

        text = str(excinfo.value)
        assert "users/auth-with-password" in text
        assert "_superusers/auth-with-password" in text
        assert "Failed to authenticate." in text

    def test_an_html_error_page_does_not_crash_the_client(self) -> None:
        """Промежуточный прокси может ответить HTML — и это тоже диагностика."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>bad gateway</html>")

        server = _server(handler)
        with pytest.raises(ServerError, match="HTTP 502"):
            server.login("a@example.com", "secret")


class TestFailureText:
    def test_the_server_message_goes_into_the_error(self) -> None:
        assert (
            _failure("POST", "/api/x", 400, {"message": "Failed to authenticate."})
            == "POST /api/x -> HTTP 400: Failed to authenticate."
        )

    def test_a_status_alone_is_still_reported(self) -> None:
        assert _failure("GET", "/api/x", 502, None) == "GET /api/x -> HTTP 502"

    def test_an_empty_message_adds_nothing(self) -> None:
        assert _failure("GET", "/api/x", 500, {"message": "  "}) == "GET /api/x -> HTTP 500"

    def test_a_foreign_body_type_is_ignored(self) -> None:
        assert _failure("GET", "/api/x", 500, ["не", "словарь"]) == "GET /api/x -> HTTP 500"
