"""Telegram-канал уведомлений: два вызова API, никакого вебхука.

Здесь важна не доставка как таковая (это за пределами машины), а
договорённости: ошибки сети и отказы Telegram превращаются в понятную
причину, а не в исключение; «узнать chat_id» читает getUpdates без
сервера снаружи; пустой токен — честный отказ, а не попытка ходить
в пустоту.
"""

import httpx

from providers.telegram import hello, last_chat_id, send


class TestSend:
    def test_a_message_goes_out_with_token_and_chat(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["params"] = dict(httpx.QueryParams(request.url.query))
            return httpx.Response(200, json={"ok": True, "result": {}})

        error = send("123:ABC", "42", "привет",
                     transport=httpx.MockTransport(handler))

        assert error is None
        assert "/bot123:ABC/sendMessage" in seen["url"]
        assert seen["params"] == {"chat_id": "42", "text": "привет"}

    def test_a_refusal_is_a_reason_not_an_exception(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={
                "ok": False, "description": "chat not found",
            })

        error = send("123:ABC", "42", "куда-то",
                     transport=httpx.MockTransport(handler))

        assert "chat not found" in error

    def test_no_network_returns_a_reason(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("никак")

        error = send("123:ABC", "42", "привет",
                     transport=httpx.MockTransport(handler))

        assert "нет связи" in error

    def test_an_empty_token_or_chat_is_refused_locally(self) -> None:
        assert "токен" in str(send("", "42", "привет"))
        assert "chat_id" in str(send("123:ABC", "  ", "привет"))


class TestDetectChatId:
    def test_the_latest_writers_chat_is_taken(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 10, "message": {"chat": {"id": 111}}},
                {"update_id": 20, "message": {"chat": {"id": 222}}},
            ]})

        chat_id, error = last_chat_id("123:ABC",
                                      transport=httpx.MockTransport(handler))

        assert error is None
        assert chat_id == "222"  # свежее сообщение, а не первое

    def test_an_empty_updates_says_write_to_the_bot(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True, "result": []})

        chat_id, error = last_chat_id("123:ABC",
                                      transport=httpx.MockTransport(handler))

        assert chat_id is None
        assert "не писали" in error

    def test_a_bad_token_is_passed_through(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={
                "ok": False, "description": "Unauthorized",
            })

        chat_id, error = last_chat_id("плохой",
                                      transport=httpx.MockTransport(handler))

        assert chat_id is None
        assert "Unauthorized" in error


class TestHello:
    def test_a_live_bot_comes_back_with_its_name(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "ok": True, "result": {"username": "my_bridge_bot"},
            })

        name, error = hello("123:ABC", transport=httpx.MockTransport(handler))

        assert error is None
        assert name == "@my_bridge_bot"

    def test_a_wrong_token_is_a_reason(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={
                "ok": False, "description": "Not Found",
            })

        name, error = hello("плохой", transport=httpx.MockTransport(handler))

        assert name is None
        assert "Not Found" in error
