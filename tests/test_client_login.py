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


class TestRegistry:
    """Реестр доступа: зарегистрирован? заблокирован?"""

    @staticmethod
    def _client(
        *,
        blocked: bool = False,
        missing: bool = False,
        broken: bool = False,
        has_field: bool = True,
        superuser: bool = False,
    ) -> PocketBase:
        import json as _json

        puts: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/auth-with-password"):
                return httpx.Response(
                    200, json={"token": "t", "record": {"id": "u0", "email": "root@b.c"}}
                )
            if broken:
                return httpx.Response(500, json={"message": "сломалось"})
            if path == "/api/collections/_superusers/records/u1":
                if superuser:
                    return httpx.Response(200, json={"id": "u1", "email": "a@b.c"})
                return httpx.Response(404, json={"message": "not found"})
            if path == "/api/collections/users/records/u1":
                if missing:
                    return httpx.Response(404, json={"message": "not found"})
                if request.method == "PATCH":
                    body = _json.loads(request.content)
                    return httpx.Response(200, json={"id": "u1", **body})
                return httpx.Response(200, json={"id": "u1", "email": "a@b.c", "blocked": blocked})
            if path == "/api/collections/users/records" and request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"id": "u1", "email": "a@b.c", "blocked": False},
                            {"id": "u2", "email": "x@y.z", "blocked": True},
                        ]
                    },
                )
            if path == "/api/collections/users" and request.method == "GET":
                fields = (
                    [{"name": "id"}, {"name": "email"}, {"name": "blocked", "type": "bool"}]
                    if has_field
                    else [{"name": "id"}, {"name": "email"}]
                )
                return httpx.Response(200, json={"name": "users", "type": "auth", "fields": fields})
            if path == "/api/collections/users" and request.method == "PUT":
                puts.append(_json.loads(request.content))
                return httpx.Response(200, json={"name": "users"})
            return httpx.Response(404, json={"message": "not found"})

        client = PocketBase("https://server.test", transport=httpx.MockTransport(handler))
        client.login("root@b.c", "secret")  # реестр — только для вошедших
        client.seen_puts = puts
        return client

    def test_a_registered_person_is_ok(self) -> None:
        assert self._client().access_state("u1") == "ok"

    def test_a_deleted_person_is_missing(self) -> None:
        assert self._client(missing=True).access_state("u1") == "missing"

    def test_a_superuser_is_registered_by_definition(self) -> None:
        """Суперпользо­ватель живёт в отдельной коллекции — и он в порядке."""
        assert self._client(missing=True, superuser=True).access_state("u1") == "ok"

    def test_a_blocked_person_is_blocked(self) -> None:
        assert self._client(blocked=True).access_state("u1") == "blocked"

    def test_a_broken_registry_is_an_error_not_a_guess(self) -> None:
        with pytest.raises(ServerError):
            self._client(broken=True).access_state("u1")

    def test_blocking_reaches_the_registry(self) -> None:
        client = self._client()

        client.set_blocked("u1", True)

        # Важно, что PATCH ушёл и не упал; состояние проверяет access_state.
        assert client.seen_puts == []  # схему при этом не трогали

    def test_people_list_carries_the_blocked_flag(self) -> None:
        people = {item["id"]: item for item in self._client().people()}
        assert people["u1"]["blocked"] is False
        assert people["u2"]["blocked"] is True

    def test_the_blocked_field_is_created_when_missing(self) -> None:
        """Коллекция без поля blocked — приставка добавит его при старте."""
        client = self._client(has_field=False)

        assert client.ensure_blocked_field() is True

        assert client.seen_puts[0]["fields"][-1]["name"] == "blocked"

    def test_an_existing_schema_is_not_touched(self) -> None:
        assert self._client(has_field=True).ensure_blocked_field() is False


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


class TestTokenExpiry:
    """Токен PocketBase живёт не вечно — приставка входит заново сама.

    Регрессия: служба работала неделями, токен протухал, и журнал
    заполнялся «HTTP 401: Missing or invalid authentication», реестр
    доступа вставал на паузу — все токены получали 403 до рестарта.
    """

    @staticmethod
    def _expiring(*, relgin_fails: bool = False):
        """Сервер: первый вход выдаёт токен «old», он протухает, повторный — «new»."""
        state = {"tokens": 0, "logins": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth-with-password"):
                state["logins"] += 1
                if relgin_fails and state["logins"] > 1:
                    return httpx.Response(400, json={"message": "Failed to authenticate."})
                token = "old" if state["logins"] == 1 else "new"
                return httpx.Response(
                    200,
                    json={"token": token, "record": {"id": "u1", "email": "a@b.c"}},
                )
            if request.headers.get("Authorization") != "new":
                return httpx.Response(401, json={"message": "Missing or invalid authentication."})
            state["tokens"] += 1
            return httpx.Response(200, json={"id": "u1", "email": "a@b.c", "blocked": False})

        return _server(handler), state

    def test_an_expired_token_triggers_relogin_and_a_retry(self) -> None:
        client, state = self._expiring()
        client.login("a@b.c", "secret")
        state["logins"] = 1

        assert client.access_state("u1") == "ok"

        # Запрос прошёл: старый токен отвергли, вход повторили, повтор удался.
        assert state["logins"] == 2
        assert state["tokens"] == 1
        assert client._token == "new"

    def test_only_one_relogin_for_many_threads(self) -> None:
        """Потоки, долбившие реестр одновременно, входят один раз."""
        client, state = self._expiring()
        client.login("a@b.c", "secret")
        state["logins"] = 1

        assert client.access_state("u1") == "ok"
        assert client.access_state("u1") == "ok"  # второй — уже по свежему токену

        assert state["logins"] == 2

    def test_a_failed_relogin_is_an_honest_error(self) -> None:
        """Пароль сменили — честный ServerError, а не тихая пустота."""
        client, _ = self._expiring(relgin_fails=True)
        client.login("a@b.c", "secret")

        with pytest.raises(ServerError, match="Failed to authenticate"):
            client.access_state("u1")

    def test_no_saved_credentials_means_a_clear_error(self) -> None:
        """Токен есть, а учётных данных для повторного входа нет."""
        client, state = self._expiring()
        client.login("a@b.c", "secret")
        client._email = client._password = ""  # токен протух, а перелогиниться нечем

        with pytest.raises(ServerError, match="перезапустите приставку"):
            client.access_state("u1")
        assert state["logins"] == 1  # новых попыток входа не было


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
