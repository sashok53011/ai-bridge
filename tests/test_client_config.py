"""Отправка на сервер: переключатели обычного пользователя и каталог админа.

PUT /api/ai-config живёт двух жизней: обычному пользователю сервер хранит
флаги и превращает незнакомые записи в предложения, а суперпользо­вателю
заменяет каталог целиком (чего нет — в корзину). Поэтому у клиента два
метода с зеркальными защитами.
"""

import httpx
import pytest

from providers.client import PocketBase, ServerError, _sync_shape


def _server(handler) -> PocketBase:
    return PocketBase("https://server.test", transport=httpx.MockTransport(handler))


def _entry(provider_id: str = "ollama", **kwargs) -> dict:
    base = {
        "id": provider_id,
        "name": "Ollama",
        "url": "http://localhost:11434/v1",
        "model": "llama3.2",
        "fallbackPriority": 6,
        "loginUrl": "",
        "getApiKeyUrl": "",
        "logoUrl": "",
        "logoAsset": "ollama",
        "status": "active",
        "origin": "admin",
        "authorName": "",
        "enabled": False,
    }
    base.update(kwargs)
    return base


def _config_server(captured: dict, *, can_edit: bool, providers: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth-with-password"):
            return httpx.Response(
                200, json={"token": "t", "record": {"id": "u1", "email": "a@b.c"}}
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"canEdit": can_edit, "updated": "", "providers": providers or []},
            )
        import json as _json

        captured["json"] = _json.loads(request.content)
        return httpx.Response(200, json={"canEdit": can_edit, "updated": "", "providers": []})

    return _server(handler)


def _logged_in_server(captured: dict, *, can_edit: bool) -> PocketBase:
    server = _config_server(captured, can_edit=can_edit)
    server.login("a@b.c", "secret")
    # Каталог запоминает canEdit — без него защиты не знают, кто перед ними.
    server.fetch_catalogue()
    return server


class TestPushCatalogue:
    def test_a_superuser_sends_the_whole_catalogue(self) -> None:
        captured: dict = {}
        server = _logged_in_server(captured, can_edit=True)

        server.push_catalogue([_entry(), _entry("openai", name="OpenAI")])

        sent = captured["json"]["providers"]
        assert [entry["id"] for entry in sent] == ["ollama", "openai"]
        assert sent[0]["name"] == "Ollama"

    def test_a_regular_user_cannot_replace_the_catalogue(self) -> None:
        captured: dict = {}
        server = _logged_in_server(captured, can_edit=False)

        with pytest.raises(ServerError, match="только суперпользо­ватель"):
            server.push_catalogue([_entry()])

        # Ни одного PUT: защита сработала до отправки.
        assert "json" not in captured


class TestSaveFlags:
    def test_the_superuser_cannot_push_flags(self) -> None:
        """Его PUT заменил бы каталог — раньше сервера отвергает клиент."""
        captured: dict = {}
        server = _logged_in_server(captured, can_edit=True)

        with pytest.raises(ServerError, match="заменила бы каталог"):
            server.save_flags([_entry(enabled=True)])

        assert "json" not in captured

    def test_a_regular_user_sends_full_visible_entries(self) -> None:
        captured: dict = {}
        server = _logged_in_server(captured, can_edit=False)

        server.save_flags([_entry(enabled=True)])

        sent = captured["json"]["providers"]
        assert sent[0]["id"] == "ollama"
        assert sent[0]["enabled"] is True
        # Полные данные, а не заглушки: сервер по ним строит предложения.
        assert sent[0]["url"] == "http://localhost:11434/v1"
        assert sent[0]["model"] == "llama3.2"


class TestSyncShape:
    def test_known_fields_travel_and_secrets_do_not(self) -> None:
        shaped = _sync_shape(_entry(api_key="sk-secret", apiKey="sk-2", enabled=True))

        assert shaped["id"] == "ollama"
        assert shaped["enabled"] is True
        assert "api_key" not in shaped
        assert "apiKey" not in shaped
