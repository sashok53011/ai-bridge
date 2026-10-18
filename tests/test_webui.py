"""Веб-интерфейс: вход людей, личные пространства, роли, защита.

Страница требует входа со всех адресов; cookie HttpOnly + SameSite=Strict,
чужой Origin отклоняется. Админ правит каталог, человек ведёт своё
пространство: ключи, переключатели, токены.
"""

import os
import threading
from pathlib import Path

import httpx
import pytest

from providers.client import PocketBase
from service.gateway import _Handler, _Server
from service.space import Spaces
from service.webui import WebUi
from storage.keys import KeyStore
from storage.sessions import SessionStore
from storage.snapshot import Snapshot
from storage.usertokens import UserTokens
from storage.users import UserStore, user_dir

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
        "enabled": False,
        "mine": False,
    }
    base.update(kwargs)
    return base


@pytest.fixture(autouse=True)
def _device(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_BRIDGE_HOME", str(tmp_path))
    return tmp_path


def _root() -> Path:
    return Path(os.environ["AI_BRIDGE_HOME"])


class _CatalogueMock:
    """Клиент каталога: GET отдаёт текущий список, PUT и DELETE запоминает."""

    def __init__(self, entries: list[dict], *, can_edit: bool):
        import json as _json

        self.puts: list[list[dict]] = []
        self.deleted: list[str] = []
        self.blocked = False
        state = {"providers": [dict(entry) for entry in entries]}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth-with-password"):
                return httpx.Response(
                    200, json={"token": "t", "record": {"id": _ADMIN, "email": "a@b.c"}}
                )
            if request.url.path == "/api/ai-stats" and request.method == "GET":
                return httpx.Response(200, json=self.stats)
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
                    json={
                        "items": [
                            {"id": _GUEST, "email": "guest@mail", "blocked": self.blocked}
                        ]
                    },
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
    server.gateway = None  # веб-слойу API не нужен
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


class TestEntry:
    def test_an_anonymous_visitor_sees_the_login_page(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            response = client.get("/")
        assert response.status_code == 200
        assert 'action="/login"' in response.text

    def test_a_wrong_token_stays_out(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            response = client.post("/login", data={"token": "не тот"})
        assert "неверный токен" in response.text

    def test_the_right_token_opens_the_catalogue_as_admin(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            page = client.get("/")
        assert "Ollama" in page.text
        assert "администратор" in page.text

    def test_email_and_password_open_a_regular_space(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client, "anna@mail")
            page = client.get("/")
        assert "anna@mail" in page.text
        assert "человек" in page.text

    def test_a_bad_password_is_refused(self, harness) -> None:
        from providers.client import ServerError

        def authenticator(email, password):
            raise ServerError("не подошло")

        _, base = harness([_entry()], authenticator=authenticator)
        with _client(base) as client:
            response = client.post("/login", data={"email": "a@b.c", "password": "x"})
        assert "не подошли" in response.text

    def test_logout_ends_the_session(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            client.post("/logout")
            page = client.get("/")
        assert 'action="/login"' in page.text


class TestSignup:
    def test_registration_creates_an_account_and_signs_in(self, harness) -> None:
        calls = []
        _, base = harness(
            [_entry()],
            signup_hook=lambda email, password: calls.append(email),
            authenticator=lambda email, password: (_GUEST, email, False),
        )
        with _client(base) as client:
            client.post(
                "/signup",
                data={"email": "new@mail", "password": "pw123", "password2": "pw123"},
            )
            home = client.get("/")
        assert calls == ["new@mail"]
        assert "new@mail" in home.text

    def test_mismatched_passwords_never_reach_the_server(self, harness) -> None:
        calls = []
        _, base = harness([_entry()], signup_hook=lambda e, p: calls.append(e))
        with _client(base) as client:
            page = client.post(
                "/signup", data={"email": "new@mail", "password": "a", "password2": "b"}
            )
        assert calls == []
        assert "не совпали" in page.text


class TestProtection:
    def test_a_foreign_origin_cannot_press_buttons(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            response = client.post(
                "/toggle/ollama", headers={"Origin": "http://evil.example"}
            )
        assert response.status_code == 403

    def test_our_own_origin_is_welcome(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            port = base.rsplit(":", 1)[1]
            response = client.post(
                "/toggle/ollama", headers={"Origin": f"http://127.0.0.1:{port}"}
            )
        assert response.status_code == 303


class TestPersonalSpace:
    def test_a_guests_toggle_lives_in_their_own_space(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/toggle/ollama")

        guest = Snapshot(user_dir(_GUEST, _root()))
        assert guest.load()[0]["enabled"] is True
        assert Snapshot(_root()).load() == []  # пространство админа не тронуто

    def test_a_guest_cannot_edit_others_or_see_people(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            edit = client.post("/edit/ollama", data={"name": "X"})
            people = client.get("/users")
        assert edit.status_code == 403
        assert people.status_code == 403

    def test_a_guest_adds_a_personal_provider(self, harness) -> None:
        """Добавленное человеком живёт в ЕГО пространстве, каталог не тронут."""
        from storage.snapshot import Snapshot

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            response = client.post(
                "/add",
                data={
                    "name": "Мой LMS",
                    "url": "http://192.168.0.50:1234/v1",
                    "model": "qwen3",
                    "api_key": "sk-mine",
                    "action": "save",
                    "infoUrl": "https://o.com/pricing",
                    "pricing": "$0.1/1M",
                },
            )
        assert response.status_code == 303

        personal = Snapshot(user_dir(_GUEST, _root())).load()
        mine = next(item for item in personal if item["id"] == "moy-lms")
        assert mine["origin"] == "user"
        assert mine["authorId"] == _GUEST
        assert mine["enabled"] is True
        assert mine["infoUrl"] == "https://o.com/pricing"  # справка доехала
        assert mine["pricing"] == "$0.1/1M"
        assert Snapshot(_root()).load() == []  # админский каталог не тронут

        assert Spaces().for_user(_GUEST).keys.get("moy-lms") == "sk-mine"

    def test_a_guests_key_stays_in_their_keystore(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/key", data={"id": "ollama", "api_key": "sk-mine"})

        # Мастер-ключ устройства общий — читаем через Spaces, как в жизни.
        assert Spaces().for_user(_GUEST).keys.get("ollama") == "sk-mine"
        assert KeyStore(_root()).get("ollama") == ""  # у админа чужого ключа нет

    def test_a_guest_picks_their_own_model(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/pick/ollama", data={"model": "qwen3:0.6b"})

        guest = Snapshot(user_dir(_GUEST, _root())).load()
        assert guest[0]["model"] == "qwen3:0.6b"
        assert Snapshot(_root()).load() == []  # каталог не тронут


class TestLogs:
    def test_the_log_is_admin_only(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            page = client.get("/logs")
        assert page.status_code == 403

    def test_the_admin_sees_a_live_journal(self, harness) -> None:
        from service.eventlog import EventLog

        log = EventLog()
        _, base = harness([_entry()], event_log=log)
        with _client(base) as client:
            _as_admin(client)
            client.post("/toggle/ollama")
            page = client.get("/logs")

        assert "тумблер" in page.text
        assert 'http-equiv="refresh"' in page.text

    def test_a_login_lands_in_the_journal(self, harness) -> None:
        from service.eventlog import EventLog

        log = EventLog()
        _, base = harness([_entry()], event_log=log)
        with _client(base) as client:
            _as_guest(client, "anna@mail")

        kinds = [event["kind"] for event in log.recent()]
        assert "вход" in kinds

    def test_autoscroll_remembers_the_choice(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            response = client.post("/logs/autoscroll")

        assert response.status_code == 303
        assert "ai_bridge_autoscroll=on" in response.headers.get("set-cookie", "")


class TestAdminWritesKeepCatalogue:
    """Регрессия 16.10.2026: админская запись строится по свежему серверному списку."""

    def test_edit_sends_the_whole_catalogue(self, harness) -> None:
        entries = [
            _entry(),
            _entry(id="openai", name="OpenAI", url="https://api.openai.com/v1", model="gpt-4o-mini"),
        ]
        catalogue, base = harness(entries)

        with _client(base) as client:
            _as_admin(client)
            response = client.post("/edit/ollama", data={"url": "http://192.168.0.133:11434/v1"})

        assert response.status_code == 303
        assert len(catalogue.puts) == 1
        sent = catalogue.puts[0]
        assert {entry["id"] for entry in sent} == {"ollama", "openai"}
        edited = next(entry for entry in sent if entry["id"] == "ollama")
        assert edited["url"] == "http://192.168.0.133:11434/v1"

    def test_add_appends_to_the_catalogue(self, harness) -> None:
        catalogue, base = harness([_entry()])

        with _client(base) as client:
            _as_admin(client)
            client.post(
                "/add",
                data={"name": "Мой LMS", "url": "http://192.168.0.133:1234/v1", "model": "qwen3"},
            )

        assert len(catalogue.puts) == 1
        ids = [entry["id"] for entry in catalogue.puts[0]]
        assert "ollama" in ids and "moy-lms" in ids

    def test_remove_takes_only_one_entry(self, harness) -> None:
        entries = [
            _entry(),
            _entry(id="openai", name="OpenAI", url="https://api.openai.com/v1", model="gpt-4o-mini"),
        ]
        catalogue, base = harness(entries)

        with _client(base) as client:
            _as_admin(client)
            client.post("/remove/openai")

        assert len(catalogue.puts) == 1
        assert [entry["id"] for entry in catalogue.puts[0]] == ["ollama"]


class TestTokens:
    def test_creating_a_token_shows_it_once_and_lists_its_tail(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            created = client.post("/tokens/create", data={"name": "телефон"})
            token = 'uib-' + created.text.split('readonly value="uib-', 1)[1].split('"', 1)[0]
            listed = client.get("/tokens")
        assert token in created.text
        assert token[-6:] in listed.text
        assert token not in listed.text  # полный токен список не тащит

    def test_a_revoked_token_stops_belonging_to_anyone(self, harness) -> None:
        tokens = UserTokens()
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            created = client.post("/tokens/create", data={"name": "ноутбук"})
            token = 'uib-' + created.text.split('readonly value="uib-', 1)[1].split('"', 1)[0]
            assert tokens.owner(token) == _GUEST
            client.post("/tokens/revoke", data={"tail": token[-6:]})
        assert tokens.owner(token) == ""

    def test_the_endpoint_line_is_built_from_the_host(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            page = client.get("/")
        port = base.rsplit(":", 1)[1]
        assert f">http://127.0.0.1:{port}/v1<" in page.text

    def test_the_api_switch_flips_the_flag(self, harness) -> None:
        users = UserStore()
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            page = client.get("/tokens")
            assert users.api_enabled(_GUEST) is True
            assert "выключить доступ" in page.text

            client.post("/tokens/api")
            assert users.api_enabled(_GUEST) is False

            page = client.get("/tokens")
            assert "ВЫКЛЮЧЕН" in page.text
            client.post("/tokens/api")
            assert users.api_enabled(_GUEST) is True


class TestAdminPeople:
    def test_the_admin_sees_people_and_deletes_one(self, harness) -> None:
        catalogue, base = harness([_entry()])
        UserStore().remember(_GUEST, "guest@mail")

        with _client(base) as client:
            _as_admin(client)
            listed = client.get("/users")
            deleted = client.post("/users/delete", data={"id": _GUEST})

        assert "guest@mail" in listed.text
        assert deleted.status_code == 303
        assert catalogue.deleted == [_GUEST]
        assert UserStore().get(_GUEST) is None

    def test_the_admin_blocks_and_unblocks(self, harness) -> None:
        catalogue, base = harness([_entry()])
        UserStore().remember(_GUEST, "guest@mail")

        with _client(base) as client:
            _as_admin(client)
            client.post("/users/block", data={"id": _GUEST})
            assert catalogue.blocked is True
            marked = client.get("/users")
            assert "заблокирован" in marked.text
            assert "разблокировать" in marked.text

            client.post("/users/block", data={"id": _GUEST})
            assert catalogue.blocked is False

    def test_the_admin_cannot_block_themselves(self, harness) -> None:
        catalogue, base = harness([_entry()])
        UserStore().remember(_ADMIN, "a@b.c", role="admin")

        with _client(base) as client:
            _as_admin(client)
            response = client.post("/users/block", data={"id": _ADMIN})

        assert response.status_code == 303
        assert catalogue.blocked is False


class TestAddFlow:
    """«Показать модели»: список у провайдера, всё введённое не теряется."""

    @staticmethod
    def _good_provider(url, **kwargs):
        class _Provider:
            def fetch_models(self, key=""):
                return [{"name": "qwen3-0.6b"}, {"name": "llama3"}]

        return _Provider()

    @staticmethod
    def _keyless_provider(url, **kwargs):
        from providers.client import NeedsKeyError

        class _Provider:
            def fetch_models(self, key=""):
                raise NeedsKeyError("без ключа провайдер отвечает 401")

        return _Provider()

    def test_fetch_shows_a_select_and_keeps_everything_entered(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._good_provider)

        with _client(base) as client:
            _as_guest(client)
            response = client.post(
                "/add",
                data={
                    "name": "X",
                    "url": "http://new.test/v1",
                    "action": "fetch",
                },
            )

        assert '<select name="model">' in response.text
        assert ">qwen3-0.6b</option>" in response.text
        assert 'value="http://new.test/v1"' in response.text  # адрес не потерян

    def test_a_keyless_list_gets_a_hint_not_a_dead_end(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._keyless_provider)

        with _client(base) as client:
            _as_guest(client)
            response = client.post(
                "/add",
                data={"name": "X", "url": "http://new.test/v1", "action": "fetch"},
            )

        assert "введите API-ключ" in response.text
