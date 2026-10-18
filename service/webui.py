"""Веб-интерфейс приставки: вход людей и их личные пространства.

Формы без JavaScript: POST → redirect (паттерн post/redirect/get),
поэтому кнопка «назад» в браузере не повторяет действие.

Аккаунты: человек входит логином/паролем (проверяет сервер каталога),
после чего страница помнит его в cookie; токен остаётся аварийным
входом и принадлежит админу. Каждый видит каталог глазами своего
пространства: свои ключи, свои переключатели, свои токены. Права:

* **админ** (суперпользо­ватель каталога) — правит определения,
  добавляет и убирает провайдеров, видит список людей;
* **человек** — своё пространство: ключ, проверка, выбор модели,
  тест-чат, токены для приложений.

Защита страницы: вход обязателен для всех (и с loopback); cookie —
HttpOnly + SameSite=Strict, чужой Origin отклоняется всегда.
"""

from __future__ import annotations

import hmac
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Callable

from core.accounts import ROLE_ADMIN, make_session
from core.catalogue import set_model as set_model_data
from core.page import (
    catalogue_page,
    edit_page,
    key_page,
    login_page,
    pick_model_page,
    signup_page,
    test_page,
    token_created_page,
    tokens_page,
    users_page,
)
from providers.client import PocketBase, Provider, ServerError
from service import ops
from service.gateway import test_chat
from service.space import Spaces
from storage.sessions import SessionStore
from storage.usertokens import UserTokens
from storage.users import UserStore

#: Имя cookie входа; значение — id сессии, срок — до закрытия браузера.
_COOKIE_NAME = "ai_bridge_session"


def _same(left: str, right: str) -> bool:
    """Сравнение секретов без утечки и без TypeError на не-ASCII."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class WebUi:
    """Маршруты страницы; владелец — HTTP-служба на том же порту.

    ``providers_source`` — сырой каталог сервера (без флагов);
    ``spaces`` — личные пространства; ``server`` — клиент каталога
    (проверка логинов и админ-действия); ``admin_id`` — суперпользо­ватель
    приставки; ``device_token`` — его аварийный токен. ``authenticator``
    и ``signup_hook`` подменяются в тестах.
    """

    def __init__(
        self,
        providers_source: Callable[[], list[dict]],
        server: PocketBase,
        *,
        spaces: Spaces,
        sessions: SessionStore,
        user_tokens: UserTokens,
        users: UserStore,
        admin_id: str,
        device_token: str = "",
        bound_host: str = "127.0.0.1",
        make_provider=None,
        authenticator: Callable[[str, str], tuple[str, str, bool]] | None = None,
        signup_hook: Callable[[str, str], None] | None = None,
    ) -> None:
        self._providers_source = providers_source
        self._server = server
        self._spaces = spaces
        self._sessions = sessions
        self._user_tokens = user_tokens
        self._users = users
        self._admin_id = admin_id
        self._device_token = device_token
        self._bound_host = bound_host
        self._make_provider = make_provider
        self._authenticator = authenticator or self._authenticate_on_server
        self._signup_hook = signup_hook or self._signup_on_server

    # ------------------------------------------------------------ вход

    def _authenticate_on_server(self, email: str, password: str) -> tuple[str, str, bool]:
        """(id, почта, админ?) через сервер каталога; отказ — ServerError."""
        probe = PocketBase(self._server.base_url)
        probe.login(email, password)
        _, _, can_edit, _ = probe.fetch_catalogue()
        return probe.user_id, email, can_edit

    def _signup_on_server(self, email: str, password: str) -> None:
        PocketBase(self._server.base_url).signup(email, password)

    def _viewer(self, handler: BaseHTTPRequestHandler) -> dict | None:
        """Кто вошёл: {id человека, почта, роль}; токен устройства = админ."""
        cookie = self._session_cookie(handler)
        if not cookie:
            return None
        session = self._sessions.get(cookie)
        if session:
            return {
                "id": str(session.get("userId") or ""),
                "email": str(session.get("email") or ""),
                "role": str(session.get("role") or ""),
            }
        if self._device_token and _same(cookie, self._device_token):
            owner = self._user_tokens.owner(self._device_token) or self._admin_id
            if owner:
                profile = self._users.get(owner) or {}
                return {"id": owner, "email": profile.get("email", ""), "role": ROLE_ADMIN}
        return None

    @staticmethod
    def _session_cookie(handler: BaseHTTPRequestHandler) -> str:
        cookie = handler.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == _COOKIE_NAME:
                return value.strip()
        return ""

    def _start_session(self, handler: BaseHTTPRequestHandler, viewer: dict) -> None:
        session = make_session(viewer["id"], viewer.get("email", ""), viewer.get("role", ""))
        self._sessions.add(session)
        handler.send_response(303)
        handler.send_header("Location", "/")
        handler.send_header(
            "Set-Cookie",
            f"{_COOKIE_NAME}={session['id']}; Path=/; HttpOnly; SameSite=Strict",
        )
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    # ------------------------------------------------------------ защита

    def _refusal(self, handler: BaseHTTPRequestHandler) -> str | None:
        """Чужой Origin (кнопки со страницы-обманки) отклоняется всегда.

        Вход обязателен для всех адресов, а cookie SameSite=Strict не
        уходит на чужие сайты — от CSRF защищает и то, и другое.
        """
        origin = (handler.headers.get("Origin") or "").strip()
        if origin and origin != "null":
            host = (handler.headers.get("Host") or "").strip().lower()
            origin_host = (urllib.parse.urlsplit(origin).netloc or "").lower()
            if origin_host != host:
                return "запрос пришёл с чужого сайта и отклонён"
        return None

    # ------------------------------------------------------------ ответы

    @staticmethod
    def _send_html(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
        body = html.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @classmethod
    def _redirect(cls, handler: BaseHTTPRequestHandler, notice: str = "", *, bad: bool = False) -> None:
        location = "/" + (f"?notice={urllib.parse.quote(notice)}&bad={1 if bad else 0}" if notice else "")
        handler.send_response(303)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    @classmethod
    def _forbidden(cls, handler: BaseHTTPRequestHandler, reason: str) -> None:
        cls._send_html(
            handler,
            403,
            f"<!doctype html><meta charset='utf-8'><title>Отказано</title>"
            f"<p>Отказано: {reason}</p><a href='/'>назад</a>",
        )

    @classmethod
    def _not_found(cls, handler: BaseHTTPRequestHandler) -> None:
        cls._send_html(
            handler,
            404,
            "<!doctype html><meta charset='utf-8'><title>Нет</title>"
            "<p>Нет такой страницы.</p><a href='/'>назад</a>",
        )

    # ------------------------------------------------------------ пространства

    def _entries_for(self, viewer: dict) -> list[dict]:
        """Каталог глазами вошедшего: сервер + его переключатели."""
        role = str(viewer.get("role") or "")
        return ops.merge_view(
            self._server,
            self._providers_source(),
            can_edit=role == ROLE_ADMIN,
            server_flags={},
            snapshot=self._spaces.for_user(str(viewer["id"])).snapshot,
            viewer_id=str(viewer["id"]),
        )

    def _find(self, viewer: dict, provider_id: str) -> dict | None:
        return next(
            (item for item in self._entries_for(viewer) if item["id"] == provider_id),
            None,
        )

    def _models_of(self, entry: dict, key: str) -> list[str]:
        factory = self._make_provider or Provider
        try:
            return [model["name"] for model in factory(entry["url"], timeout=15.0).fetch_models(key)]
        except Exception:  # noqa: BLE001 — список моделей — украшение, ошибка уйдёт в форму
            return []

    @staticmethod
    def _app_address(handler: BaseHTTPRequestHandler) -> str:
        """Адрес /v1 для приложений — по тому, как страница открыта.

        За туннелом хост без порта → https; по LAN/localhost с портом → http.
        """
        host = (handler.headers.get("Host") or "").strip().lower()
        if not host:
            return ""
        scheme = "http" if ":" in host else "https"
        return f"{scheme}://{host}/v1"

    # ------------------------------------------------------------ страницы

    def handle_get(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        refusal = self._refusal(handler)
        if refusal:
            self._forbidden(handler, refusal)
            return

        clean = urllib.parse.urlsplit(path).path
        query = urllib.parse.urlsplit(path).query
        params = urllib.parse.parse_qs(query)
        notice = (params.get("notice") or [""])[0]
        notice_bad = (params.get("bad") or ["0"])[0] == "1"

        if clean == "/login":
            self._send_html(handler, 200, login_page(notice if notice_bad else ""))
            return
        if clean == "/signup":
            self._send_html(handler, 200, signup_page(notice if notice_bad else ""))
            return

        viewer = self._viewer(handler)
        if viewer is None:
            self._send_html(handler, 200, login_page())
            return

        role = str(viewer.get("role") or "")
        is_admin = role == ROLE_ADMIN
        space = self._spaces.for_user(str(viewer["id"]))

        if clean in ("/", "/index.html"):
            try:
                stats = self._server.fetch_stats()
            except ServerError:
                stats = {}  # счётчик — украшение; без него страница живёт
            role_line = f"вы вошли как {viewer.get('email') or '—'} · {'администратор' if is_admin else 'человек'}"
            self._send_html(
                handler,
                200,
                catalogue_page(
                    self._entries_for(viewer),
                    stats=stats,
                    can_edit=is_admin,
                    keyed_ids=frozenset(space.keys.ids()),
                    notice=notice,
                    notice_bad=notice_bad,
                    role=role_line,
                    app_address=self._app_address(handler),
                ),
            )
            return

        if clean == "/tokens":
            self._send_html(
                handler,
                200,
                tokens_page(
                    self._user_tokens.list_for(str(viewer["id"])),
                    app_address=self._app_address(handler),
                    api_on=self._users.api_enabled(str(viewer["id"])),
                    notice=notice,
                    can_edit=is_admin,
                ),
            )
            return

        if clean == "/users":
            if not is_admin:
                self._forbidden(handler, "список людей — только администратору")
                return
            self._send_html(
                handler,
                200,
                users_page(self._users.list_all(), current_id=str(viewer["id"]), notice=notice),
            )
            return

        if clean.startswith("/key/"):
            provider_id = urllib.parse.unquote(clean[len("/key/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            self._send_html(
                handler,
                200,
                key_page(
                    entry["name"],
                    provider_id=provider_id,
                    has_key=bool(space.keys.get(provider_id)),
                    notice=notice if notice_bad else "",
                    can_edit=is_admin,
                    login_url=entry.get("loginUrl", ""),
                    key_url=entry.get("getApiKeyUrl", ""),
                ),
            )
            return

        if clean.startswith("/edit/"):
            if not is_admin:
                self._forbidden(handler, "правка определений — только администратору")
                return
            provider_id = urllib.parse.unquote(clean[len("/edit/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            self._send_html(handler, 200, edit_page(entry))
            return

        if clean.startswith("/pick/"):
            provider_id = urllib.parse.unquote(clean[len("/pick/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            models = self._models_of(entry, space.keys.get(provider_id))
            self._send_html(
                handler,
                200,
                pick_model_page(
                    entry["name"], provider_id, models,
                    current=entry.get("model", ""),
                    notice=notice if notice_bad else "",
                    can_edit=is_admin,
                ),
            )
            return

        self._not_found(handler)

    # ------------------------------------------------------------ действия

    def handle_post(self, handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        refusal = self._refusal(handler)
        if refusal:
            self._forbidden(handler, refusal)
            return

        clean = urllib.parse.urlsplit(path).path
        form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))

        def field(name: str) -> str:
            values = form.get(name, [""])
            return values[0].strip() if values else ""

        # -------------------------------------------------------- без сессии
        if clean == "/login":
            self._post_login(handler, field)
            return
        if clean == "/signup":
            self._post_signup(handler, field)
            return

        viewer = self._viewer(handler)
        if viewer is None:
            self._send_html(handler, 200, login_page("сначала войдите"))
            return

        if clean == "/logout":
            self._sessions.drop(self._session_cookie(handler))
            handler.send_response(303)
            handler.send_header("Location", "/login")
            handler.send_header(
                "Set-Cookie", f"{_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
            )
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return

        role = str(viewer.get("role") or "")
        is_admin = role == ROLE_ADMIN
        user_id = str(viewer["id"])
        space = self._spaces.for_user(user_id)

        if clean.startswith("/toggle/"):
            provider_id = urllib.parse.unquote(clean[len("/toggle/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            result = ops.set_enabled(
                self._server,
                self._entries_for(viewer),
                [provider_id],
                enabled=not entry.get("enabled"),
                can_edit=is_admin,
                snapshot=space.snapshot,
                sync=False,
            )
            word = "включён" if not entry.get("enabled") else "выключен"
            notice = f"{entry['name']}: {word}"
            if result["error"]:
                notice = result["error"]
            self._redirect(handler, notice)
            return

        if clean.startswith("/check/"):
            provider_id = urllib.parse.unquote(clean[len("/check/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            kwargs = {} if self._make_provider is None else {"make_provider": self._make_provider}
            result = ops.check(
                self._server, entry, space.keys.get(provider_id),
                report_for=user_id, **kwargs,
            )
            self._redirect(handler, self._check_notice(entry, result))
            return

        if clean.startswith("/test/"):
            provider_id = urllib.parse.unquote(clean[len("/test/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            outcome = test_chat(
                entry, space.keys.get(provider_id),
                **({} if self._make_provider is None else {"make_provider": self._make_provider}),
            )
            self._send_html(
                handler,
                200,
                test_page(
                    entry["name"], entry.get("model", ""),
                    outcome["question"], answer=outcome["answer"], error=outcome["error"],
                    can_edit=is_admin,
                ),
            )
            return

        if clean.startswith("/pick/"):
            provider_id = urllib.parse.unquote(clean[len("/pick/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            model = field("model")
            if not model:
                self._redirect(handler, "модель не выбрана", bad=True)
                return
            updated, error = set_model_data(self._entries_for(viewer), provider_id, model)
            if updated is None:
                self._redirect(handler, error or "не получилось", bad=True)
                return
            space.snapshot.save(updated)
            self._redirect(handler, f"{entry['name']}: модель {model}")
            return

        if clean == "/key":
            provider_id = field("id")
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            space.keys.set(provider_id, field("api_key"))
            self._redirect(handler, f"{entry['name']}: ключ сохранён в вашем пространстве")
            return

        if clean == "/tokens/api":
            turned_on = not self._users.api_enabled(user_id)
            self._users.set_api(user_id, turned_on)
            self._redirect(
                handler,
                "доступ приложений включён — токены работают"
                if turned_on
                else "доступ приложений выключен — токены не пройдут",
            )
            return

        if clean == "/tokens/create":
            name = field("name")
            if not name:
                self._redirect(handler, "назовите токен", bad=True)
                return
            token = self._user_tokens.create(user_id, name)
            self._send_html(handler, 200, token_created_page(token, name))
            return

        if clean == "/tokens/revoke":
            if self._user_tokens.revoke(user_id, field("tail")):
                self._redirect(handler, "токен отозван")
            else:
                self._redirect(handler, "такого токена нет", bad=True)
            return

        # -------------------------------------------------------- только админ
        if not is_admin:
            self._forbidden(handler, "это действие — только администратору")
            return

        admin_space = self._spaces.for_user(user_id)

        if clean.startswith("/remove/"):
            provider_id = urllib.parse.unquote(clean[len("/remove/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            result = ops.remove(
                self._server, self._entries_for(viewer), self._providers_source(),
                provider_id, can_edit=True,
                snapshot=admin_space.snapshot, key_store=admin_space.keys,
            )
            notice = (result["note"] and f"{entry['name']}: {result['note']}") or ""
            self._redirect(handler, result["error"] or notice)
            return

        if clean.startswith("/edit/"):
            provider_id = urllib.parse.unquote(clean[len("/edit/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            changes = {
                name: field(name)
                for name in (
                    "name", "url", "model", "fallbackPriority",
                    "loginUrl", "getApiKeyUrl", "context", "pricing", "capabilities",
                )
            }
            result = ops.edit(
                self._server, self._entries_for(viewer), self._providers_source(),
                provider_id, changes, can_edit=True, snapshot=admin_space.snapshot,
            )
            notice = f"{entry['name']}: сохранено" if result["error"] is None else result["error"]
            self._redirect(handler, notice)
            return

        if clean == "/add":
            result = ops.add(
                self._server, self._entries_for(viewer), self._providers_source(),
                name=field("name"), url=field("url"), model=field("model"),
                key=field("api_key"), can_edit=True,
                snapshot=admin_space.snapshot, key_store=admin_space.keys,
            )
            if result["entry"] is None:
                self._redirect(handler, result["error"] or "не получилось", bad=True)
                return
            notice = f"{result['entry']['name']}: {result['note'] or result['error']}"
            self._redirect(handler, notice)
            return

        if clean == "/users/delete":
            target = field("id")
            if target == user_id:
                self._redirect(handler, "себя удалить нельзя", bad=True)
                return
            try:
                self._server.delete_user(target)
            except ServerError as exc:
                self._redirect(handler, f"на сервере не удалилось ({exc})", bad=True)
                return
            self._user_tokens.drop_user(target)
            self._sessions.drop_user(target)
            self._users.purge(target)
            self._redirect(handler, "пользователь удалён: аккаунт, токены и пространство")
            return

        self._not_found(handler)

    # ------------------------------------------------------------ вход

    def _post_login(self, handler: BaseHTTPRequestHandler, field) -> None:
        token = field("token")
        if token:
            if self._device_token and _same(token, self._device_token):
                owner = self._user_tokens.owner(self._device_token) or self._admin_id
                profile = self._users.get(owner) or {}
                self._start_session(
                    handler, {"id": owner, "email": profile.get("email", ""), "role": ROLE_ADMIN}
                )
                return
            self._send_html(handler, 200, login_page("неверный токен"))
            return

        email, password = field("email"), field("password")
        if not email or not password:
            self._send_html(handler, 200, login_page("введите почту и пароль"))
            return
        try:
            user_id, email, can_edit = self._authenticator(email, password)
        except ServerError:
            self._send_html(handler, 200, login_page("почта или пароль не подошли"))
            return
        if not user_id:
            self._send_html(handler, 200, login_page("сервер не сказал, кто вы"))
            return
        role = ROLE_ADMIN if can_edit else "user"
        self._users.remember(user_id, email, role=role)
        self._start_session(handler, {"id": user_id, "email": email, "role": role})

    def _post_signup(self, handler: BaseHTTPRequestHandler, field) -> None:
        email, password, twice = field("email"), field("password"), field("password2")
        if not email or not password:
            self._send_html(handler, 200, signup_page("введите почту и пароль"))
            return
        if password != twice:
            self._send_html(handler, 200, signup_page("пароли не совпали"))
            return
        try:
            self._signup_hook(email, password)
        except ServerError as exc:
            self._send_html(handler, 200, signup_page(f"регистрация не прошла: {exc}"))
            return
        try:
            user_id, email, can_edit = self._authenticator(email, password)
        except ServerError:
            self._send_html(handler, 200, signup_page("аккаунт создан — войдите паролем"))
            return
        role = ROLE_ADMIN if can_edit else "user"
        self._users.remember(user_id, email, role=role)
        self._start_session(handler, {"id": user_id, "email": email, "role": role})

    # ------------------------------------------------------------ мелочи

    @staticmethod
    def _check_notice(entry: dict, result: dict) -> str:
        """Короткий итог проверки для строки уведомления."""
        if result["dead"]:
            return f"{entry['name']}: {result['dead']}"
        outcome = result["outcome"]
        if outcome["ok"]:
            text = f"{entry['name']}: работает"
            if outcome["model"]:
                text += f" · модель {outcome['model']}"
        else:
            text = f"{entry['name']}: {outcome['error'] or 'не получилось'}"
        if result["report_error"]:
            text += f" (отчёт не ушёл: {result['report_error']})"
        elif outcome["report"] and result["counter"]:
            text += f" · счётчик: {result['counter']}"
        return text
