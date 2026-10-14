"""Веб-интерфейс: маршруты форм и защита локальной службы.

Две линии обороны проверяются отдельно: чужой Host (DNS-rebinding) и
чужой Origin (нажатие кнопок со страницы-обманки).
"""

import threading

import httpx
import pytest

from providers.client import PocketBase, Provider
from service import ops
from service.gateway import _Handler, _Server
from service.webui import WebUi
from storage.keys import KeyStore


def _entry(**kwargs) -> dict:
    base = {
        "id": "ollama",
        "name": "Ollama",
        "url": "http://ollama.test/v1",
        "model": "llama3.2",
        "fallbackPriority": 6,
        "status": "active",
        "origin": "admin",
        "enabled": False,
        "mine": False,
    }
    base.update(kwargs)
    return base


@pytest.fixture(autouse=True)
def _device(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_BRIDGE_HOME", str(tmp_path))
    return tmp_path


class _Catalogue:
    """Список, который читается так же, как в жизни: каталог + снапшот.

    ``ops`` пишет итог в снапшот на диске — значит и читать надо через
    ``ops.merge_view``, иначе тест проверяет не тот путь.
    """

    def __init__(self, entries: list[dict], server: PocketBase, can_edit: bool):
        self.entries = entries
        self._server = server
        self._can_edit = can_edit

    def __call__(self) -> list[dict]:
        return ops.merge_view(
            self._server, self.entries, can_edit=self._can_edit, server_flags={}
        )


def _server(entries: list[dict], *, can_edit: bool = False, make_provider=None):
    """Залогиненный клиент каталога для веб-слоя."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth-with-password"):
            return httpx.Response(
                200, json={"token": "t", "record": {"id": "u1", "email": "a@b.c"}}
            )
        return httpx.Response(200, json={"canEdit": can_edit, "updated": "", "providers": []})

    client = PocketBase("https://server.test", transport=httpx.MockTransport(handler))
    client.login("a@b.c", "secret")
    client.fetch_catalogue()
    return client


def _harness(entries: list[dict], **kwargs):
    """Веб-слой на настоящем HTTP-порту — как в жизни, но без сети."""
    can_edit = kwargs.pop("can_edit", False)
    keys = KeyStore()
    client = _server(entries, can_edit=can_edit)
    catalogue = _Catalogue(entries, client, can_edit)
    ui = WebUi(
        catalogue,
        keys,
        client,
        can_edit=can_edit,
        bound_host="127.0.0.1",
        **kwargs,
    )
    server = _Server(("127.0.0.1", 0), _Handler)
    server.gateway = None  # веб-слойу API не нужен
    server.ui = ui
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return catalogue, keys, server, base


@pytest.fixture
def harness(tmp_path):
    holders = []

    def build(entries, **kwargs):
        item = _harness(entries, **kwargs)
        holders.append(item)
        return item

    yield build
    for _, _, server, _ in holders:
        server.shutdown()
        server.server_close()


class TestPages:
    def test_the_catalogue_renders(self, harness) -> None:
        _, _, _, base = harness([_entry()])
        with httpx.Client(base_url=base) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert "Ollama" in response.text

    def test_a_toggle_flips_the_flag_and_redirects(self, harness) -> None:
        catalogue, _, _, base = harness([_entry()])
        with httpx.Client(base_url=base, follow_redirects=False) as client:
            response = client.post("/toggle/ollama")
        assert response.status_code == 303
        # Итог живёт в снапшоте — читаем тем же путём, что и страница.
        assert catalogue()[0]["enabled"] is True

    def test_a_check_uses_the_stored_key_only(self, harness) -> None:
        """Ключ уходит к провайдеру, на страницу не возвращается."""
        catalogue, keys, _, base = harness(
            [_entry()],
            make_provider=lambda url, **kw: _fake_provider(url, models=["llama3.2"], **kw),
        )
        keys.set("ollama", "sk-secret")
        with httpx.Client(base_url=base, follow_redirects=True) as client:
            response = client.post("/check/ollama")
        assert "sk-secret" not in response.text
        assert catalogue()  # список жив

    def test_adding_a_provider_from_the_form(self, harness) -> None:
        catalogue, _, _, base = harness([_entry()])
        with httpx.Client(base_url=base, follow_redirects=False) as client:
            response = client.post(
                "/add",
                data={"name": "Мой LMS", "url": "http://127.0.0.1:1234/v1", "model": "qwen3"},
            )
        assert response.status_code == 303
        assert any(entry["id"] == "moy-lms" for entry in catalogue())

    def test_saving_a_key_keeps_it_on_the_device(self, harness) -> None:
        _, keys, _, base = harness([_entry()])
        with httpx.Client(base_url=base, follow_redirects=False) as client:
            client.post("/key", data={"id": "ollama", "api_key": "sk-local"})
        assert keys.get("ollama") == "sk-local"

    def test_an_unknown_page_is_a_404(self, harness) -> None:
        _, _, _, base = harness([_entry()])
        with httpx.Client(base_url=base) as client:
            assert client.get("/нет-такого").status_code == 404


def _fake_provider(url: str, *, models=None, **kwargs) -> Provider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": name} for name in (models or [])]})

    return Provider(url, transport=httpx.MockTransport(handler), **kwargs)


class TestTokenLogin:
    """На не-loopback адресе страница требует вход по токену."""

    def test_a_fresh_browser_sees_the_login_form(self, harness) -> None:
        _, _, _, base = harness([_entry()], require_token=True, token="s3cret")
        with httpx.Client(base_url=base) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert "токен" in response.text
        assert "Ollama" not in response.text  # каталог до входа не показывается

    def test_a_wrong_token_stays_out(self, harness) -> None:
        _, _, _, base = harness([_entry()], require_token=True, token="s3cret")
        with httpx.Client(base_url=base, follow_redirects=False) as client:
            response = client.post("/login", data={"token": "wrong"})
        assert response.status_code == 200
        assert "неверный токен" in response.text

    def test_the_right_token_sets_a_cookie_and_opens_the_catalogue(self, harness) -> None:
        _, _, _, base = harness([_entry()], require_token=True, token="s3cret")
        with httpx.Client(base_url=base, follow_redirects=False) as client:
            response = client.post("/login", data={"token": "s3cret"})
            assert response.status_code == 303
            cookie = response.headers.get("set-cookie", "")
            assert "HttpOnly" in cookie and "SameSite=Strict" in cookie

            page = client.get("/", headers={"Cookie": f"ai_bridge_session=s3cret"})
            assert "Ollama" in page.text

    def test_actions_need_the_session_too(self, harness) -> None:
        catalogue, _, _, base = harness([_entry()], require_token=True, token="s3cret")
        with httpx.Client(base_url=base, follow_redirects=False) as client:
            # Без cookie тумблер не проходит.
            response = client.post("/toggle/ollama")
            assert response.status_code == 200  # форма входа, не действие
            assert catalogue()[0]["enabled"] is False

            # С cookie — проходит.
            client.post("/login", data={"token": "s3cret"})
            response = client.post(
                "/toggle/ollama", headers={"Cookie": "ai_bridge_session=s3cret"}
            )
            assert response.status_code == 303
            assert catalogue()[0]["enabled"] is True

    def test_loopback_page_stays_open(self, harness) -> None:
        """На 127.0.0.1 вход не нужен — это ваша машина."""
        _, _, _, base = harness([_entry()])  # require_token по умолчанию False
        with httpx.Client(base_url=base) as client:
            assert "Ollama" in client.get("/").text


class TestProtection:
    def test_a_foreign_host_is_refused(self, harness) -> None:
        """DNS-rebinding: чужой домен, разрешившийся в 127.0.0.1."""
        _, _, _, base = harness([_entry()])
        with httpx.Client(base_url=base) as client:
            response = client.get("/", headers={"Host": "evil.example"})
        assert response.status_code == 403
        assert "evil" in response.text or "rebinding" in response.text

    def test_a_foreign_origin_cannot_press_buttons(self, harness) -> None:
        """Страница-обманка на чужом домене шлёт POST в наш порт."""
        catalogue, _, _, base = harness([_entry()])
        with httpx.Client(base_url=base, follow_redirects=False) as client:
            response = client.post(
                "/toggle/ollama",
                headers={"Origin": "http://evil.example"},
            )
        assert response.status_code == 403
        assert catalogue()[0]["enabled"] is False  # флаг не тронут

    def test_our_own_origin_is_welcome(self, harness) -> None:
        catalogue, _, _, base = harness([_entry()])
        port = base.rsplit(":", 1)[1]
        with httpx.Client(base_url=base, follow_redirects=False) as client:
            response = client.post(
                "/toggle/ollama",
                headers={"Origin": f"http://127.0.0.1:{port}"},
            )
        assert response.status_code == 303
        assert catalogue()[0]["enabled"] is True

    def test_the_api_routes_still_answer_without_the_ui(self, tmp_path) -> None:
        """API не зависит от веб-слоя: /v1/models работает и без него."""
        from service.gateway import Gateway

        gateway = Gateway(lambda: [_entry(enabled=True)], KeyStore())
        server = _Server(("127.0.0.1", 0), _Handler)
        server.gateway = gateway
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with httpx.Client(base_url=base) as client:
                data = client.get("/v1/models").json()
            assert data["data"] == [{"id": "llama3.2", "owned_by": "ollama"}]
        finally:
            server.shutdown()
            server.server_close()
