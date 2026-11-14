"""Веб-интерфейс: вход людей, личные пространства, админские действия.

Страница требует входа со всех адресов; cookie HttpOnly + SameSite=Strict,
чужой Origin отклоняется. Админ правит каталог, человек ведёт своё
пространство: ключи, переключатели, токены.

Тесты поднимают настоящий HTTP-сервер на свободном порту; каталог
подменяется на httpx.MockTransport, чтобы сеть не участвовала.
"""

import threading

import httpx
import pytest

from providers.client import PocketBase
from service.gateway import _Handler, _Server
from service.space import Spaces
from service.webui import WebUi
from storage.sessions import SessionStore
from storage.usertokens import UserTokens
from storage.users import UserStore

_DEVICE_TOKEN = "t-Device"
_ADMIN = "u1"
_GUEST = "u2"


def _entry(**kwargs) -> dict:
    base = {
        "id": "ollama",
        "name": "Ollama",
        "url": "http://ollama.test/v1",
        "model": "llama3.2",
        "fallbackPriority": 6,
        "status": "active",
        "origin": "admin",
        "enabled": True,
        "mine": False,
    }
    base.update(kwargs)
    return base


@pytest.fixture(autouse=True)
def _device(tmp_path, monkeypatch):
    """Каждый тест работает на своём временном устройстве."""
    monkeypatch.setenv("AI_BRIDGE_HOME", str(tmp_path))
    return tmp_path


class _FakeProvider:
    """Провайдер без сети: отдаёт заранее заданный список моделей."""

    def __init__(self, models: list[str]) -> None:
        self._models = models

    def fetch_models(self, api_key: str = "") -> list[dict]:
        return [{"name": name} for name in self._models]


def _make_provider(url: str, **kwargs):
    return _FakeProvider(["qwen3", "llama3.2"])


class _CatalogueMock:
    """Клиент каталога: GET отдаёт текущий список, PUT и DELETE запоминает."""

    def __init__(self, entries: list[dict], *, can_edit: bool):
        import json as _json

        self.puts: list[list[dict]] = []
        self.deleted: list[str] = []
        self.blocked = False
        self.seen: list[dict] = []
        state = {"providers": [dict(entry) for entry in entries]}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth-with-password"):
                return httpx.Response(
                    200, json={"token": "t", "record": {"id": _ADMIN, "email": "a@b.c"}}
                )
            if request.url.path == "/api/ai-stats" and request.method == "GET":
                return httpx.Response(200, json=self.stats)
            if request.method == "POST":
                body = _json.loads(request.content) if request.content else {}
                self.seen.append({"method": "POST", "path": request.url.path, "body": body})
            if request.url.path == "/api/ai-config" and request.method == "PUT":
                body = _json.loads(request.content)
                state["providers"] = list(body.get("providers") or [])
                self.puts.append(state["providers"])
                return httpx.Response(
                    200, json={"canEdit": can_edit, "updated": "", "providers": state["providers"]}
                )
            if request.url.path == "/api/collections/users/records" and request.method == "GET":
                return httpx.Response(
                    200,
                    json={"items": [{"id": _GUEST, "email": "guest@mail", "blocked": self.blocked}]},
                )
            if request.method == "PATCH" and "/api/collections/users/records/" in request.url.path:
                body = _json.loads(request.content)
                self.blocked = bool(body.get("blocked"))
                return httpx.Response(200, json={"id": _GUEST, **body})
            if request.method == "DELETE" and "/api/collections/users/records/" in request.url.path:
                self.deleted.append(request.url.path.rsplit("/", 1)[-1])
                return httpx.Response(200, json={})
            return httpx.Response(
                200, json={"canEdit": can_edit, "updated": "", "providers": state["providers"]}
            )

        self.stats: dict = {}
        self.client = PocketBase("https://server.test", transport=httpx.MockTransport(handler))
        self.client.login("a@b.c", "secret")
        self.client.fetch_catalogue()


def _harness(entries: list[dict], **kwargs):
    """Веб-слой на настоящем HTTP-порту — как в жизни, но без сети."""
    can_edit = kwargs.pop("can_edit", True)
    authenticator = kwargs.pop(
        "authenticator", lambda email, password: (_GUEST, email, False)
    )
    signup_hook = kwargs.pop("signup_hook", lambda email, password: None)
    catalogue = _CatalogueMock(entries, can_edit=can_edit)

    state = {"entries": [dict(entry) for entry in entries]}

    def source() -> list[dict]:
        # Обновлённый список: после админского PUT сервер отдаёт новый.
        if catalogue.puts:
            return [dict(entry) for entry in catalogue.puts[-1]]
        return [dict(entry) for entry in state["entries"]]

    ui = WebUi(
        source,
        catalogue.client,
        spaces=Spaces(admin_id=_ADMIN),
        sessions=SessionStore(),
        user_tokens=UserTokens(),
        users=UserStore(),
        admin_id=_ADMIN,
        device_token=_DEVICE_TOKEN,
        bound_host="127.0.0.1",
        authenticator=authenticator,
        signup_hook=signup_hook,
        **kwargs,
    )
    server = _Server(("127.0.0.1", 0), _Handler)
    server.gateway = None  # веб-слою API не нужен
    server.ui = ui
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return catalogue, server, base


@pytest.fixture
def harness(tmp_path):
    holders = []

    def build(entries, **kwargs):
        item = _harness(entries, **kwargs)
        holders.append(item)
        return item[0], item[2]  # (каталог-мок, base)

    yield build
    for _, server, _ in holders:
        server.shutdown()
        server.server_close()


def _client(base: str, *, follow: bool = False) -> httpx.Client:
    """Клиент без авто-редиректа: тесты проверяют коды ответов напрямую."""
    return httpx.Client(base_url=base, follow_redirects=follow)


def _as_admin(client: httpx.Client) -> None:
    """Вход админа: аварийный токен устройства = его личный токен."""
    response = client.post("/login", data={"token": _DEVICE_TOKEN})
    assert response.status_code == 303  # cookie в заголовке, дальше GET /


def _as_guest(client: httpx.Client, email: str = "guest@mail") -> None:
    response = client.post("/login", data={"email": email, "password": "pw"})
    assert response.status_code == 303


class TestPages:
    """Страницы провайдера: ключ, модель, правка."""

    def test_get_pick_page_shows_models(self, harness) -> None:
        """Регрессия: GET /pick/ падал с NameError — список моделей не приходил."""
        _, base = harness([_entry()], make_provider=_make_provider)
        with _client(base) as client:
            _as_guest(client)
            response = client.get("/pick/ollama")

        assert response.status_code == 200
        assert "qwen3" in response.text

    def test_get_pick_page_works_for_the_admin_too(self, harness) -> None:
        _, base = harness([_entry()], make_provider=_make_provider)
        with _client(base) as client:
            _as_admin(client)
            response = client.get("/pick/ollama")

        assert response.status_code == 200
        assert "qwen3" in response.text

    def test_get_edit_page_opens_for_admin(self, harness) -> None:
        """Регрессия: GET /edit/ отдавал SQL-страницу и падал."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            response = client.get("/edit/ollama")

        assert response.status_code == 200
        assert "показать модели" in response.text

    def test_get_edit_page_is_admin_only(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            response = client.get("/edit/ollama")

        assert response.status_code == 403

    def test_get_key_page_opens(self, harness) -> None:
        """Регрессия: /key/ пропал из handle_get — кнопка «ключ» вела в 404."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            response = client.get("/key/ollama")

        assert response.status_code == 200
        assert "Ollama" in response.text


class TestRemove:
    """Удаление провайдера: только админ, только POST."""

    def test_admin_removes_a_provider(self, harness) -> None:
        """Регрессия: POST /remove/ падал с NameError admin_space."""
        catalogue, base = harness(
            [_entry(), _entry(id="b", name="B", url="http://b/v1")]
        )
        with _client(base) as client:
            _as_admin(client)
            response = client.post("/remove/ollama")

        assert response.status_code == 303
        assert [entry["id"] for entry in catalogue.puts[-1]] == ["b"]

    def test_a_guest_cannot_remove(self, harness) -> None:
        catalogue, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            response = client.post("/remove/ollama")

        assert response.status_code == 403
        assert catalogue.puts == []

    def test_get_remove_does_not_delete(self, harness) -> None:
        """Через GET ничего не удаляется: дубль обработчика это делал."""
        catalogue, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            response = client.get("/remove/ollama")

        assert response.status_code == 404
        assert catalogue.puts == []


class TestEditFetch:
    """«Показать модели» в форме правки."""

    def test_admin_fetches_models_in_edit_form(self, harness) -> None:
        """Регрессия: POST /edit/ падал с NameError admin_space."""
        _, base = harness([_entry()], make_provider=_make_provider)
        with _client(base) as client:
            _as_admin(client)
            response = client.post(
                "/edit/ollama",
                data={"action": "fetch", "url": "http://ollama.test/v1"},
            )

        assert response.status_code == 200
        assert "qwen3" in response.text


class TestGuards:
    """Админские действия закрыты для обычного человека."""

    def test_a_guest_cannot_delete_users(self, harness) -> None:
        """Регрессия: админский шлюз пропал из handle_post."""
        catalogue, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            response = client.post("/users/delete", data={"id": _GUEST})

        assert response.status_code == 403
        assert catalogue.deleted == []

    def test_a_guest_opens_sql_playground(self, harness) -> None:
        """SQL Playground персональный: человек открывает свою базу."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            response = client.get("/sql")

        assert response.status_code == 200
