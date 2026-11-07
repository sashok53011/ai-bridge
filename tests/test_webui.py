"""Веб-интерфейс: вход людей, личные пространства, роли, защита.

Страница требует входа со всех адресов; cookie HttpOnly + SameSite=Strict,
чужой Origin отклоняется. Админ правит каталог, человек ведёт своё
пространство: ключи, переключатели, токены.
"""

import json
import os
import sqlite3
import threading
import urllib.parse
from pathlib import Path

import httpx
import pytest

from providers.client import PocketBase
from service.gateway import _Handler, _Server
from service.space import Spaces
from service.webui import WebUi
from storage.board import BoardStore
from storage.keys import KeyStore
from storage.mail import MailStore
from storage.secretbox import SecretBox
from storage.sessions import SessionStore
from storage.snapshot import Snapshot
from storage.sqlplayground import SqlPlayground
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


def _key_of(user_id: str, provider_id: str) -> str:
    """Ключ из чужого пространства: шифротекст вскрывает мастер-ключ корня."""
    return KeyStore(user_dir(user_id, _root()), master=SecretBox(_root())).get(provider_id)


class _CatalogueMock:
    """Клиент каталога: GET отдаёт текущий список, PUT и DELETE запоминает."""

    def __init__(self, entries: list[dict], *, can_edit: bool):
        import json as _json

        self.puts: list[list[dict]] = []
        self.deleted: list[str] = []
        self.blocked = False
        self.seen: list[dict] = []  # POST-отчёты в /api/ai-stats и прочие тела
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
                self.seen.append(
                    {"method": "POST", "path": request.url.path, "body": body}
                )
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
        # Доска «Нашёл bug» и почта общая для всех действий одного теста —
        # как в жизни: написал один, увидел другой.
        kwargs.setdefault("board", BoardStore(tmp_path))
        kwargs.setdefault("mail", MailStore(tmp_path))
        from storage.verify import VerifyStore
        kwargs.setdefault("verify_store", VerifyStore(tmp_path))
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

    def test_the_bug_board_is_open_to_a_plain_person(self, harness) -> None:
        """Доска «Нашёл bug» — раздел для всех вошедших, не только админа."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            page = client.get("/bugs")
        assert page.status_code == 200
        assert 'id="bug-chat"' in page.text
        assert 'href="/bugs"' in page.text  # пункт есть в общей навигации
        # Админские разделы при этом не подглядели.
        assert 'href="/users"' not in page.text

    def test_a_person_publishes_a_bug_and_it_is_visible_to_the_next(self, harness) -> None:
        """Общий разговор: написал один — увидел другой (доска на сервере)."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client, "first@mail")
            posted = client.post("/bugs/post", data={"text": "падает при старте"})
            board = client.get("/bugs")

        assert posted.status_code == 303
        assert "падает при старте" in board.text
        assert "first@mail" in board.text  # автор подписан сервером

        # Второй участник заходит отдельно — сообщение на месте.
        with _client(base) as client:
            _as_guest(client, "second@mail")
            page = client.get("/bugs")
        assert "падает при старте" in page.text

    def test_a_status_without_a_reason_does_not_stick(self, harness) -> None:
        """Без пояснения статус не проходит: сервер возвращает причину."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/bugs/post", data={"text": "баг"})
            page = client.get("/bugs")
            # id скрытое поле первого (и единственного) сообщения
            post_id = page.text.split('name="id" value="', 1)[1].split('"', 1)[0]
            refused = client.post(
                "/bugs/status", data={"id": post_id, "key": "work", "why": "  "}
            )
            after = client.get("/bugs")

        assert refused.status_code == 303
        assert refused.headers["location"].startswith("/bugs?notice=")
        assert refused.headers["location"].endswith("&bad=1")  # отказ помечен
        assert 'class="bug-why"' not in after.text  # статус не применился: причины нет

    def test_a_comment_is_visible_to_everyone(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client, "author@mail")
            client.post("/bugs/post", data={"text": "баг"})
            page = client.get("/bugs")
            post_id = page.text.split('name="id" value="', 1)[1].split('"', 1)[0]
            client.post("/bugs/comment", data={"id": post_id, "text": "вижу тоже"})

        with _client(base) as client:
            _as_guest(client, "other@mail")
            page = client.get("/bugs")
        assert "вижу тоже" in page.text
        assert "author@mail" in page.text

    def test_a_plain_person_cannot_drop_messages(self, harness) -> None:
        """Удаление — только админ: общий разговор не выжигают по ошибке."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/bugs/post", data={"text": "баг"})
            page = client.get("/bugs")
            post_id = page.text.split('name="id" value="', 1)[1].split('"', 1)[0]
            dropped = client.post("/bugs/drop", data={"id": post_id})
            still_there = client.get("/bugs")

        assert dropped.status_code == 403
        assert "баг" in still_there.text

    def test_an_anonymous_visitor_is_redirected_to_login_not_to_the_board(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            response = client.get("/bugs")
        assert "bug-chat" not in response.text
        assert 'action="/login"' in response.text

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
        _, base = harness([_entry(capabilities="vision")])
        with _client(base) as client:
            _as_guest(client)
            client.post("/pick/ollama", data={"model": "qwen3:0.6b"})
            page = client.get("/")  # выбор должен сразу жить в каталоге

        guest = Snapshot(user_dir(_GUEST, _root())).load()
        assert guest[0]["model"] == "qwen3:0.6b"
        assert guest[0]["picked"] is True  # отметка осознанного выбора
        assert guest[0]["capabilities"] == "vision"  # чужая справка не тронута
        assert "Ollama" in page.text  # сам провайдер в каталоге
        # Имя модели из каталога убрано (только провайдер + ключ).
        assert "qwen3:0.6b" not in page.text
        assert Snapshot(_root()).load() == []  # каталог не тронут

    def test_get_pick_page_does_not_crash(self, harness) -> None:
        """Регрессия 22.10: GET /pick/ падал с NameError (user_id в GET нет).

        Страница выбора модели роняла весь запрос — Cloudflare отдавал
        502 «Host Error» на все провайдеры. report_models должен получить
        идентификатор из viewer, а не из несуществующей переменной.
        """
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            response = client.get("/pick/ollama")

        assert response.status_code == 200
        assert "ollama" in response.text.lower()

    def test_get_pick_page_works_for_the_admin_too(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            response = client.get("/pick/ollama")

        assert response.status_code == 200


class TestMessages:
    """Личная почта: ящик, написать любому, рассылка, отказ от приёма."""

    @staticmethod
    def _inbox(client) -> str:
        return client.get("/messages").text

    def test_a_letter_reaches_the_recipients_inbox(self, harness) -> None:
        """Написал один — увидел другой: письмо живёт на сервере."""
        _, base = harness([_entry()])
        UserStore().remember(_ADMIN, "a@b.c", role="admin")
        with _client(base) as client:
            _as_admin(client)
            sent = client.post("/messages/send",
                               data={"to": _GUEST, "text": "привет, сосед"})

        with _client(base) as client:
            _as_guest(client)
            page = self._inbox(client)

        assert sent.status_code == 303
        assert "привет, сосед" in page
        assert "a@b.c" in page  # автор подписан сервером

    def test_a_broadcast_reaches_everyone(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            client.post("/messages/send", data={"to": "*", "text": "всем объявление"})

        with _client(base) as client:
            _as_guest(client)
            page = self._inbox(client)
        assert "всем объявление" in page

    def test_a_plain_person_cannot_broadcast(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            refused = client.post("/messages/send", data={"to": "*", "text": "я всем"})
        assert refused.status_code == 403

    def test_refusing_mail_reroutes_letters_to_the_admin(self, harness) -> None:
        """Отказавшийся не пропадает: письмо читает админ, за кого оно."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/messages/mail")  # отказаться от приёма
            page = self._inbox(client)
        assert "не принимаете" in page
        assert "не закрывается" in page  # обещание видно прямо на странице

        with _client(base) as client:
            _as_admin(client)
            client.post("/messages/send", data={"to": _GUEST, "text": "тебе не достанется"})
            admin_box = self._inbox(client)

        assert "тебе не достанется" in admin_box
        assert "за адресата" in admin_box  # и видно, за кого именно

        # У самого отказавшегося письма нет — его забрал админ.
        with _client(base) as client:
            _as_guest(client)
            own = self._inbox(client)
        assert "тебе не достанется" not in own

    def test_refusing_can_be_undone(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/messages/mail")
            assert "не принимаете" in self._inbox(client)
            client.post("/messages/mail")
            page = self._inbox(client)
        assert "Вы принимаете сообщения" in page
        assert "не принимаете" not in page

    def test_the_admin_opens_any_inbox_but_a_person_cannot(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            stranger = client.get("/messages", params={"box": _ADMIN})
        assert stranger.status_code == 403  # чужой ящик закрыт

        with _client(base) as client:
            _as_admin(client)
            opened = client.get("/messages", params={"box": _GUEST})
        assert opened.status_code == 200
        assert "← мой ящик" in opened.text

    def test_deleting_letters_is_the_admins_alone(self, harness) -> None:
        from storage.mail import MailStore

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            client.post("/messages/send", data={"to": _GUEST, "text": "смой мусор"})
        letter_id = MailStore(_root()).inbox(_GUEST)[0]["id"]

        with _client(base) as client:
            _as_guest(client)
            refused = client.post("/messages/drop", data={"id": letter_id})
        assert refused.status_code == 403  # человек не удаляет чужое

        with _client(base) as client:
            _as_admin(client)
            dropped = client.post("/messages/drop", data={"id": letter_id})
        assert dropped.status_code == 303

        with _client(base) as client:
            _as_guest(client)
            after = self._inbox(client)
        assert "смой мусор" not in after

    def test_an_anonymous_visitor_sees_the_login_page(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            response = client.get("/messages")
        assert 'action="/login"' in response.text
        assert 'action="/messages/send"' not in response.text


class TestTelegramNotify:
    """Уведомления: токен — админ, chat_id — каждый, пуш при новом письме."""

    @staticmethod
    def _fake_telegram(monkeypatch, calls: list) -> None:
        """Подменяем канал: пишем в список вместо настоящего Telegram."""
        from providers import telegram

        def send(token, chat_id, text, **kwargs):
            calls.append({"token": token, "chat": chat_id, "text": text})
            return None

        monkeypatch.setattr(telegram, "send", send)

    def test_only_the_admin_can_save_the_token(self, harness) -> None:
        from storage.notify import NotifyStore

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            refused = client.post("/notify/token", data={"token": "123:ABC"})

        assert refused.status_code == 403
        assert NotifyStore(_root()).token() == ""

    def test_the_admin_saves_the_token_and_it_stays_off_the_page(
        self, harness, monkeypatch
    ) -> None:
        from providers import telegram
        from storage.notify import NotifyStore

        monkeypatch.setattr(
            telegram, "hello", lambda token, **kw: ("@my_bridge_bot", None)
        )
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            saved = client.post("/notify/token", data={"token": "123:ABC"})
            page = client.get("/messages")

        assert saved.status_code == 303
        assert "my_bridge_bot" in saved.headers["location"]  # ответ в редиректе
        assert NotifyStore(_root()).token() == "123:ABC"
        assert "123:ABC" not in page.text  # секрет не утекает в HTML

    def test_a_person_saves_their_own_chat_id(self, harness) -> None:
        from storage.users import UserStore

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            saved = client.post("/notify/chat", data={"chat": "424242"})
            page = client.get("/messages")

        assert saved.status_code == 303
        assert UserStore(_root()).tg(_GUEST) == "424242"
        assert "424242" in page.text

    def test_detect_reads_the_chat_id_from_the_bot(self, harness, monkeypatch) -> None:
        from providers import telegram
        from storage.notify import NotifyStore
        from storage.users import UserStore

        NotifyStore(_root()).set_token("123:ABC")
        monkeypatch.setattr(
            telegram, "last_chat_id",
            lambda token, **kw: ("777777", None),
        )
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            detected = client.post("/notify/detect")

        assert detected.status_code == 303
        assert "777777" in detected.headers["location"]
        assert UserStore(_root()).tg(_GUEST) == "777777"

    def test_detect_without_a_token_says_so(self, harness) -> None:
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            refused = client.post("/notify/detect")
        assert refused.status_code == 303
        assert refused.headers["location"].endswith("&bad=1")

    def test_a_new_letter_pushes_to_the_recipient(self, harness, monkeypatch) -> None:
        from storage.notify import NotifyStore
        from storage.users import UserStore

        calls: list = []
        self._fake_telegram(monkeypatch, calls)
        NotifyStore(_root()).set_token("123:ABC")
        users = UserStore(_root())
        users.remember(_ADMIN, "a@b.c", role="admin")
        users.set_tg(_GUEST, "555555")

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            client.post("/messages/send", data={"to": _GUEST, "text": "привет из почты"})

        import time

        for _ in range(50):  # пуш уходит в фоне — ждём до секунды
            if calls:
                break
            time.sleep(0.02)

        assert calls, "уведомление не ушло"
        assert calls[0]["chat"] == "555555"
        assert "привет из почты" in calls[0]["text"]
        assert "a@b.c" in calls[0]["text"]

    def test_a_broadcast_pushes_to_everyone_with_a_chat_id(
        self, harness, monkeypatch
    ) -> None:
        from storage.notify import NotifyStore
        from storage.users import UserStore

        calls: list = []
        self._fake_telegram(monkeypatch, calls)
        NotifyStore(_root()).set_token("123:ABC")
        users = UserStore(_root())
        users.remember(_ADMIN, "a@b.c", role="admin")
        users.remember(_GUEST, "guest@mail")
        users.set_tg(_GUEST, "555555")  # у админа chat_id нет — ему не шлём

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            client.post("/messages/send", data={"to": "*", "text": "объявление"})

        import time

        for _ in range(50):
            if calls:
                break
            time.sleep(0.02)

        assert [c["chat"] for c in calls] == ["555555"]

    def test_no_token_means_no_push_even_with_a_chat_id(self, harness, monkeypatch) -> None:
        from storage.users import UserStore

        calls: list = []
        self._fake_telegram(monkeypatch, calls)
        UserStore(_root()).set_tg(_GUEST, "555555")  # токена нет

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            client.post("/messages/send", data={"to": _GUEST, "text": "тихо"})

        import time

        time.sleep(0.1)
        assert calls == []

    def test_a_test_button_sends_to_oneself(self, harness, monkeypatch) -> None:
        from storage.notify import NotifyStore
        from storage.users import UserStore

        calls: list = []
        self._fake_telegram(monkeypatch, calls)
        NotifyStore(_root()).set_token("123:ABC")
        UserStore(_root()).set_tg(_GUEST, "555555")

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            tested = client.post("/notify/test")

        assert tested.status_code == 303
        assert tested.headers["location"].endswith("&bad=0")
        assert calls and calls[0]["chat"] == "555555"
        assert "Проверка" in calls[0]["text"]

    # ------------------------------------------------- пуш админа о событиях
    # Три события, которые админ не должен пропустить: новый человек,
    # новый баг на доске, новый провайдер в каталоге. Токен + chat_id
    # админа уже сохранены — проверяем, что событие уходит в канал.

    @staticmethod
    def _admin_ready() -> None:
        from storage.notify import NotifyStore
        from storage.users import UserStore

        NotifyStore(_root()).set_token("123:ABC")
        users = UserStore(_root())
        users.remember(_ADMIN, "a@b.c", role="admin")
        users.set_tg(_ADMIN, "999999")

    @staticmethod
    def _wait_calls(calls: list, count: int = 1) -> list:
        import time

        for _ in range(50):
            if len(calls) >= count:
                break
            time.sleep(0.02)
        return calls

    def test_a_new_person_pings_the_admin(self, harness, monkeypatch) -> None:
        calls: list = []
        self._fake_telegram(monkeypatch, calls)
        self._admin_ready()

        _, base = harness([_entry()], signup_hook=lambda e, p: None)
        with _client(base) as client:
            client.post(
                "/signup",
                data={"email": "newcomer@mail", "password": "a", "password2": "a"},
            )

        calls = self._wait_calls(calls)
        assert calls, "регистрация не уведомила админа"
        assert calls[0]["chat"] == "999999"
        assert "Новый человек" in calls[0]["text"]
        assert "newcomer@mail" in calls[0]["text"]
        assert "/users" in calls[0]["text"]  # ссылка ведёт к списку людей

    def test_a_new_bug_pings_the_admin(self, harness, monkeypatch) -> None:
        calls: list = []
        self._fake_telegram(monkeypatch, calls)
        self._admin_ready()

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/bugs/post", data={"text": "кнопка не отвечает при старте"})

        calls = self._wait_calls(calls)
        assert calls, "баг не уведомил админа"
        assert calls[0]["chat"] == "999999"
        assert "Новый баг" in calls[0]["text"]
        assert "кнопка не отвечает" in calls[0]["text"]
        assert "/bugs" in calls[0]["text"]

    def test_a_new_provider_pings_the_admin(self, harness, monkeypatch) -> None:
        calls: list = []
        self._fake_telegram(monkeypatch, calls)
        self._admin_ready()

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            client.post(
                "/add",
                data={"name": "Мой LMS", "url": "http://192.168.0.50:1234/v1",
                      "model": "qwen3", "action": "save"},
            )

        calls = self._wait_calls(calls)
        assert calls, "добавление провайдера не уведомило админа"
        assert calls[0]["chat"] == "999999"
        assert "Новый провайдер" in calls[0]["text"]
        assert "Мой LMS" in calls[0]["text"]

    def test_no_admin_chat_id_means_silent(self, harness, monkeypatch) -> None:
        """Нет chat_id — тихо: событие записано, Telegram не трогается."""
        from storage.notify import NotifyStore

        calls: list = []
        self._fake_telegram(monkeypatch, calls)
        NotifyStore(_root()).set_token("123:ABC")  # токен есть, chat_id нет

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            client.post("/bugs/post", data={"text": "тихий баг"})

        import time

        time.sleep(0.1)
        assert calls == []

    def test_a_failed_push_does_not_break_the_action(self, harness, monkeypatch) -> None:
        """Telegram упал — письмо/баг всё равно приняты, действие прошло."""
        from providers import telegram
        from storage.notify import NotifyStore
        from storage.users import UserStore

        def boom(token, chat_id, text, **kwargs):
            raise RuntimeError("telegram лёг")

        monkeypatch.setattr(telegram, "send", boom)
        NotifyStore(_root()).set_token("123:ABC")
        users = UserStore(_root())
        users.remember(_ADMIN, "a@b.c", role="admin")
        users.set_tg(_ADMIN, "999999")

        _, base = harness([_entry()])
        with _client(base) as client:
            _as_guest(client)
            posted = client.post("/bugs/post", data={"text": "баг при сетевом сбое"})
            page = client.get("/bugs")

        assert posted.status_code == 303
        assert "баг при сетевом сбое" in page.text  # запись принята несмотря на пуш


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
        assert "location.reload" in page.text  # автообновление скриптом

    def test_a_login_lands_in_the_journal(self, harness) -> None:
        from service.eventlog import EventLog

        log = EventLog()
        _, base = harness([_entry()], event_log=log)
        with _client(base) as client:
            _as_guest(client, "anna@mail")

        kinds = [event["kind"] for event in log.recent()]
        assert "вход" in kinds

    def test_the_logs_route_has_no_autoscroll_any_more(self, harness) -> None:
        """Кнопка автоскролла убрана: при «новые сверху» она бесполезна."""
        _, base = harness([_entry()])
        with _client(base) as client:
            _as_admin(client)
            response = client.post("/logs/autoscroll")

        assert response.status_code == 404

    def test_the_log_page_does_not_record_itself(self, harness) -> None:
        from service.eventlog import EventLog

        log = EventLog()
        _, base = harness([_entry()], event_log=log)
        with _client(base) as client:
            _as_admin(client)
            for _ in range(3):
                client.get("/logs")  # meta-refresh порождал мусор каждые 5 секунд

        texts = [event["text"] for event in log.recent()]
        assert not any(text == "/logs" for text in texts)

    def test_page_visits_are_human_readable_with_the_path_kept(self, harness) -> None:
        """В журнале «Сообщения», а не /messages; путь — полем события."""
        from service.eventlog import EventLog

        log = EventLog()
        _, base = harness([_entry()], event_log=log)
        with _client(base) as client:
            _as_guest(client)
            client.get("/messages")

        page_events = [e for e in log.recent() if e["kind"] == "страница"]
        assert page_events, "посещение страницы не записано"
        assert page_events[0]["text"] == "Сообщения"
        assert page_events[0]["path"] == "/messages"

    def test_a_sent_letter_says_who_and_what(self, harness) -> None:
        """«Письмо для адресата: …» — кому и о чём, а не «отправлено»."""
        from service.eventlog import EventLog
        from storage.users import UserStore

        log = EventLog()
        UserStore(_root()).remember(_GUEST, "guest@mail")
        _, base = harness([_entry()], event_log=log)
        with _client(base) as client:
            _as_admin(client)
            client.post("/messages/send", data={"to": _GUEST, "text": "привет, сосед"})

        letters = [e for e in log.recent() if e["kind"] == "письмо"]
        assert letters, "письмо не записано в журнал"
        assert "письмо для guest@mail" in letters[0]["text"]
        assert "привет, сосед" in letters[0]["text"]  # содержимое видно

    def test_a_published_bug_carries_its_text(self, harness) -> None:
        from service.eventlog import EventLog

        log = EventLog()
        _, base = harness([_entry()], event_log=log)
        with _client(base) as client:
            _as_guest(client)
            client.post("/bugs/post", data={"text": "кнопка не отвечает при старте"})

        bugs = [e for e in log.recent() if e["kind"] == "баг"]
        assert bugs, "баг не записан"
        assert "новый баг" in bugs[0]["text"]
        assert "кнопка не отвечает" in bugs[0]["text"]

    def test_a_rule_change_describes_the_rule_itself(self, harness) -> None:
        """Правило — с условием и действием, а не «№0 в response»."""
        from service.eventlog import EventLog
        from storage.rules import RuleStore

        log = EventLog()
        rules = RuleStore(_root())
        rules.add("response", text="quota exceeded", action="black", provider="Groq")
        _, base = harness([_entry()], event_log=log, rules=rules)
        with _client(base) as client:
            _as_admin(client)
            client.post("/rules/drop", data={"where": "response", "index": "0"})

        drops = [e for e in log.recent() if e["kind"] == "правило"]
        assert drops, "удаление правила не записано"
        assert "убрано:" in drops[0]["text"]
        assert "quota exceeded" in drops[0]["text"]  # именно это правило
        assert "groq" in drops[0]["text"]  # провайдер (нормализован сервером)

    def test_the_bodies_route_is_gone_and_bodies_are_always_on(self, harness) -> None:
        from service.eventlog import EventLog

        log = EventLog()
        _, base = harness([_entry()], event_log=log)
        with _client(base) as client:
            _as_admin(client)
            page = client.get("/logs").text
            assert "bodies-btn" not in page  # кнопки больше нет
            assert "тела запросов: вкл" not in page
            assert "тела запросов: выкл" not in page

            gone = client.post("/logs/bodies")
            assert gone.status_code == 404  # маршрут удалён


class TestRulesPage:
    """Раздел «Правила»: строка → действие, только для админа."""

    @staticmethod
    def _rules():
        from storage.rules import RuleStore

        return RuleStore(_root())

    def test_a_person_cannot_see_or_touch_rules(self, harness) -> None:
        from storage.rules import RuleStore

        _, base = harness([_entry()], rules=RuleStore(_root()))
        with _client(base) as client:
            _as_guest(client)

            assert client.get("/rules").status_code == 403
            assert client.post("/rules/add", data={"where": "response"}).status_code == 403
            assert client.post("/rules/drop", data={"where": "response"}).status_code == 403

    def test_the_admin_sees_both_blocks_with_typical_strings(self, harness) -> None:
        from storage.rules import RuleStore

        _, base = harness([_entry()], rules=RuleStore(_root()))
        with _client(base) as client:
            _as_admin(client)
            page = client.get("/rules").text

            assert "Условия в ответах провайдеров" in page
            assert "Строки в запросах клиентов" in page
            assert "image_url" in page  # подсказка, как выглядит строка
            assert "tool_choice" in page
            assert "Что делают действия" in page  # пояснение простыми словами

    def test_adding_a_response_rule_makes_a_decision_for_the_provider(self, harness) -> None:
        from storage.rules import RuleStore

        rules = RuleStore(_root())
        _, base = harness([_entry()], rules=rules)
        with _client(base) as client:
            _as_admin(client)
            response = client.post("/rules/add", data={
                "where": "response", "provider": "ollama",
                "text": "insufficient credits", "action": "grey",
            })

            assert response.status_code == 303
            stored = rules.response_rules("ollama")
            assert stored == [{"text": "insufficient credits", "action": "grey",
                               "provider": "ollama"}]

    def test_the_rule_shows_up_on_the_page_and_can_be_dropped(self, harness) -> None:
        from storage.rules import RuleStore

        rules = RuleStore(_root())
        rules.add("response", text="закончились кредиты", action="black", provider="ollama")
        _, base = harness([_entry()], rules=rules)
        with _client(base) as client:
            _as_admin(client)
            page = client.get("/rules").text
            assert "закончились кредиты" in page
            assert "ollama" in page

            client.post("/rules/drop", data={"where": "response", "index": "0"})

            assert rules.all()["response"] == []
            assert "закончились кредиты" not in client.get("/rules").text

    def test_a_request_rule_is_global_and_stored(self, harness) -> None:
        from storage.rules import RuleStore

        rules = RuleStore(_root())
        _, base = harness([_entry()], rules=rules)
        with _client(base) as client:
            _as_admin(client)
            client.post("/rules/add", data={
                "where": "request", "text": "base64", "action": "vision",
            })

            assert rules.request_rules() == [{"text": "base64", "action": "vision"}]

    def test_an_empty_string_or_unknown_action_is_refused(self, harness) -> None:
        from storage.rules import RuleStore

        rules = RuleStore(_root())
        _, base = harness([_entry()], rules=rules)
        with _client(base) as client:
            _as_admin(client)
            bad = client.post("/rules/add", data={
                "where": "response", "provider": "ollama",
                "text": "   ", "action": "grey",
            }, follow_redirects=False)

            assert bad.status_code == 303
            assert "bad=True" not in bad.headers.get("location", "")  # редирект
            assert rules.all()["response"] == []  # ничего не записалось


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


class TestEditKeyAndModels:
    """Правка: ключ и «показать модели» живут на одной странице с формой."""

    @staticmethod
    def _provider_recording(seen: list):
        def factory(url, **kwargs):
            class _Provider:
                def fetch_models(self, key=""):
                    seen.append(key)
                    return [{"name": "gpt-5"}]

            return _Provider()

        return factory

    def test_fetch_in_edit_shows_the_select_and_sends_the_entered_key(self, harness) -> None:
        seen: list[str] = []
        _, base = harness([_entry()], make_provider=self._provider_recording(seen))

        with _client(base) as client:
            _as_admin(client)
            response = client.post(
                "/edit/ollama",
                data={"url": "https://api.githubcopilot.com", "api_key": "gho_x",
                      "action": "fetch"},
            )

        assert response.status_code == 200
        assert '<select name="model">' in response.text
        assert seen == ["gho_x"]  # ключ дошёл до провайдера
        # Ничего не сохранено: ни адрес, ни ключ — только перерисовка.
        assert KeyStore(_root()).get("ollama") == ""

    def test_fetch_falls_back_to_the_stored_key(self, harness) -> None:
        seen: list[str] = []
        _, base = harness([_entry()], make_provider=self._provider_recording(seen))

        with _client(base) as client:
            _as_admin(client)
            client.post("/key", data={"id": "ollama", "api_key": "stored-key"})
            client.post("/edit/ollama", data={"action": "fetch",
                                              "url": "http://localhost:11434/v1"})

        assert seen == ["stored-key"]  # вводить ключ заново не нужно

    def test_saving_edit_persists_the_key_alongside_the_fields(self, harness) -> None:
        catalogue, base = harness([_entry()])

        with _client(base) as client:
            _as_admin(client)
            response = client.post(
                "/edit/ollama",
                data={"name": "Мой Ollama", "url": "http://localhost:11434/v1",
                      "model": "llama3.2", "api_key": "sk-new"},
            )

        assert response.status_code == 303
        assert KeyStore(_root()).get("ollama") == "sk-new"
        sent = catalogue.puts[-1]
        edited = next(entry for entry in sent if entry["id"] == "ollama")
        assert edited["name"] == "Мой Ollama"
        # Ключ не уезжает на сервер каталога — он остаётся на устройстве.
        assert "sk-new" not in json.dumps(sent, ensure_ascii=False)

    def test_a_fetch_redirects_back_to_the_same_edit_page(self, harness) -> None:
        _, base = harness([_entry()])

        with _client(base) as client:
            _as_admin(client)
            response = client.post("/edit/ollama", data={"name": "X"})

        assert response.headers["location"].startswith("/edit/ollama")


class TestGithubOauth:
    """Вход через GitHub: код → подтверждение → ключ в хранилище."""

    @staticmethod
    def _copilot_entry(**kwargs) -> dict:
        entry = _entry(id="github-copilot-acp", name="GitHub Copilot",
                       url="https://api.githubcopilot.com", model="")
        entry.update(kwargs)
        return entry

    @pytest.fixture
    def github(self, monkeypatch):
        """Подмена вызовов GitHub: код входа и обмен токеном."""
        state = {"flow": None, "polls": []}

        def start_device_flow(**kwargs):
            return {"device_code": "dc-1", "user_code": "WXYZ-9876",
                    "url": "https://github.com/login/device",
                    "interval": 5, "expires_in": 900}

        def poll_device_flow(device_code, **kwargs):
            state["polls"].append(device_code)
            return state.get("answer", ("pending", ""))

        monkeypatch.setattr("service.webui.copilot.start_device_flow", start_device_flow)
        monkeypatch.setattr("service.webui.copilot.poll_device_flow", poll_device_flow)
        return state

    def test_the_key_page_of_copilot_offers_github_login(self, harness, github) -> None:
        _, base = harness([self._copilot_entry()])

        with _client(base) as client:
            _as_guest(client)
            page = client.get("/key/github-copilot-acp")

        assert 'action="/oauth/github-copilot-acp"' in page.text
        assert "Войти через GitHub" in page.text

    def test_an_ordinary_provider_has_no_github_button(self, harness, github) -> None:
        _, base = harness([_entry()])

        with _client(base) as client:
            _as_guest(client)
            page = client.get("/key/ollama")

        assert "/oauth/" not in page.text

    def test_start_shows_the_code_and_check_saves_the_token(self, harness, github) -> None:
        github["answer"] = ("done", "gho_fromgithub")
        _, base = harness([self._copilot_entry()])

        with _client(base) as client:
            _as_guest(client)
            started = client.post("/oauth/github-copilot-acp", data={"action": "start"})
            page = client.get("/oauth/github-copilot-acp")
            checked = client.post("/oauth/github-copilot-acp", data={"action": "check"})

        assert started.status_code == 303
        assert "WXYZ-9876" in page.text  # код показан человеку
        assert checked.status_code == 303
        assert "вход через GitHub выполнен" in urllib.parse.unquote(checked.headers["location"])
        # Ключ осел в личном пространстве вошедшего, не в каталоге.
        assert _key_of(_GUEST, "github-copilot-acp") == "gho_fromgithub"

    def test_pending_check_returns_to_the_waiting_page(self, harness, github) -> None:
        _, base = harness([self._copilot_entry()])

        with _client(base) as client:
            _as_guest(client)
            client.post("/oauth/github-copilot-acp", data={"action": "start"})
            checked = client.post("/oauth/github-copilot-acp", data={"action": "check"})

        assert checked.headers["location"].startswith("/oauth/github-copilot-acp")
        assert github["polls"] == ["dc-1"]
        assert _key_of(_GUEST, "github-copilot-acp") == ""

    def test_a_refusal_is_explained_and_the_flow_stops(self, harness, github) -> None:
        github["answer"] = ("error", "вы отклонили вход")
        _, base = harness([self._copilot_entry()])

        with _client(base) as client:
            _as_guest(client)
            client.post("/oauth/github-copilot-acp", data={"action": "start"})
            checked = client.post("/oauth/github-copilot-acp", data={"action": "check"})
            again = client.post("/oauth/github-copilot-acp", data={"action": "check"})

        assert checked.status_code == 303
        assert "отклонили" in urllib.parse.unquote(checked.headers["location"])
        # Состояние очищено: вторая проверка — «вход не начат».
        assert "не начат" in urllib.parse.unquote(again.headers["location"])
        assert _key_of(_GUEST, "github-copilot-acp") == ""

    def test_check_without_start_says_the_flow_never_began(self, harness, github) -> None:
        _, base = harness([self._copilot_entry()])

        with _client(base) as client:
            _as_guest(client)
            checked = client.post("/oauth/github-copilot-acp", data={"action": "check"})

        assert "не начат" in urllib.parse.unquote(checked.headers["location"])

    def test_slow_down_makes_the_page_wait_longer(self, harness, github) -> None:
        """«Медленнее» от GitHub удлиняет паузу, а не роняет вход."""
        github["answer"] = ("pending", "slow_down")
        _, base = harness([self._copilot_entry()])

        with _client(base) as client:
            _as_guest(client)
            client.post("/oauth/github-copilot-acp", data={"action": "start"})
            client.post("/oauth/github-copilot-acp", data={"action": "check"})
            page = client.get("/oauth/github-copilot-acp")

        assert "10000" in page.text  # 5 с по умолчанию + 5 за slow_down
        assert page.status_code == 200

    def test_a_foreign_provider_cannot_start_the_flow(self, harness, github) -> None:
        """Не-Copilot адрес не пускает к device-flow GitHub."""
        _, base = harness([_entry()])

        with _client(base) as client:
            _as_guest(client)
            started = client.post("/oauth/ollama", data={"action": "start"})

        assert started.status_code == 303
        assert "только" in urllib.parse.unquote(started.headers["location"])

    def test_the_waiting_page_does_not_flood_the_journal(self, harness, github) -> None:
        """Опрос каждые 5 секунд не пишет событие «страница» на каждую проверку."""
        from service.eventlog import EventLog

        log = EventLog()
        _, base = harness([self._copilot_entry()], event_log=log)
        with _client(base) as client:
            _as_guest(client)
            client.post("/oauth/github-copilot-acp", data={"action": "start"})
            client.get("/oauth/github-copilot-acp")
            client.get("/oauth/github-copilot-acp")

        page_events = [e for e in log.recent() if e["kind"] == "страница"]
        assert not any("oauth" in (e.get("path") or "") for e in page_events)


class TestTestReports:
    """Кнопка «тест» — главная проверка провайдера: ответ модели засчитывается.

    Статистика сообщества пополняется тем же POST /api/ai-stats, что и
    старая проверка; «нужен ключ» в статистику не попадает.
    """

    @staticmethod
    def _answering_provider(url, **kwargs):
        class _Response:
            status_code = 200

            @staticmethod
            def read() -> bytes:
                return json.dumps(
                    {"choices": [{"message": {"content": "привет!"}}]}
                ).encode("utf-8")

        class _Provider:
            @staticmethod
            def forward_chat(body, *, api_key, timeout, stream):
                return _Response()

        return _Provider()

    def test_a_successful_chat_posts_a_report(self, harness) -> None:
        catalogue, base = harness([_entry()], make_provider=self._answering_provider)

        with _client(base) as client:
            _as_guest(client, "anna@mail")
            client.post("/key", data={"id": "ollama", "api_key": "sk-test"})
            response = client.post("/test/ollama")

        assert "привет!" in response.text
        reports = [
            item for item in catalogue.seen
            if item["method"] == "POST" and item["path"] == "/api/ai-stats"
        ]
        assert len(reports) == 1
        assert reports[0]["body"]["ok"] is True
        assert reports[0]["body"]["userId"]  # чьим именем записан успех

    def test_a_chat_without_a_key_sends_no_report(self, harness) -> None:
        """Нет ключа — это состояние приставки, а не поломка провайдера."""
        entry = _entry()
        entry["model"] = ""  # до ключа обычно не доходит: сначала нет модели
        catalogue, base = harness([entry], make_provider=self._answering_provider)

        with _client(base) as client:
            _as_guest(client)
            response = client.post("/test/ollama")

        assert "не выбрана модель" in response.text
        assert not any(
            item["method"] == "POST" and item["path"] == "/api/ai-stats"
            for item in catalogue.seen
        )


class TestImageTestPage:
    """Дополнительный тест на странице «Тест»: секция генерации и её результат."""

    @staticmethod
    def _image_provider(url, **kwargs):
        class _Chat:
            status_code = 200
            headers: dict = {}

            @staticmethod
            def read() -> bytes:
                return json.dumps(
                    {"choices": [{"message": {"content": "Да"}}]}
                ).encode("utf-8")

        class _Image:
            status_code = 200
            headers: dict = {}

            @staticmethod
            def read() -> bytes:
                return json.dumps(
                    {"data": [{"url": "https://media.test/pic.png"}]}
                ).encode("utf-8")

        class _Provider:
            @staticmethod
            def forward_chat(body, *, api_key, timeout, stream):
                return _Chat()

            @staticmethod
            def forward_path(path, body, *, api_key, timeout, stream):
                assert path == "/images/generations"
                return _Image()

            @staticmethod
            def fetch_model_meta(key: str = "") -> dict:
                return {"flux.1-schnell": {
                    "category": "image",
                    "endpoints": ["/v1/images/generations"],
                    "paid": False,
                }}

        return _Provider()

    def test_the_test_page_offers_the_image_form(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._image_provider)

        with _client(base) as client:
            _as_guest(client, "anna@mail")
            response = client.post("/test/ollama")

        assert response.status_code == 200
        assert 'action="/imgtest/ollama"' in response.text
        assert 'value="flux.1-schnell"' in response.text
        assert 'name="question"' in response.text  # и свой промпт к чату

    def test_a_generated_picture_is_shown(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._image_provider)

        with _client(base) as client:
            _as_guest(client, "anna@mail")
            client.post("/key", data={"id": "ollama", "api_key": "sk-test"})
            response = client.post(
                "/imgtest/ollama", data={"prompt": "кот", "model": "flux.1-schnell"},
            )

        assert response.status_code == 200
        assert "<img" in response.text
        assert "https://media.test/pic.png" in response.text

    def test_a_custom_question_keeps_the_image_section(self, harness) -> None:
        """Регрессия: свой промпт не должен ронять секцию генерации (502).

        `question` — для чат-теста; в `image_models` он уходить не должен.
        """
        _, base = harness([_entry()], make_provider=self._image_provider)

        with _client(base) as client:
            _as_guest(client, "anna@mail")
            response = client.post("/test/ollama", data={"question": "привет"})

        assert response.status_code == 200
        assert 'action="/imgtest/ollama"' in response.text
        assert "привет" in response.text

    def test_a_provider_without_meta_has_no_section(self, harness) -> None:
        """Без метаданных каталога секции генерации нет — не у всех подряд."""
        class _Bare:
            @staticmethod
            def forward_chat(body, *, api_key, timeout, stream):
                class _Chat:
                    status_code = 200
                    headers: dict = {}

                    @staticmethod
                    def read() -> bytes:
                        return json.dumps(
                            {"choices": [{"message": {"content": "Да"}}]}
                        ).encode("utf-8")

                return _Chat()

        _, base = harness([_entry()], make_provider=lambda url, **kw: _Bare())

        with _client(base) as client:
            _as_guest(client, "anna@mail")
            response = client.post("/test/ollama")

        assert "/imgtest/" not in response.text

    def test_an_image_url_reaches_the_chat_body(self, harness) -> None:
        """Поле «картинка» формы: image_url долетает до запроса чата."""
        seen: dict = {}

        def provider(url, **kwargs):
            class _Chat:
                status_code = 200
                headers: dict = {}

                @staticmethod
                def read() -> bytes:
                    return json.dumps(
                        {"choices": [{"message": {"content": "вижу"}}]}
                    ).encode("utf-8")

            class _P:
                @staticmethod
                def forward_chat(body, *, api_key, timeout, stream):
                    seen.update(json.loads(body))
                    return _Chat()

            return _P()

        _, base = harness([_entry()], make_provider=provider)

        with _client(base) as client:
            _as_guest(client, "anna@mail")
            client.post("/key", data={"id": "ollama", "api_key": "sk-test"})
            response = client.post("/test/ollama", data={
                "question": "что на картинке?",
                "image_url": "https://x/pic.png",
            })

        assert response.status_code == 200
        assert "вижу" in response.text
        content = seen["messages"][0]["content"]
        assert isinstance(content, list)
        assert content[1]["image_url"]["url"] == "https://x/pic.png"

    def test_a_picked_model_reaches_the_chat_request(self, harness) -> None:
        """Select «модель»: тест можно пустить на любой модели каталога."""
        seen: dict = {}

        def provider(url, **kwargs):
            class _Chat:
                status_code = 200
                headers: dict = {}

                @staticmethod
                def read() -> bytes:
                    return json.dumps(
                        {"choices": [{"message": {"content": "с другой модели"}}]}
                    ).encode("utf-8")

            class _P:
                @staticmethod
                def forward_chat(body, *, api_key, timeout, stream):
                    seen.update(json.loads(body))
                    return _Chat()

                @staticmethod
                def fetch_models(key: str = ""):
                    return [{"name": "llama3.2"}, {"name": "moonshotai/kimi-k3"}]

            return _P()

        _, base = harness([_entry()], make_provider=provider)

        with _client(base) as client:
            _as_guest(client, "anna@mail")
            client.post("/key", data={"id": "ollama", "api_key": "sk-test"})
            response = client.post("/test/ollama", data={
                "question": "привет",
                "test_model": "moonshotai/kimi-k3",
            })

        assert response.status_code == 200
        assert "с другой модели" in response.text
        assert seen["model"] == "moonshotai/kimi-k3"


class TestMediaTestRoutes:
    """Страница теста: озвучка, 3D и вектор — свои маршруты и результаты."""

    @staticmethod
    def _provider(url, **kwargs):
        class _Link:
            def __init__(self, link: str, content: bytes = b"data"):
                self.status_code = 200
                self.headers = {"Link": f"<{link}>; rel=\"enclosure\""}
                self._content = content

            def read(self) -> bytes:
                return self._content

            def close(self) -> None:
                pass

        class _Json:
            def __init__(self, payload: dict):
                self.status_code = 200
                self.headers: dict = {}
                self._payload = payload

            def read(self) -> bytes:
                return json.dumps(self._payload).encode("utf-8")

            def close(self) -> None:
                pass

        class _Provider:
            @staticmethod
            def fetch_model_meta(key: str = "") -> dict:
                return {
                    "tts-1": {"category": "audio",
                              "endpoints": ["/v1/audio/speech"], "paid": False},
                    "google/lyria-3.5": {"category": "audio",
                                         "endpoints": ["/v1/audio/speech"],
                                         "paid": True},
                    "elevenlabs/voice-isolator": {
                        "category": "audio",
                        "endpoints": ["/v1/audio/voice-isolator"], "paid": True},
                    "trellis-2": {"category": "3d", "endpoints": [], "paid": False},
                    "emb-1": {"category": "embedding",
                              "endpoints": ["/v1/embeddings"], "paid": False},
                    "whisper-1": {"category": "audio",
                                  "endpoints": ["/v1/audio/transcriptions"],
                                  "paid": False},
                    "flux": {"category": "image",
                             "endpoints": ["/v1/images/generations"], "paid": False},
                }

            @staticmethod
            def fetch_audio_catalogue(key: str = "") -> dict:
                return {"openai/tts-1": ["alloy", "nova"]}

            @staticmethod
            def media_get(kind, prompt, model, *, api_key, params=None,
                          prefer_async=False, stream=True):
                if kind == "audio":
                    assert (params or {}).get("voice") == "alloy" or not params
                    return _Link("https://media.test/voice.mp3")
                assert kind == "3d"
                assert (params or {}).get("image") == "https://x/p.png"
                return _Link("https://media.test/model.glb")

            @staticmethod
            def forward_path(path, body, *, api_key, timeout, stream):
                if path == "/embeddings":
                    return _Json({"data": [{"embedding": [0.5, -0.25]}]})
                assert path == "/images/generations"
                return _Json({"data": [{"url": "https://media.test/pic.png"}]})

            @staticmethod
            def forward_form(path, data, files, *, api_key, from_root=False,
                             stream=False):
                if path == "/audio/voice-isolator":
                    assert data["model"] == "elevenlabs/voice-isolator"
                    name, content, ctype = files["file"]
                    assert content  # файл (свой или подставленный тон)

                    class _L:
                        status_code = 200
                        headers = {"Link": '<https://media.test/clean.mp3>; '
                                           'rel="enclosure"'}

                        @staticmethod
                        def read() -> bytes:
                            return b"ID3mp3"

                        @staticmethod
                        def close() -> None:
                            pass

                    return _L()

                assert path == "/audio/transcriptions"
                assert data["model"] == "whisper-1"
                name, content, ctype = files["file"]
                assert content  # файл не пустой: свой или подставленный тон

                class _T:
                    status_code = 200
                    headers: dict = {}

                    @staticmethod
                    def read() -> bytes:
                        return json.dumps({"text": "тихий голос"}).encode("utf-8")

                    @staticmethod
                    def close() -> None:
                        pass

                return _T()

        return _Provider()

    @staticmethod
    def _ready(client) -> None:
        _as_guest(client, "anna@mail")
        client.post("/key", data={"id": "ollama", "api_key": "sk-test"})

    def test_audio_route_renders_a_player(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._provider)

        with _client(base) as client:
            self._ready(client)
            response = client.post("/audiotest/ollama", data={
                "text": "привет мир", "voice": "alloy", "model": "tts-1",
            })

        assert response.status_code == 200
        assert "<audio" in response.text
        assert "https://media.test/voice.mp3" in response.text
        assert 'list="voices"' in response.text  # голоса каталога — подсказки

    def test_music_runs_through_the_audio_route(self, harness) -> None:
        """Музыка — тот же маршрут озвучки, но результат в своей секции."""
        _, base = harness([_entry()], make_provider=self._provider)

        with _client(base) as client:
            self._ready(client)
            response = client.post("/audiotest/ollama", data={
                "text": "спокойная фортепианная мелодия",
                "model": "google/lyria-3.5", "kind": "music",
            })

        assert response.status_code == 200
        assert "<h2>Музыка</h2>" in response.text
        assert "<audio" in response.text  # плеер в секции музыки

    def test_voice_route_returns_the_link(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._provider)

        with _client(base) as client:
            self._ready(client)
            response = client.post("/voicetest/ollama", data={
                "model": "isolator|elevenlabs/voice-isolator",
            })

        assert response.status_code == 200
        assert "https://media.test/clean.mp3" in response.text
        assert "<h2>Обработка звука</h2>" in response.text

    def test_three_d_route_offers_the_download(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._provider)

        with _client(base) as client:
            self._ready(client)
            response = client.post("/3dtest/ollama", data={
                "image": "https://x/p.png", "resolution": "low",
                "model": "trellis-2", "prompt": "",
            })

        assert response.status_code == 200
        assert "https://media.test/model.glb" in response.text
        assert "скачать" in response.text

    def test_embedding_route_shows_the_vector(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._provider)

        with _client(base) as client:
            self._ready(client)
            response = client.post("/embedtest/ollama", data={
                "text": "привет", "model": "emb-1",
            })

        assert response.status_code == 200
        assert "вектор из 2 чисел" in response.text
        assert "0.5" in response.text

    def test_transcriber_without_a_file_gets_the_test_tone(self, harness) -> None:
        """Пустое поле аудио — веб сам подставляет тестовый тон, текст приходит."""
        _, base = harness([_entry()], make_provider=self._provider)

        with _client(base) as client:
            self._ready(client)
            response = client.post("/stttest/ollama", data={"model": "whisper-1"})

        assert response.status_code == 200
        assert "тихий голос" in response.text

    def test_transcriber_accepts_an_uploaded_file(self, harness) -> None:
        _, base = harness([_entry()], make_provider=self._provider)

        with _client(base) as client:
            self._ready(client)
            response = client.post(
                "/stttest/ollama", data={"model": "whisper-1"},
                files={"audio": ("note.wav", b"RIFFDATA", "audio/wav")},
            )

        assert response.status_code == 200
        assert "тихий голос" in response.text


class TestStats:
    """Страница «Статистика»: расходы по моделям, доступна всем вошедшим."""

    @staticmethod
    def _usage() -> "UsageStore":
        from storage.usage import UsageStore

        store = UsageStore()
        store.add(_GUEST, "ollama", "llama3.2", prompt=300, completion=70)
        store.add(_GUEST, "ollama", "llama3.2", switched=True)
        return store

    def test_an_anonymous_visitor_is_sent_to_login(self, harness) -> None:
        _, base = harness([_entry()], usage_store=self._usage())

        with _client(base) as client:
            response = client.get("/stats")

        assert "Вход" in response.text  # статистика — не для улицы

    def test_a_person_is_locked_out_of_stats(self, harness) -> None:
        """Статистика — только админу: человек получает 403."""
        _, base = harness([_entry(capabilities="vision")], usage_store=self._usage())

        with _client(base) as client:
            _as_guest(client)
            response = client.get("/stats")

        assert response.status_code == 403

    def test_the_admin_sees_the_numbers(self, harness) -> None:
        _, base = harness([_entry(capabilities="vision")], usage_store=self._usage())

        with _client(base) as client:
            _as_admin(client)
            page = client.get("/stats")

        assert "llama3.2" in page.text
        assert "<td class=\"num\">2</td>" in page.text  # запросов: два add()
        assert "vision" in page.text  # способности из каталога

    def test_a_model_with_context_shows_it_under_the_name(self, harness) -> None:
        _, base = harness([_entry(context="128K")], usage_store=self._usage())

        with _client(base) as client:
            _as_admin(client)
            page = client.get("/stats")

        assert "128K" in page.text

    def test_the_ok_percentage_is_shown(self, harness) -> None:
        from storage.usage import UsageStore

        store = UsageStore()
        store.add(_GUEST, "ollama", "llama3.2")
        store.add(_GUEST, "ollama", "llama3.2", ok=False)
        _, base = harness([_entry()], usage_store=store)

        with _client(base) as client:
            _as_admin(client)
            page = client.get("/stats")

        assert "50%" in page.text  # одна удачная из двух
        assert "(1 ✗)" in page.text

    def test_other_peoples_spending_is_not_shown(self, harness) -> None:
        from storage.usage import UsageStore

        store = self._usage()
        store.add("кто-то", "ollama", "llama3.2", prompt=99999)
        _, base = harness([_entry()], usage_store=store)

        with _client(base) as client:
            _as_guest(client)
            page = client.get("/stats")

        assert "99999" not in page.text  # чужие расходы — не моё дело

    def test_the_admin_sees_the_lists_and_a_person_does_not(self, harness) -> None:
        """Админ видит списки (белый — четырьмя секциями); человеку — нет."""
        from storage.lists import ModelLists

        _, base = harness(
            [_entry()], usage_store=self._usage(), lists=ModelLists(_root())
        )

        with _client(base) as client:
            _as_admin(client)
            admin_page = client.get("/stats")

        assert "Списки приставки" in admin_page.text
        assert "Белый список" in admin_page.text
        assert "Только текст" in admin_page.text
        assert "Vision (с картинками)" in admin_page.text
        assert "Tools (с инструментами)" in admin_page.text
        assert "Vision + Tools" in admin_page.text
        assert "Чёрный список" in admin_page.text

    def test_a_person_does_not_see_the_groups_section(self, harness) -> None:
        """Четыре секции групп — только админу (человек получает 403)."""
        _, base = harness([_entry()], usage_store=self._usage())

        with _client(base) as client:
            _as_guest(client)
            page = client.get("/stats")

        assert page.status_code == 403
        assert "Только текст" not in page.text
        assert "Vision + Tools" not in page.text

    def test_a_guest_has_no_lists_section(self, harness) -> None:
        _, base = harness([_entry()], usage_store=self._usage())

        with _client(base) as client:
            _as_guest(client)
            page = client.get("/stats")

        assert page.status_code == 403
        assert "Списки приставки" not in page.text

    def test_the_lists_are_editable_textareas(self, harness) -> None:
        """Каждый список — форма с textarea и кнопкой сохранить."""
        from storage.lists import ModelLists

        _, base = harness(
            [_entry()], usage_store=self._usage(), lists=ModelLists(_root())
        )

        with _client(base) as client:
            _as_admin(client)
            page = client.get("/stats")

        # Белый (4 группы) + серый + чёрный + доказано без vision +
        # доказано без tools + доказано платные = девять форм.
        for filename in (
            "whitelist-text.txt", "whitelist-vision.txt",
            "whitelist-tools.txt", "whitelist-vision-tools.txt",
            "greylist.txt", "blacklist.txt",
            "novision.txt", "notools.txt", "paid.txt",
        ):
            assert f'action="/stats/save/{filename}"' in page.text, filename
        # Плюс форма создания нового кодового слова.
        assert page.text.count("<textarea") == 10
        assert 'action="/stats/codes/create"' in page.text

    def test_the_admin_can_save_a_list(self, harness) -> None:
        from storage.lists import ModelLists

        lists = ModelLists(_root())
        _, base = harness(
            [_entry()], usage_store=self._usage(), lists=lists
        )

        with _client(base) as client:
            _as_admin(client)
            response = client.post(
                "/stats/save/blacklist.txt",
                data={"text": "groq/llama-3.1-8b-instant  # плохо ведёт\n"},
            )

        assert response.status_code == 303
        assert "groq/llama-3.1-8b-instant" in lists.text("blacklist.txt")

    def test_an_unknown_filename_is_rejected(self, harness) -> None:
        from storage.lists import ModelLists

        lists = ModelLists(_root())
        _, base = harness(
            [_entry()], usage_store=self._usage(), lists=lists
        )

        with _client(base) as client:
            _as_admin(client)
            client.post("/stats/save/../../etc/passwd", data={"text": "x"})

        # Ничего, кроме известных файлов, не создаётся.
        assert lists.text("../../etc/passwd") == ""

    def test_a_person_cannot_save_lists(self, harness) -> None:
        from storage.lists import ModelLists

        _, base = harness([_entry()], usage_store=self._usage(), lists=ModelLists(_root()))

        with _client(base) as client:
            _as_guest(client)
            response = client.post("/stats/save/blacklist.txt", data={"text": "x"})

        assert response.status_code == 403


class TestCatalogueCacheRefresh:
    """Удаление немедленно обновляет кэш каталога (регрессия «не удаляется»).

    В проде ``state["providers"]`` обновлялся раз в 300 секунд, и
    ``merge_view`` строил вид из старого кэша: удалённая запись
    возвращалась и пересохранялась в снапшот — приходилось жать «убрать»
    много раз подряд. Теперь каждая успешная админ-мутация обновляет кэш.
    """

    @staticmethod
    def _harness_with_refresh(entries, refresh_calls):
        def refresh():
            refresh_calls.append(1)

        catalogue, server, base = _harness(entries, refresh=refresh)
        return catalogue, base

    def test_remove_refreshes_the_catalogue_cache(self) -> None:
        calls: list[int] = []
        _, base = self._harness_with_refresh([_entry()], calls)

        with _client(base) as client:
            _as_admin(client)
            response = client.post("/remove/ollama", follow_redirects=False)

        assert response.status_code == 303
        assert len(calls) == 1  # кэш обновлён сразу после удаления

    def test_edit_refreshes_the_catalogue_cache(self) -> None:
        calls: list[int] = []
        _, base = self._harness_with_refresh([_entry()], calls)

        with _client(base) as client:
            _as_admin(client)
            client.post("/edit/ollama", data={"name": "Ollama 2"})

        assert len(calls) == 1

    def test_a_failed_remove_does_not_refresh(self) -> None:
        """Запись не найдена (ничего не ушло на сервер) — кэш не трогаем."""
        calls: list[int] = []
        _, base = self._harness_with_refresh([_entry()], calls)

        with _client(base) as client:
            _as_admin(client)
            client.post("/remove/net-takogo")  # → 404, мутации не было

        assert calls == []

    def test_the_link_lives_in_the_nav_for_everyone(self, harness) -> None:
        _, base = harness([_entry()])

        with _client(base) as client:
            _as_guest(client)
            page = client.get("/")

        assert 'href="/stats"' in page.text

    def test_without_a_store_the_page_still_renders(self, harness) -> None:
        _, base = harness([_entry()])

        with _client(base) as client:
            _as_admin(client)
            page = client.get("/stats")

        assert page.status_code == 200
        assert "llama3.2" in page.text


class TestReputationPage:
    """Секция «на паузе» на странице статистики: только админ, с кнопкой."""

    @staticmethod
    def _reputation():
        from storage.reputation import ReputationStore

        store = ReputationStore()
        for _ in range(3):
            store.fault("ollama/llama3.2")
        return store

    def test_the_admin_sees_paused_models_with_a_button(self, harness) -> None:
        _, base = harness([_entry()], reputation=self._reputation())

        with _client(base) as client:
            _as_admin(client)
            page = client.get("/stats")

        assert "На паузе" in page.text
        assert "ollama/llama3.2" in page.text
        assert "/stats/release" in page.text

    def test_a_guest_never_sees_the_section(self, harness) -> None:
        _, base = harness([_entry()], reputation=self._reputation())

        with _client(base) as client:
            _as_guest(client)
            page = client.get("/stats")

        assert "На паузе" not in page.text

    def test_release_button_forgives_and_reports(self, harness) -> None:
        from storage.reputation import ReputationStore

        store = self._reputation()
        _, base = harness([_entry()], reputation=store)

        with _client(base) as client:
            _as_admin(client)
            done = client.post("/stats/release", data={"key": "ollama/llama3.2"}, follow_redirects=True)

        assert store.remaining("ollama/llama3.2") == 0
        assert "прощена" in done.text

    def test_a_guest_cannot_release(self, harness) -> None:
        from storage.reputation import ReputationStore

        store = self._reputation()
        _, base = harness([_entry()], reputation=store)

        with _client(base) as client:
            _as_guest(client)
            refused = client.post("/stats/release", data={"key": "ollama/llama3.2"})

        assert refused.status_code == 403
        assert store.remaining("ollama/llama3.2") > 0  # штрафы на месте


class TestSqlPlaygroundRoutes:
    @staticmethod
    def _database(path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE sample (value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('hello')")

    def test_admin_can_inspect_schema_and_run_select(self, harness, tmp_path) -> None:
        database = tmp_path / "data.db"
        self._database(database)
        _, base = harness([_entry()], sql_playground=SqlPlayground(database))

        with _client(base) as client:
            _as_admin(client)
            schema = client.get("/sql")
            result = client.post(
                "/sql/run",
                data={"query": "SELECT value FROM sample"},
            )

        assert schema.status_code == 200
        assert "sample" in schema.text
        assert schema.headers["Cache-Control"] == "no-store"
        assert result.status_code == 200
        assert "hello" in result.text
        assert "Результат запроса" in result.text

    def test_guest_cannot_view_or_query_database(self, harness, tmp_path) -> None:
        database = tmp_path / "data.db"
        self._database(database)
        _, base = harness([_entry()], sql_playground=SqlPlayground(database))

        with _client(base) as client:
            _as_guest(client)
            page = client.get("/sql")
            result = client.post("/sql/run", data={"query": "SELECT * FROM sample"})

        assert page.status_code == 403
        assert result.status_code == 403


class TestAutotestRun:
    """Запуск теста: post/redirect/GET — перезагрузка не перезапускает тест.

    Регрессия 23.10.2026: POST отдавал HTML прямо, и автообновление по
    окончании (location.reload) повторно отправляло форму — тест стартовал
    снова и снова.
    """

    @staticmethod
    def _fake_gateway(runs: list) -> object:
        class _GW:
            def run_autotest(self, provider_id, image_b64, prompt, tests,
                             record=False, **kwargs):
                runs.append({"provider": provider_id, "prompt": prompt,
                             "tests": tests, "record": record, **kwargs})
                return {"provider": provider_id, "results": [], "prompt": prompt}

        return _GW()

    @staticmethod
    def _wait_for(predicate, timeout: float = 3.0) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    @staticmethod
    def _build(tmp_path):
        from storage.autotest import AutotestStore

        _, server, base = _harness(
            [_entry()], autotest_store=AutotestStore(tmp_path)
        )
        return server, base

    @staticmethod
    def _multipart() -> bytes:
        return (
            "--B\r\n"
            'Content-Disposition: form-data; name="provider"\r\n\r\nollama\r\n'
            "--B\r\n"
            'Content-Disposition: form-data; name="prompt"\r\n\r\nhi\r\n'
            "--B\r\n"
            'Content-Disposition: form-data; name="test"\r\n\r\nimage\r\n'
            "--B\r\n"
            'Content-Disposition: form-data; name="voice"\r\n\r\nnova\r\n'
            "--B\r\n"
            'Content-Disposition: form-data; name="only_model"\r\n\r\nmy/pic\r\n'
            "--B\r\n"
            'Content-Disposition: form-data; name="audio"; filename="t.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
            "RIFF....WAVE\r\n"
            "--B--\r\n"
        ).encode()

    def test_run_redirects_instead_of_returning_html(self, tmp_path) -> None:
        server, base = self._build(tmp_path)
        try:
            runs: list = []
            server.gateway = self._fake_gateway(runs)
            with _client(base) as client:
                _as_admin(client)
                response = client.post(
                    "/autotest/run", content=self._multipart(),
                    headers={"Content-Type": "multipart/form-data; boundary=B"},
                    follow_redirects=False,
                )

            assert response.status_code == 303
            assert response.headers["location"].startswith("/autotest")
            assert self._wait_for(lambda: len(runs) == 1)  # тест запущен один раз
        finally:
            server.shutdown()
            server.server_close()

    def test_media_fields_reach_the_gateway(self, tmp_path) -> None:
        """Тип теста, голос, своя модель и аудиофайл уходят в прогон."""
        server, base = self._build(tmp_path)
        try:
            runs: list = []
            server.gateway = self._fake_gateway(runs)
            with _client(base) as client:
                _as_admin(client)
                client.post(
                    "/autotest/run", content=self._multipart(),
                    headers={"Content-Type": "multipart/form-data; boundary=B"},
                    follow_redirects=False,
                )

            assert self._wait_for(lambda: len(runs) == 1)
            run = runs[0]
            assert run["tests"] == ["image"]
            assert run["voice"] == "nova"
            assert run["only_model"] == "my/pic"
            assert run["audio"] == b"RIFF....WAVE"
            assert run["audio_name"] == "t.wav"
        finally:
            server.shutdown()
            server.server_close()

    def test_the_stop_route_redirects(self, tmp_path) -> None:
        server, base = self._build(tmp_path)
        try:
            server.gateway = self._fake_gateway([])
            with _client(base) as client:
                _as_admin(client)
                response = client.post("/autotest/stop", follow_redirects=False)

            assert response.status_code == 303
            assert response.headers["location"].startswith("/autotest")
        finally:
            server.shutdown()
            server.server_close()

    def test_a_user_cannot_run_or_stop(self, tmp_path) -> None:
        server, base = self._build(tmp_path)
        try:
            server.gateway = self._fake_gateway([])
            with _client(base) as client:
                _as_guest(client)

                assert client.post("/autotest/run", data={"provider": "x"}).status_code == 403
                assert client.post("/autotest/stop").status_code == 403
        finally:
            server.shutdown()
            server.server_close()


class TestVerifyPage:
    """Страница «Проверка»: доступна всем вошедшим, прогон идёт в фоне."""

    @staticmethod
    def _fake_gateway(runs: list) -> object:
        class _GW:
            def run_verify(self, provider_specs, system_prompt, user_prompt,
                           images, record=False):
                runs.append({"specs": provider_specs, "system": system_prompt,
                             "user": user_prompt, "n_images": len(images)})
                return {"provider": ",".join(s["id"] for s in provider_specs),
                        "results": [], "prompt": user_prompt,
                        "system_prompt": system_prompt, "images": images}

        return _GW()

    @staticmethod
    def _wait_for(predicate, timeout: float = 3.0) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    @staticmethod
    def _build(tmp_path):
        from storage.verify import VerifyStore

        _, server, base = _harness([_entry()], verify_store=VerifyStore(tmp_path))
        return server, base

    def test_a_run_goes_through_for_an_admin(self, tmp_path) -> None:
        server, base = self._build(tmp_path)
        try:
            runs: list = []
            server.gateway = self._fake_gateway(runs)
            with _client(base) as client:
                _as_admin(client)
                response = client.get("/verify")
                assert response.status_code == 200
                assert 'name="providers"' in response.text
                assert 'name="images"' in response.text

                # Один провайдер + одна маленькая картинка.
                body = (
                    "--B\r\n"
                    'Content-Disposition: form-data; name="providers"\r\n\r\nollama\r\n'
                    "--B\r\n"
                    'Content-Disposition: form-data; name="model_ollama"\r\n\r\nllama3.2\r\n'
                    "--B\r\n"
                    'Content-Disposition: form-data; name="user_prompt"\r\n\r\nчто на картинке?\r\n'
                    "--B\r\n"
                    'Content-Disposition: form-data; name="images"; filename="t.png"\r\n'
                    "Content-Type: image/png\r\n\r\n"
                    "PNGDATA\r\n"
                    "--B--\r\n"
                ).encode()
                response = client.post(
                    "/verify/run", content=body,
                    headers={"Content-Type": "multipart/form-data; boundary=B"},
                    follow_redirects=False,
                )
                assert response.status_code == 303
                assert response.headers["location"].startswith("/verify")
                assert self._wait_for(lambda: len(runs) == 1)
                assert runs[0]["n_images"] == 1
                assert runs[0]["specs"][0]["id"] == "ollama"
        finally:
            server.shutdown()
            server.server_close()


class TestVerifyPageAccess:
    """Доступ к «Проверке»: любой вошедший (не только админ)."""

    @staticmethod
    def _build(tmp_path):
        from storage.verify import VerifyStore

        _, server, base = _harness([_entry()], verify_store=VerifyStore(tmp_path))
        return server, base

    def test_export_returns_markdown_attachment(self, harness, tmp_path) -> None:
        from storage.verify import VerifyStore

        store = VerifyStore(tmp_path)
        store.begin("ollama", "что на картинке?",
                    system_prompt="коротко", images=["data:image/png;base64,AAAA"])
        store.finish("ollama", [
            {"provider": "ollama", "model": "llama3.2", "image_index": 0,
             "ts": "2026-10-30T10:00:00Z", "status": "ok", "http_status": 200,
             "duration": 1.0, "response": "кот на картинке"},
        ], prompt="что на картинке?", system_prompt="коротко",
           images=["data:image/png;base64,AAAA"])

        _, server, base = _harness([_entry()], verify_store=store)
        try:
            with _client(base) as client:
                _as_guest(client, "anna@mail")
                response = client.get("/verify/export")
            assert response.status_code == 200
            assert "attachment" in response.headers.get("content-disposition", "")
            assert "# Проверка" in response.text
            assert "коротко" in response.text
            assert "кот на картинке" in response.text
            assert "data:image/png;base64,AAAA" in response.text
        finally:
            server.shutdown()
            server.server_close()
