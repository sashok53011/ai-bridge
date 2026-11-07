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
import json
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from typing import Callable

from core.accounts import ROLE_ADMIN, make_session
from core.catalogue import set_model as set_model_data
from core.page import (
    add_page,
    catalogue_page,
    edit_page,
    key_page,
    login_page,
    logs_page,
    oauth_page,
    pick_model_page,
    signup_page,
    sql_playground_page,
    stats_page,
    test_page,
    token_created_page,
    tokens_page,
    users_page,
)
from providers import copilot
from providers.client import NeedsKeyError, PocketBase, Provider, ServerError
from service import ops
from service.gateway import (
    _merge_capabilities,
    embed_text,
    generate_3d,
    generate_audio,
    generate_image,
    generate_transcript,
    media_models,
    process_audio,
    test_chat,
)
from service.space import Spaces
from storage.sqlplayground import SqlPlaygroundError
from storage.sessions import SessionStore
from storage.usertokens import UserTokens
from storage.users import UserStore

#: Имя cookie входа; значение — id сессии, срок — до закрытия браузера.
_COOKIE_NAME = "ai_bridge_session"


def _same(left: str, right: str) -> bool:
    """Сравнение секретов без утечки и без TypeError на не-ASCII."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _rule_note(store, where: str, viewer: dict) -> str:
    """Описание последнего добавленного правила — для журнала и страницы."""
    
    data = store.all()
    rules = data.get(where) or []
    if not rules:
        return where
    rule = rules[-1]
    actions = RESPONSE_ACTIONS if where == "response" else REQUEST_ACTIONS
    action = actions.get(str(rule.get("action") or ""), str(rule.get("action") or ""))
    head = str(rule.get("provider") or where)
    return f"{head}: {describe(rule)} → {action}"


#: Человекочитаемые имена страниц — в журнал, а не каша из слэшей.
_PAGE_TITLES = {
    "/": "Каталог",
    "/index.html": "Каталог",
    "/add": "Новый провайдер",
    "/stats": "Статистика",
    "/sql": "SQL Playground",
    "/tokens": "Приложения и токены",
    "/messages": "Сообщения",
    "/bugs": "Нашёл bug",
    "/logs": "Журнал",
    "/rules": "Правила",
    "/autotest": "Автотесты",
    "/users": "Пользователи",
}

#: Динамические префиксы: /edit/gemini → «Правка: gemini».
_PAGE_PREFIXES = (
    ("/edit/", "правка "),
    ("/pick/", "выбор модели "),
    ("/key/", "ключ "),
    ("/oauth/", "вход github "),
    ("/test/", "тест "),
    ("/toggle/", "переключатель "),
    ("/remove/", "удаление "),
)


def _page_title(path: str) -> str:
    """Имя страницы для журнала: /messages → «Сообщения», /edit/x → «правка x»."""
    if path in _PAGE_TITLES:
        return _PAGE_TITLES[path]
    for prefix, label in _PAGE_PREFIXES:
        if path.startswith(prefix):
            return label + urllib.parse.unquote(path[len(prefix):])
    return path


#: Человекочитаемые имена разделов правил.
_WHERE_TITLES = {"response": "ответы", "request": "запросы"}


def _preview(text: str, limit: int = 150) -> str:
    """Первые знаки текста для журнала и пушей: суть видна, строка не рвётся."""
    flat = " ".join(str(text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _test_tone() -> bytes:
    """Тестовый звук для stt: полусекунды тона 440 Гц (WAV, 16 kHz, mono).

    Без своего аудиофайла под рукой тест распознавания заведомо не
    пройдёт, поэтому при пустом поле форма подставляет этот тон: файл
    собирается здесь же, без сети и без файловой системы.
    """
    import io
    import math
    import struct
    import wave

    rate = 16000
    frames = int(rate * 0.5)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        for index in range(frames):
            value = int(12000 * math.sin(2 * math.pi * 440 * index / rate))
            wav.writeframes(struct.pack("<h", value))
    return buffer.getvalue()


def _rule_at(store, where: str, index: int) -> str:
    """Описание правила по номеру — для журнала (правка/удаление).

    Журнал должен отвечать «что именно», а не «№0 в response»: через
    неделю номера сдвигаются, а описание — нет.
    """
    
    rules = store.all().get(where) or []
    if not 0 <= index < len(rules):
        return f"{_WHERE_TITLES.get(where, where)} №{index} (не найдено)"
    rule = rules[index]
    actions = RESPONSE_ACTIONS if where == "response" else REQUEST_ACTIONS
    action = actions.get(str(rule.get("action") or ""), str(rule.get("action") or ""))
    head = str(rule.get("provider") or _WHERE_TITLES.get(where, where))
    return f"{head}: {describe(rule)} → {action}"


class WebUi:
    """Маршруты страницы; владелец — HTTP-служба на том же порту.

    ``providers_source`` — сырой каталог сервера (без флагов);
    ``spaces`` — личные пространства; ``server`` — клиент каталога
    (проверка логинов и админ-действия); ``admin_id`` — суперпользо­ватель
    приставки; ``device_token`` — его аварийный токен. ``authenticator``
    и ``signup_hook`` подменяются в тестах; ``event_log`` — журнал
    событий (кто заходил, на что нажал).
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
        event_log=None,  # service.eventlog.EventLog | None
        usage_store=None,  # storage.usage.UsageStore | None
        reputation=None,  # storage.reputation.ReputationStore | None
        capabilities=None,  # storage.capabilities.CapabilityStore | None
        lists=None,  # storage.lists.ModelLists | None
        refresh=None,  # Callable[[], None] — обновить кэш каталога после правок
        sql_playground=None,  # storage.sqlplayground.SqlPlayground | None
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
        self._log = event_log
        self._usage = usage_store
        self._reputation = reputation
        self._capabilities = capabilities
        self._lists = lists
        self._refresh = refresh
        self._sql_playground = sql_playground
        # Удалённые разделы: заглушки, чтобы старый код не падал.
        self._rules = None
        self._autotest_store = None
        self._verify_store = None
        self._board = None
        self._mail = None
        self._notify = None        # Ждущие device-flow входа GitHub: (user_id, provider_id) → состояние.
        self._oauth_pending: dict[tuple[str, str], dict] = {}

    def _test_page_html(self, entry: dict, key: str, is_admin: bool, *,
                        question: str = "", answer: str = "", error: str = "",
                        **results) -> str:
        """Страница теста со всеми секциями: списки моделей — одним походом.

        ``results`` — результаты секций (image_result, audio_result,
        three_result, embed_result); чат-блок есть только с вопросом —
        иначе страница открыта после генерации и чужой ответ не к месту.
        """
        kwargs = (
            {} if self._make_provider is None
            else {"make_provider": self._make_provider}
        )
        media = media_models(entry, key, **kwargs)
        # Каталог моделей — для select «любая модель» в форме промпта
        # (провайдер недоступен — поля нет, тест идёт на настроенной).
        models: list[str] = []
        try:
            models = [str(name) for name in self._models_of(entry, key) if name]
        except Exception:  # noqa: BLE001
            models = []
        voices: list[str] = []
        if media.get("audio") or media.get("voice"):
            # Голоса — отдельный каталог (у Pollinations /audio/models);
            # подсказка для поля голоса, при отказе — пусто, форма работает.
            try:
                factory = self._make_provider or Provider
                catalogue = factory(entry["url"], timeout=10.0).fetch_audio_catalogue(key)
                voices = sorted({voice for list_ in catalogue.values() for voice in list_})
            except Exception:  # noqa: BLE001
                voices = []
        return test_page(
            entry["name"], entry.get("model", ""), question,
            answer=answer, error=error, can_edit=is_admin,
            provider_id=str(entry["id"]), media=media, voices=voices,
            models=models, current_model=str(entry.get("model") or ""),
            **results,
        )

    def _note_media(self, viewer: dict, entry: dict, what: str, *,
                    prompt: str, model: str, error: str) -> None:
        """Событие в журнал о результате генерации (строки — обрезанные)."""
        who = str(viewer.get("email") or "")
        if error:
            self._event("тест", who,
                        f"{entry['name']}: {what} не получился — {error[:120]}")
            return
        detail = f" «{prompt[:60]}»" if prompt else ""
        tail = f" ({model})" if model else ""
        self._event("тест", who, f"{entry['name']}: {what}{detail} — готово{tail}")

    def _refresh_now(self) -> None:
        """Обновить кэш каталога сразу после админ-правки (если передан)."""
        if self._refresh is None:
            return
        try:
            self._refresh()
        except Exception:  # noqa: BLE001 — обновление не должно ронять действие
            pass

    def _sql_page(
        self,
        *,
        query: str = "",
        result: dict | None = None,
        error: str = "",
        include_data: bool = False,
        is_admin: bool = False,
    ) -> str:
        schema = {"tables": [], "relations": []}
        if self._sql_playground is not None:
            try:
                schema = self._sql_playground.schema(include_data=include_data)
            except SqlPlaygroundError as exc:
                error = error or str(exc)
        return sql_playground_page(
            schema=schema,
            query=query,
            result=result,
            error=error,
            include_data=include_data,
            configured=self._sql_playground is not None,
            is_admin=is_admin,
        )

    def _event(self, kind: str, user: str, text: str, **fields) -> None:
        """Одно событие в журнал; без журнала — молча пропускаем."""
        if self._log is not None:
            self._log.record(kind, user, text, **fields)

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
    def _send_html(handler: BaseHTTPRequestHandler, status: int, html: str,
                   *, cache_control: str | None = None) -> None:
        body = html.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        if cache_control:
            handler.send_header("Cache-Control", cache_control)
        handler.end_headers()
        handler.wfile.write(body)

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    @classmethod
    def _redirect(
        cls,
        handler: BaseHTTPRequestHandler,
        notice: str = "",
        *,
        bad: bool = False,
        to: str = "/",
    ) -> None:
        """303 на ``to`` (по умолчанию — главная) с уведомлением в URL.

        ``to`` нужно страницам с автообновлением (журнал): после
        переключателя возвращаем туда же, а не на главную.
        """
        query = f"?notice={urllib.parse.quote(notice)}&bad={1 if bad else 0}" if notice else ""
        handler.send_response(303)
        handler.send_header("Location", to + query)
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

    def _local_statuses(self, entries: list[dict]) -> dict[str, str]:
        """Живые строки состояния локальных провайдеров для каталога.

        Только локальные (localhost/LAN) и включённые; таймаут 1.5 с —
        висящий сервер не держит страницу (норма — десятки мс).
        Недоступен — без строки, молча.
        """
        from core.routes import is_local_url

        statuses: dict[str, str] = {}
        for entry in entries:
            if not entry.get("enabled") or not is_local_url(str(entry.get("url") or "")):
                continue
            factory = self._make_provider or Provider
            try:
                line = factory(entry["url"], timeout=1.5).local_status()
            except Exception:  # noqa: BLE001 — статус не должен ломать каталог
                continue
            if line:
                statuses[entry["id"]] = line
        return statuses

    @staticmethod
    def _parse_multipart(body: bytes, content_type: str) -> tuple[dict, dict]:
        """Разобрать multipart/form-data: (поля, файлы).

        Возвращает (form_dict, files_dict), где form_dict — {имя: [значение]},
        files_dict — {имя: (имя_файла, bytes)}.
        """
        form: dict[str, list[str]] = {}
        files: dict[str, tuple[str, bytes]] = {}

        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("boundary="):
                boundary = part[len("boundary="):].strip().strip('"')
                break
        if not boundary:
            return form, files

        delimiter = ("--" + boundary).encode("utf-8")
        chunks = body.split(delimiter)
        for chunk in chunks[1:-1]:
            if chunk.startswith(b"\r\n"):
                chunk = chunk[2:]
            header_end = chunk.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            headers_raw = chunk[:header_end].decode("utf-8", errors="replace")
            content = chunk[header_end + 4:]
            if content.endswith(b"\r\n"):
                content = content[:-2]

            name = None
            filename = None
            for line in headers_raw.split("\r\n"):
                if line.lower().startswith("content-disposition:"):
                    for token in line.split(";"):
                        token = token.strip()
                        if token.lower().startswith("name="):
                            name = token[len("name="):].strip().strip('"')
                        elif token.lower().startswith("filename="):
                            filename = token[len("filename="):].strip().strip('"')

            if name is None:
                continue
            if filename:
                files[name] = (filename, content)
            else:
                form.setdefault(name, []).append(content.decode("utf-8", errors="replace").strip())

        return form, files

    def _grey_text(self, viewer: dict) -> str:
        """Серый список этого человека (персональный каталог) для страницы."""
        from storage.lists import GREYLIST, ModelLists

        try:
            directory = self._spaces.for_user(str(viewer["id"])).directory
            return ModelLists(directory).text(GREYLIST)
        except Exception:  # noqa: BLE001
            return ""

    def _grey_rows(self, viewer: dict) -> list[dict]:
        """Строки таблицы таймера серого списка: кто спит и сколько осталось.

        Провайдер спит целиком (все модели); остаток — в минутах,
        причина и номер цикла — из комментария файла.
        """
        from storage.lists import ModelLists

        try:
            directory = self._spaces.for_user(str(viewer["id"])).directory
            cells = ModelLists(directory).grey()
        except Exception:  # noqa: BLE001
            return []
        now = datetime.now(timezone.utc)
        rows = []
        for provider_id, cell in cells.items():
            left = int((cell["until"] - now).total_seconds() // 60)
            rows.append(
                {
                    "id": provider_id,
                    "minutes": left,
                    "reason": cell.get("reason") or "—",
                    "cycle": int(cell.get("cycle") or 1),
                }
            )
        rows.sort(key=lambda row: row["minutes"])
        return rows

    def _whitelist_groups(self) -> dict:
        """Белый список по группам: {группа: содержимое файла}."""
        from storage.lists import WHITELIST_FILES

        groups: dict = {}
        for group, filename in WHITELIST_FILES.items():
            groups[group] = self._lists.text(filename)
        return groups

    def _site_link(self, handler: BaseHTTPRequestHandler) -> str:
        """Внешний адрес приставки (scheme://host) — для ссылки в уведомлении.

        Без Host пустая строка: ссылка тогда относительная, тоже работает.
        """
        host = (handler.headers.get("Host") or "").strip()
        if not host:
            return ""
        return ("http://" if ":" in host else "https://") + host

    def _push(self, user_id: str, text: str) -> None:
        """Толкнуть уведомление человеку в Telegram — в фоне.

        Токен берётся из настроек, chat_id — из профиля; нет того или
        другого — молчим. Сеть не должна держать ответ страницы, поэтому
        отдельным потоком; ошибка отправки — в stderr (журнал службы),
        письмо она уже не отменит.
        """
        if self._notify is None:
            return
        token = self._notify.token()
        if not token:
            return
        chat_id = self._users.tg(user_id)
        if not chat_id:
            return

        def worker() -> None:
            from providers import telegram

            try:
                error = telegram.send(token, chat_id, text)
                if error:
                    print(f"telegram: {error}", file=sys.stderr, flush=True)
            except Exception as exc:  # noqa: BLE001 — пуш никогда не роняет действие
                print(f"telegram: {exc}", file=sys.stderr, flush=True)

        threading.Thread(target=worker, daemon=True).start()

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

        # Язык: ?lang=xx в URL → cookie → по умолчанию ru.
        from core.i18n import set_lang, LANGS
        lang = (params.get("lang") or [""])[0]
        if lang in LANGS:
            handler.send_response(302)
            handler.send_header("Location", f"/{clean}" if clean != "/" else "/")
            handler.send_header(
                "Set-Cookie",
                f"ai_lang={lang}; Path=/; Max-Age=31536000; SameSite=Strict",
            )
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        cookie_lang = ""
        cookie_header = handler.headers.get("Cookie") or ""
        for part in cookie_header.split(";"):
            kv = part.strip().split("=", 1)
            if len(kv) == 2 and kv[0] == "ai_lang":
                cookie_lang = kv[1].strip()
        set_lang(cookie_lang if cookie_lang in LANGS else "ru")

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
        # Просмотр самого журнала не пишется: страница обновляется каждые
        # 5 секунд и засоряла бы журнал самим собой. Страница ожидания
        # OAuth опрашивается так же часто — молчим и о ней. Имя —
        # человеческое («Сообщения», а не /messages), путь — поле события.
        if clean != "/logs" and not clean.startswith("/oauth/"):
            self._event(
                "страница",
                str(viewer.get("email") or ""),
                _page_title(clean or "/"),
                path=clean or "/",
            )

        if clean in ("/", "/index.html"):
            entries = self._entries_for(viewer)
            role_line = f"вы вошли как {viewer.get('email') or '—'} · {'администратор' if is_admin else 'человек'}"
            self._send_html(
                handler,
                200,
                catalogue_page(
                    entries,
                    can_edit=is_admin,
                    keyed_ids=frozenset(space.keys.ids()),
                    notice=notice,
                    notice_bad=notice_bad,
                    role=role_line,
                    app_address=self._app_address(handler),
                    statuses=self._local_statuses(entries),
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

        if clean == "/add":
            self._send_html(handler, 200, add_page(is_admin=is_admin))
            return



        if clean == "/stats":
            # Статистика — только админу: здесь его расходы, его списки
            # и его решения. Обычному человеку эта страница не нужна.
            if not is_admin:
                self._forbidden(handler, "статистика — только администратору")
                return
            usage = self._usage.totals() if self._usage else {}
            rows = []
            for entry in self._entries_for(viewer):
                model = str(entry.get("model") or "")
                key = f"{entry['id']}/{model}"
                cell = usage.get(key) or {}
                # Способности: рукописная справка с учётом выученного.
                caps = str(entry.get("capabilities") or "")
                if self._capabilities is not None:
                    try:
                        known = self._capabilities.known(key)
                    except Exception:  # noqa: BLE001
                        known = {}
                    if known:
                        caps = _merge_capabilities(caps, known)
                rows.append(
                    {
                        "model": model or "—",
                        "provider": entry["name"],
                        "ctx": str(entry.get("context") or ""),
                        "caps": caps,
                        "requests": int(cell.get("requests", 0)),
                        "fails": int(cell.get("fails", 0)),
                        "last": str(cell.get("last") or ""),
                    }
                )
            rows.sort(key=lambda row: (-row["requests"], row["model"]))
            penalized = self._reputation.penalized() if self._reputation else []
            codes: dict[str, str] = {}
            if self._lists:
                for name in self._lists.code_names():
                    codes[name] = self._lists.code_text(name)
            self._send_html(
                handler,
                200,
                stats_page(
                    rows, can_edit=True, notice=notice, penalized=penalized,
                    whitelist=self._whitelist_groups() if self._lists else {},
                    greylist=self._grey_text(viewer) if self._lists else "",
                    grey_rows=self._grey_rows(viewer) if self._lists else [],
                    blacklist=self._lists.text("blacklist.txt") if self._lists else "",
                    novision=self._lists.text("novision.txt") if self._lists else "",
                    notools=self._lists.text("notools.txt") if self._lists else "",
                    paid=self._lists.text("paid.txt") if self._lists else "",
                    code_lists=codes,
                ),
            )
            return

        if clean == "/logs":
            if not is_admin:
                self._forbidden(handler, "журнал — только администратору")
                return
            events = self._log.recent(500) if self._log is not None else []
            self._send_html(
                handler,
                200,
                logs_page(
                    events,
                    notice=notice,
                ),
                cache_control="no-store",
            )
            return

        if clean == "/users":
            if not is_admin:
                self._forbidden(handler, "список людей — только администратору")
                return
            remote: dict[str, dict] = {}
            try:
                remote = {item["id"]: item for item in self._server.people()}
            except ServerError:
                remote = {}  # реестр молчит — покажем хотя бы локальные профили
            people = [
                {
                    **profile,
                    "blocked": bool(remote.get(str(profile.get("id")), {}).get("blocked")),
                }
                for profile in self._users.list_all()
            ]
            self._send_html(
                handler,
                200,
                users_page(people, current_id=str(viewer["id"]), notice=notice),
            )
            return

        if clean.startswith("/oauth/"):
            # Страница ожидания device-flow: код уже выдан, ждём подтверждения.
            provider_id = urllib.parse.unquote(clean[len("/oauth/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            pending = self._oauth_pending.get((str(viewer["id"]), provider_id))
            if pending is None:
                self._redirect(handler, "вход через GitHub не начат", bad=True,
                               to=f"/key/{urllib.parse.quote(provider_id)}")
                return
            if time.time() > pending["deadline"]:
                del self._oauth_pending[(str(viewer["id"]), provider_id)]
                self._redirect(handler, "код истёк — начните вход заново", bad=True,
                               to=f"/key/{urllib.parse.quote(provider_id)}")
                return
            self._send_html(
                handler,
                200,
                oauth_page(
                    entry["name"],
                    provider_id=provider_id,
                    user_code=pending["user_code"],
                    verify_url=pending["url"],
                    interval=int(pending.get("interval") or 5),
                    notice=notice if notice_bad else "",
                    can_edit=is_admin,
                ),
            )
            return

        if clean.startswith("/edit/"):
            if not is_admin:
                self._forbidden(handler, "правка определений — только администратору")
                return
            provider_id = urllib.parse.unquote(clean[len("/edit/") :])
            error = ""
            if self._sql_playground is None:
                error = "SQL Playground не настроен."
            elif query:
                try:
                    result = self._sql_playground.execute(query)
                except SqlPlaygroundError as exc:
                    error = str(exc)
            self._send_html(
                handler,
                200,
                self._sql_page(
                    query=query,
                    result=result,
                    error=error,
                    include_data=include_data,
                ),
                cache_control="no-store",
            )
            return

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
            self._event("тумблер", str(viewer.get("email") or ""), notice)
            self._redirect(handler, notice)
            return

        if clean.startswith("/test/"):
            provider_id = urllib.parse.unquote(clean[len("/test/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            key = space.keys.get(provider_id)
            kwargs = (
                {} if self._make_provider is None
                else {"make_provider": self._make_provider}
            )
            # Свой промпт приходит из формы на странице; пустой — как всегда,
            # крошечный вопрос по умолчанию.
            question = field("question").strip()
            chat_kwargs = dict(kwargs)
            if question:
                chat_kwargs["question"] = question
            image_url = field("image_url").strip()
            if image_url:
                chat_kwargs["image_url"] = image_url
            picked = field("test_model").strip()
            if picked:
                chat_kwargs["model"] = picked
            outcome = test_chat(entry, key, **chat_kwargs)
            # Тест — главная проверка провайдера: настоящий ответ модели
            # засчитывается в статистику сообщества под именем человека.
            ops.report_chat(self._server, entry, outcome, report_for=str(viewer["id"]))
            self._event(
                "тест",
                str(viewer.get("email") or ""),
                f"{entry['name']}: "
                + (f"ответ от {entry.get('model', '')}" if not outcome["error"] else outcome["error"]),
            )
            self._send_html(
                handler, 200,
                self._test_page_html(
                    entry, key, is_admin, question=outcome["question"],
                    answer=outcome["answer"], error=outcome["error"],
                ),
            )
            return

        if clean.startswith("/imgtest/"):
            provider_id = urllib.parse.unquote(clean[len("/imgtest/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            key = space.keys.get(provider_id)
            kwargs = (
                {} if self._make_provider is None
                else {"make_provider": self._make_provider}
            )
            result = generate_image(entry, key, prompt=field("prompt"),
                                    model=field("model"), **kwargs)
            self._note_media(viewer, entry, "картинка",
                             prompt=str(result.get("prompt") or ""),
                             model=str(result.get("model") or ""),
                             error=str(result.get("error") or ""))
            self._send_html(
                handler, 200,
                self._test_page_html(entry, key, is_admin, image_result=result),
            )
            return

        # Озвучка, музыка, 3D, вектор, распознавание и обработка звука —
        # по тому же шаблону, свой маршрут на тип.
        media_kind = ""
        tail = ""
        if clean.startswith("/audiotest/"):
            media_kind, tail = "audio", clean[len("/audiotest/") :]
        elif clean.startswith("/3dtest/"):
            media_kind, tail = "3d", clean[len("/3dtest/") :]
        elif clean.startswith("/embedtest/"):
            media_kind, tail = "embedding", clean[len("/embedtest/") :]
        elif clean.startswith("/stttest/"):
            media_kind, tail = "stt", clean[len("/stttest/") :]
        elif clean.startswith("/voicetest/"):
            media_kind, tail = "voice", clean[len("/voicetest/") :]
        if media_kind:
            provider_id = urllib.parse.unquote(tail)
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            key = space.keys.get(provider_id)
            kwargs = (
                {} if self._make_provider is None
                else {"make_provider": self._make_provider}
            )
            if media_kind == "audio":
                result = generate_audio(
                    entry, key, text=field("text"), model=field("model"),
                    voice=field("voice"), **kwargs,
                )
                # Музыка — тот же маршрут, но свой результат и своя секция.
                if field("kind") == "music":
                    result_key, what = "music_result", "музыка"
                else:
                    result_key, what = "audio_result", "озвучка"
            elif media_kind == "3d":
                result = generate_3d(
                    entry, key, image=field("image"), model=field("model"),
                    resolution=field("resolution") or "low",
                    prompt=field("prompt"), **kwargs,
                )
                result_key, what = "three_result", "3D"
            elif media_kind == "stt":
                # Свой файл; пусто — тестовый тон (без звука тест бессмыслен).
                audio = None
                audio_name = "test-tone.wav"
                mime = "audio/wav"
                uploaded = file_field("audio")
                if uploaded is not None and uploaded[1]:
                    import mimetypes as _mt

                    audio_name = uploaded[0] or "sample.wav"
                    audio = uploaded[1]
                    mime = _mt.guess_type(audio_name)[0] or "application/octet-stream"
                else:
                    audio = _test_tone()
                result = generate_transcript(
                    entry, key, model=field("model"), audio=audio,
                    audio_name=audio_name, mime=mime, **kwargs,
                )
                result_key, what = "transcript_result", "распознавание"
            elif media_kind == "voice":
                # Значение select — «действие|модель»; файл — свой или тон.
                raw_model = field("model")
                action, sep, model = raw_model.partition("|")
                if not sep:
                    action, model = "isolator", raw_model
                audio = None
                audio_name = "test-tone.wav"
                mime = "audio/wav"
                uploaded = file_field("audio")
                if uploaded is not None and uploaded[1]:
                    import mimetypes as _mt

                    audio_name = uploaded[0] or "sample.wav"
                    audio = uploaded[1]
                    mime = _mt.guess_type(audio_name)[0] or "application/octet-stream"
                else:
                    audio = _test_tone()
                result = process_audio(
                    entry, key, action=action, model=model, audio=audio,
                    audio_name=audio_name, mime=mime, voice=field("voice"),
                    **kwargs,
                )
                result_key, what = "voice_result", "обработка звука"
            else:
                result = embed_text(
                    entry, key, text=field("text"), model=field("model"), **kwargs,
                )
                result_key, what = "embed_result", "вектор"
            self._note_media(viewer, entry, what,
                             prompt=str(result.get("text")
                                        or result.get("image") or ""),
                             model=str(result.get("model") or ""),
                             error=str(result.get("error") or ""))
            self._send_html(
                handler, 200,
                self._test_page_html(entry, key, is_admin, **{result_key: result}),
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
            # Отметка личного выбора: переживёт обновление каталога.
            updated = [
                {**item, "picked": True} if item["id"] == provider_id else item
                for item in updated
            ]
            space.snapshot.save(updated)
            self._event("модель", str(viewer.get("email") or ""), f"{entry['name']} -> {model}")
            self._redirect(handler, f"{entry['name']}: модель {model}")
            return

        if clean == "/key":
            provider_id = field("id")
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            space.keys.set(provider_id, field("api_key"))
            self._event("ключ", str(viewer.get("email") or ""), f"{entry['name']}: сохранён")
            self._redirect(handler, f"{entry['name']}: ключ сохранён в вашем пространстве")
            return

        if clean.startswith("/oauth/"):
            provider_id = urllib.parse.unquote(clean[len("/oauth/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            key_to = f"/key/{urllib.parse.quote(provider_id)}"
            oauth_to = f"/oauth/{urllib.parse.quote(provider_id)}"
            state = (user_id, provider_id)
            if field("action") != "check":
                # Начало входа: GitHub выдаёт одноразовый код и ссылку.
                if not copilot.is_copilot_url(entry.get("url")):
                    self._redirect(handler, "этот провайдер входит через GitHub только "
                                   "по адресу Copilot", bad=True, to=key_to)
                    return
                flow = copilot.start_device_flow()
                if flow.get("error"):
                    self._redirect(handler, f"вход не начался: {flow['error']}", bad=True,
                                   to=key_to)
                    return
                self._oauth_pending[state] = {
                    "device_code": flow["device_code"],
                    "user_code": flow["user_code"],
                    "url": flow["url"],
                    "interval": int(flow.get("interval") or 5),
                    "deadline": time.time() + int(flow.get("expires_in") or 900),
                }
                self._event("вход", str(viewer.get("email") or ""),
                            f"{entry['name']}: начат вход через GitHub")
                self._redirect(handler, "", to=oauth_to)
                return
            # Проверка: один опрос GitHub — готов ли токен.
            pending = self._oauth_pending.get(state)
            if pending is None:
                self._redirect(handler, "вход через GitHub не начат", bad=True, to=key_to)
                return
            if time.time() > pending["deadline"]:
                del self._oauth_pending[state]
                self._redirect(handler, "код истёк — начните вход заново", bad=True, to=key_to)
                return
            kind, value = copilot.poll_device_flow(pending["device_code"])
            if kind == "done":
                del self._oauth_pending[state]
                space.keys.set(provider_id, value)
                self._event("ключ", str(viewer.get("email") or ""),
                            f"{entry['name']}: вход через GitHub выполнен, ключ сохранён")
                self._redirect(handler, f"{entry['name']}: вход через GitHub выполнен, "
                               "ключ сохранён", to=key_to)
                return
            if kind == "error":
                del self._oauth_pending[state]
                self._event("вход", str(viewer.get("email") or ""),
                            f"{entry['name']}: вход через GitHub не состоялся ({value})")
                self._redirect(handler, value, bad=True, to=key_to)
                return
            # Ещё не подтверждено — возвращаемся на страницу ожидания,
            # её скрипт сам спросит GitHub через несколько секунд.
            if value == "slow_down":
                # GitHub просит опрашивать реже (RFC 8628).
                pending["interval"] = int(pending.get("interval") or 5) + 5
            self._redirect(handler, "", to=oauth_to)
            return

        if clean == "/tokens/api":
            turned_on = not self._users.api_enabled(user_id)
            self._users.set_api(user_id, turned_on)
            self._event(
                "доступ приложений",
                str(viewer.get("email") or ""),
                "включён" if turned_on else "выключен",
            )
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
            self._event("токен", str(viewer.get("email") or ""), f"создан «{name}»")
            self._send_html(
                handler, 200, token_created_page(token, name, can_edit=is_admin)
            )
            return

        if clean == "/tokens/revoke":
            tail = field("tail")
            # Название токена — до отзыва, иначе в журнале останется хвост.
            name = next(
                (str(t.get("name") or "") for t in self._user_tokens.list_for(user_id)
                 if str(t.get("tail") or "") == tail),
                "",
            )
            if self._user_tokens.revoke(user_id, tail):
                self._event(
                    "токен", str(viewer.get("email") or ""),
                    f"отозван «{name}»" if name else f"отозван (…{tail})",
                )
                self._redirect(handler, "токен отозван")
            else:
                self._redirect(handler, "такого токена нет", bad=True)
            return

        if clean == "/add":
            values = {
                name: field(name)
                for name in (
                    "name", "url", "model", "api_key", "loginUrl", "getApiKeyUrl",
                    "logoUrl", "infoUrl", "pricing", "context", "capabilities",
                )
            }
            if field("action") == "fetch":
                models: list[str] = []
                error = ""
                if not values["url"]:
                    error = "введите адрес провайдера — по нему запросим модели"
                else:
                    factory = self._make_provider or Provider
                    try:
                        models = [
                            item["name"]
                            for item in factory(values["url"], timeout=15.0).fetch_models(
                                values["api_key"]
                            )
                        ]
                        if not models:
                            error = "провайдер не вернул ни одной модели"
                    except NeedsKeyError:
                        error = (
                            "без ключа список недоступен — введите API-ключ "
                            "и нажмите кнопку ещё раз"
                        )
                    except Exception as exc:  # noqa: BLE001 — причина уходит в подсказку
                        error = f"список не получился ({exc})"
                self._send_html(
                    handler,
                    200,
                    add_page(
                        values=values,
                        models=models if models else None,
                        models_error=error,
                        is_admin=is_admin,
                    ),
                )
                return
            result = ops.add(
                self._server,
                self._entries_for(viewer),
                self._providers_source(),
                name=values["name"],
                url=values["url"],
                model=values["model"],
                key=values["api_key"],
                login_url=values["loginUrl"],
                key_url=values["getApiKeyUrl"],
                details={
                    key: values[key]
                    for key in ("logoUrl", "infoUrl", "pricing", "context", "capabilities")
                },
                can_edit=is_admin,
                snapshot=space.snapshot,
                key_store=space.keys,
                sync=is_admin,
                author_id="" if is_admin else user_id,
            )
            if result["entry"] is None:
                self._redirect(handler, result["error"] or "не получилось", bad=True)
                return
            if is_admin:
                self._refresh_now()  # запись ушла на сервер — свежий каталог
            self._event(
                "провайдер",
                str(viewer.get("email") or ""),
                f"добавлен {result['entry']['name']}: {result['note'] or result['error']}",
                url=values["url"],
            )
            # Каталог — общее дело: кто бы ни добавил, админу в Telegram.
            self._push(
                self._admin_id,
                f"Новый провайдер: {result['entry']['name']} — "
                f"добавил {str(viewer.get('email') or '—')}\n"
                f"{self._site_link(handler)}/",
            )
            self._redirect(handler, f"{result['entry']['name']}: {result['note'] or result['error']}")
            return

        # ------------------------------------------------------- доска багов
        # Общий разговор для всех вошедших: сообщение, комментарий,
        # статус с обязательным пояснением. Удаление — только админу,
        # но проверяется оно здесь же, ниже, а не общим шлагбаумом.
        # письма администратору — ящик остаётся открытым, власть полная.
        # («узнать» читает getUpdates), тест — живой вызов API.
        # -------------------------------------------------------- только админ
        if not is_admin:
            self._forbidden(handler, "это действие — только администратору")
            return

        admin_space = self._spaces.for_user(str(viewer["id"]))
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
            if result["code"] == 0:
                # Кэш каталога устарел при push — обновляем сразу, иначе
                # следующая перерисовка вернёт запись из старого кэша.
                self._refresh_now()
            notice = (result["note"] and f"{entry['name']}: {result['note']}") or ""
            self._event("провайдер", str(viewer.get("email") or ""), notice or result["error"])
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
                    "getApiKeyUrl",
                )
            }
            if field("action") == "fetch":
                # Показать модели: ничего не сохраняем, только перерисовать
                # форму со списком. Ключ — введённый сейчас, иначе сохранённый.
                key = field("api_key") or admin_space.keys.get(provider_id)
                models: list[str] = []
                error = ""
                if not changes["url"]:
                    error = "введите адрес провайдера — по нему запросим модели"
                else:
                    factory = self._make_provider or Provider
                    try:
                        models = [
                            item["name"]
                            for item in factory(changes["url"], timeout=15.0).fetch_models(key)
                        ]
                        if not models:
                            error = "провайдер не вернул ни одной модели"
                    except NeedsKeyError:
                        error = (
                            "без ключа список недоступен — введите API-ключ "
                            "и нажмите кнопку ещё раз"
                        )
                    except Exception as exc:  # noqa: BLE001 — причина уходит в подсказку
                        error = f"список не получился ({exc})"
                values = {name: field(name) for name in (
                    "name", "url", "model", "fallbackPriority",
                    "getApiKeyUrl", "api_key",
                )}
                self._send_html(
                    handler,
                    200,
                    edit_page(
                        entry,
                        values=values,
                        models=models if models else None,
                        models_error=error,
                        has_key=bool(admin_space.keys.get(provider_id)),
                        oauth=copilot.is_copilot_url(changes["url"] or entry.get("url")),
                    ),
                )
                return
            # Ключ живёт на устройстве (хранилище), поля определения — в каталоге.
            key_note = ""
            if field("api_key"):
                admin_space.keys.set(provider_id, field("api_key"))
                key_note = " · ключ обновлён"
                self._event(
                    "ключ", str(viewer.get("email") or ""),
                    f"{entry['name']}: сохранён при правке",
                )
            result = ops.edit(
                self._server, self._entries_for(viewer), self._providers_source(),
                provider_id, changes, can_edit=True, snapshot=admin_space.snapshot,
            )
            if result["error"] is None:
                self._refresh_now()  # кэш устарел при push — свежий каталог
            notice = f"{entry['name']}: сохранено" if result["error"] is None else result["error"]
            if result["error"] is None and result.get("note"):
                notice += f" · {result['note']}"
            notice += key_note
            self._event(
                "правка",
                str(viewer.get("email") or ""),
                notice,
                **{key: value for key, value in changes.items() if value},
            )
            self._redirect(handler, notice, to=f"/edit/{urllib.parse.quote(provider_id)}")
            return


        if clean == "/sql":
            self._send_html(
                handler, 200,
                self._sql_page(
                    include_data=(params.get("data") or ["0"])[0] == "1",
                    is_admin=is_admin,
                ),
                cache_control="no-store",
            )
            return

        if clean == "/logs/clear":
            if self._log is not None:
                self._log.clear()
            self._event("журнал", str(viewer.get("email") or ""), "очищен")
            self._redirect(handler, "журнал очищен", to="/logs")
            return

        self._not_found(handler)

    # ------------------------------------------------------------ вход

    def handle_post(self, handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        refusal = self._refusal(handler)
        if refusal:
            self._forbidden(handler, refusal)
            return

        clean = urllib.parse.urlsplit(path).path
        content_type = handler.headers.get("Content-Type", "")

        # Язык из cookie (для POST-ответов).
        from core.i18n import set_lang, LANGS
        cookie_lang = ""
        cookie_header = handler.headers.get("Cookie") or ""
        for part in cookie_header.split(";"):
            kv = part.strip().split("=", 1)
            if len(kv) == 2 and kv[0] == "ai_lang":
                cookie_lang = kv[1].strip()
        set_lang(cookie_lang if cookie_lang in LANGS else "ru")

        if content_type.startswith("multipart/form-data"):
            form, files = self._parse_multipart(body, content_type)
        else:
            form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
            files = {}

        def field(name: str) -> str:
            values = form.get(name, [""])
            return values[0].strip() if values else ""

        def file_field(name: str) -> tuple[str, bytes] | None:
            return files.get(name)

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
            self._event("вход", str(viewer.get("email") or ""), "вышел")
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

        if clean == "/sql/run":
            query = field("query")
            include_data = field("include_data") == "1"
            result = None
            error = ""
            if self._sql_playground is None:
                error = "SQL Playground не настроен."
            elif query:
                try:
                    result = self._sql_playground.execute(query, read_only=not is_admin)
                except SqlPlaygroundError as exc:
                    error = str(exc)
            self._send_html(
                handler,
                200,
                self._sql_page(
                    query=query,
                    result=result,
                    error=error,
                    include_data=include_data,
                    is_admin=is_admin,
                ),
                cache_control="no-store",
            )
            return

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
            self._event("тумблер", str(viewer.get("email") or ""), notice)
            self._redirect(handler, notice)
            return

        if clean.startswith("/test/"):
            provider_id = urllib.parse.unquote(clean[len("/test/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            key = space.keys.get(provider_id)
            kwargs = (
                {} if self._make_provider is None
                else {"make_provider": self._make_provider}
            )
            # Свой промпт приходит из формы на странице; пустой — как всегда,
            # крошечный вопрос по умолчанию.
            question = field("question").strip()
            chat_kwargs = dict(kwargs)
            if question:
                chat_kwargs["question"] = question
            image_url = field("image_url").strip()
            if image_url:
                chat_kwargs["image_url"] = image_url
            picked = field("test_model").strip()
            if picked:
                chat_kwargs["model"] = picked
            outcome = test_chat(entry, key, **chat_kwargs)
            # Тест — главная проверка провайдера: настоящий ответ модели
            # засчитывается в статистику сообщества под именем человека.
            ops.report_chat(self._server, entry, outcome, report_for=str(viewer["id"]))
            self._event(
                "тест",
                str(viewer.get("email") or ""),
                f"{entry['name']}: "
                + (f"ответ от {entry.get('model', '')}" if not outcome["error"] else outcome["error"]),
            )
            self._send_html(
                handler, 200,
                self._test_page_html(
                    entry, key, is_admin, question=outcome["question"],
                    answer=outcome["answer"], error=outcome["error"],
                ),
            )
            return

        if clean.startswith("/imgtest/"):
            provider_id = urllib.parse.unquote(clean[len("/imgtest/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            key = space.keys.get(provider_id)
            kwargs = (
                {} if self._make_provider is None
                else {"make_provider": self._make_provider}
            )
            result = generate_image(entry, key, prompt=field("prompt"),
                                    model=field("model"), **kwargs)
            self._note_media(viewer, entry, "картинка",
                             prompt=str(result.get("prompt") or ""),
                             model=str(result.get("model") or ""),
                             error=str(result.get("error") or ""))
            self._send_html(
                handler, 200,
                self._test_page_html(entry, key, is_admin, image_result=result),
            )
            return

        # Озвучка, музыка, 3D, вектор, распознавание и обработка звука —
        # по тому же шаблону, свой маршрут на тип.
        media_kind = ""
        tail = ""
        if clean.startswith("/audiotest/"):
            media_kind, tail = "audio", clean[len("/audiotest/") :]
        elif clean.startswith("/3dtest/"):
            media_kind, tail = "3d", clean[len("/3dtest/") :]
        elif clean.startswith("/embedtest/"):
            media_kind, tail = "embedding", clean[len("/embedtest/") :]
        elif clean.startswith("/stttest/"):
            media_kind, tail = "stt", clean[len("/stttest/") :]
        elif clean.startswith("/voicetest/"):
            media_kind, tail = "voice", clean[len("/voicetest/") :]
        if media_kind:
            provider_id = urllib.parse.unquote(tail)
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            key = space.keys.get(provider_id)
            kwargs = (
                {} if self._make_provider is None
                else {"make_provider": self._make_provider}
            )
            if media_kind == "audio":
                result = generate_audio(
                    entry, key, text=field("text"), model=field("model"),
                    voice=field("voice"), **kwargs,
                )
                # Музыка — тот же маршрут, но свой результат и своя секция.
                if field("kind") == "music":
                    result_key, what = "music_result", "музыка"
                else:
                    result_key, what = "audio_result", "озвучка"
            elif media_kind == "3d":
                result = generate_3d(
                    entry, key, image=field("image"), model=field("model"),
                    resolution=field("resolution") or "low",
                    prompt=field("prompt"), **kwargs,
                )
                result_key, what = "three_result", "3D"
            elif media_kind == "stt":
                # Свой файл; пусто — тестовый тон (без звука тест бессмыслен).
                audio = None
                audio_name = "test-tone.wav"
                mime = "audio/wav"
                uploaded = file_field("audio")
                if uploaded is not None and uploaded[1]:
                    import mimetypes as _mt

                    audio_name = uploaded[0] or "sample.wav"
                    audio = uploaded[1]
                    mime = _mt.guess_type(audio_name)[0] or "application/octet-stream"
                else:
                    audio = _test_tone()
                result = generate_transcript(
                    entry, key, model=field("model"), audio=audio,
                    audio_name=audio_name, mime=mime, **kwargs,
                )
                result_key, what = "transcript_result", "распознавание"
            elif media_kind == "voice":
                # Значение select — «действие|модель»; файл — свой или тон.
                raw_model = field("model")
                action, sep, model = raw_model.partition("|")
                if not sep:
                    action, model = "isolator", raw_model
                audio = None
                audio_name = "test-tone.wav"
                mime = "audio/wav"
                uploaded = file_field("audio")
                if uploaded is not None and uploaded[1]:
                    import mimetypes as _mt

                    audio_name = uploaded[0] or "sample.wav"
                    audio = uploaded[1]
                    mime = _mt.guess_type(audio_name)[0] or "application/octet-stream"
                else:
                    audio = _test_tone()
                result = process_audio(
                    entry, key, action=action, model=model, audio=audio,
                    audio_name=audio_name, mime=mime, voice=field("voice"),
                    **kwargs,
                )
                result_key, what = "voice_result", "обработка звука"
            else:
                result = embed_text(
                    entry, key, text=field("text"), model=field("model"), **kwargs,
                )
                result_key, what = "embed_result", "вектор"
            self._note_media(viewer, entry, what,
                             prompt=str(result.get("text")
                                        or result.get("image") or ""),
                             model=str(result.get("model") or ""),
                             error=str(result.get("error") or ""))
            self._send_html(
                handler, 200,
                self._test_page_html(entry, key, is_admin, **{result_key: result}),
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
            # Отметка личного выбора: переживёт обновление каталога.
            updated = [
                {**item, "picked": True} if item["id"] == provider_id else item
                for item in updated
            ]
            space.snapshot.save(updated)
            self._event("модель", str(viewer.get("email") or ""), f"{entry['name']} -> {model}")
            self._redirect(handler, f"{entry['name']}: модель {model}")
            return

        if clean == "/key":
            provider_id = field("id")
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            space.keys.set(provider_id, field("api_key"))
            self._event("ключ", str(viewer.get("email") or ""), f"{entry['name']}: сохранён")
            self._redirect(handler, f"{entry['name']}: ключ сохранён в вашем пространстве")
            return

        if clean.startswith("/oauth/"):
            provider_id = urllib.parse.unquote(clean[len("/oauth/") :])
            entry = self._find(viewer, provider_id)
            if entry is None:
                self._not_found(handler)
                return
            key_to = f"/key/{urllib.parse.quote(provider_id)}"
            oauth_to = f"/oauth/{urllib.parse.quote(provider_id)}"
            state = (user_id, provider_id)
            if field("action") != "check":
                # Начало входа: GitHub выдаёт одноразовый код и ссылку.
                if not copilot.is_copilot_url(entry.get("url")):
                    self._redirect(handler, "этот провайдер входит через GitHub только "
                                   "по адресу Copilot", bad=True, to=key_to)
                    return
                flow = copilot.start_device_flow()
                if flow.get("error"):
                    self._redirect(handler, f"вход не начался: {flow['error']}", bad=True,
                                   to=key_to)
                    return
                self._oauth_pending[state] = {
                    "device_code": flow["device_code"],
                    "user_code": flow["user_code"],
                    "url": flow["url"],
                    "interval": int(flow.get("interval") or 5),
                    "deadline": time.time() + int(flow.get("expires_in") or 900),
                }
                self._event("вход", str(viewer.get("email") or ""),
                            f"{entry['name']}: начат вход через GitHub")
                self._redirect(handler, "", to=oauth_to)
                return
            # Проверка: один опрос GitHub — готов ли токен.
            pending = self._oauth_pending.get(state)
            if pending is None:
                self._redirect(handler, "вход через GitHub не начат", bad=True, to=key_to)
                return
            if time.time() > pending["deadline"]:
                del self._oauth_pending[state]
                self._redirect(handler, "код истёк — начните вход заново", bad=True, to=key_to)
                return
            kind, value = copilot.poll_device_flow(pending["device_code"])
            if kind == "done":
                del self._oauth_pending[state]
                space.keys.set(provider_id, value)
                self._event("ключ", str(viewer.get("email") or ""),
                            f"{entry['name']}: вход через GitHub выполнен, ключ сохранён")
                self._redirect(handler, f"{entry['name']}: вход через GitHub выполнен, "
                               "ключ сохранён", to=key_to)
                return
            if kind == "error":
                del self._oauth_pending[state]
                self._event("вход", str(viewer.get("email") or ""),
                            f"{entry['name']}: вход через GitHub не состоялся ({value})")
                self._redirect(handler, value, bad=True, to=key_to)
                return
            # Ещё не подтверждено — возвращаемся на страницу ожидания,
            # её скрипт сам спросит GitHub через несколько секунд.
            if value == "slow_down":
                # GitHub просит опрашивать реже (RFC 8628).
                pending["interval"] = int(pending.get("interval") or 5) + 5
            self._redirect(handler, "", to=oauth_to)
            return

        if clean == "/tokens/api":
            turned_on = not self._users.api_enabled(user_id)
            self._users.set_api(user_id, turned_on)
            self._event(
                "доступ приложений",
                str(viewer.get("email") or ""),
                "включён" if turned_on else "выключен",
            )
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
            self._event("токен", str(viewer.get("email") or ""), f"создан «{name}»")
            self._send_html(
                handler, 200, token_created_page(token, name, can_edit=is_admin)
            )
            return

        if clean == "/tokens/revoke":
            tail = field("tail")
            # Название токена — до отзыва, иначе в журнале останется хвост.
            name = next(
                (str(t.get("name") or "") for t in self._user_tokens.list_for(user_id)
                 if str(t.get("tail") or "") == tail),
                "",
            )
            if self._user_tokens.revoke(user_id, tail):
                self._event(
                    "токен", str(viewer.get("email") or ""),
                    f"отозван «{name}»" if name else f"отозван (…{tail})",
                )
                self._redirect(handler, "токен отозван")
            else:
                self._redirect(handler, "такого токена нет", bad=True)
            return

        if clean == "/add":
            values = {
                name: field(name)
                for name in (
                    "name", "url", "model", "api_key", "loginUrl", "getApiKeyUrl",
                    "logoUrl", "infoUrl", "pricing", "context", "capabilities",
                )
            }
            if field("action") == "fetch":
                models: list[str] = []
                error = ""
                if not values["url"]:
                    error = "введите адрес провайдера — по нему запросим модели"
                else:
                    factory = self._make_provider or Provider
                    try:
                        models = [
                            item["name"]
                            for item in factory(values["url"], timeout=15.0).fetch_models(
                                values["api_key"]
                            )
                        ]
                        if not models:
                            error = "провайдер не вернул ни одной модели"
                    except NeedsKeyError:
                        error = (
                            "без ключа список недоступен — введите API-ключ "
                            "и нажмите кнопку ещё раз"
                        )
                    except Exception as exc:  # noqa: BLE001 — причина уходит в подсказку
                        error = f"список не получился ({exc})"
                self._send_html(
                    handler,
                    200,
                    add_page(
                        values=values,
                        models=models if models else None,
                        models_error=error,
                        is_admin=is_admin,
                    ),
                )
                return
            result = ops.add(
                self._server,
                self._entries_for(viewer),
                self._providers_source(),
                name=values["name"],
                url=values["url"],
                model=values["model"],
                key=values["api_key"],
                login_url=values["loginUrl"],
                key_url=values["getApiKeyUrl"],
                details={
                    key: values[key]
                    for key in ("logoUrl", "infoUrl", "pricing", "context", "capabilities")
                },
                can_edit=is_admin,
                snapshot=space.snapshot,
                key_store=space.keys,
                sync=is_admin,
                author_id="" if is_admin else user_id,
            )
            if result["entry"] is None:
                self._redirect(handler, result["error"] or "не получилось", bad=True)
                return
            if is_admin:
                self._refresh_now()  # запись ушла на сервер — свежий каталог
            self._event(
                "провайдер",
                str(viewer.get("email") or ""),
                f"добавлен {result['entry']['name']}: {result['note'] or result['error']}",
                url=values["url"],
            )
            # Каталог — общее дело: кто бы ни добавил, админу в Telegram.
            self._push(
                self._admin_id,
                f"Новый провайдер: {result['entry']['name']} — "
                f"добавил {str(viewer.get('email') or '—')}\n"
                f"{self._site_link(handler)}/",
            )
            self._redirect(handler, f"{result['entry']['name']}: {result['note'] or result['error']}")
            return

        # ------------------------------------------------------- доска багов
        # Общий разговор для всех вошедших: сообщение, комментарий,
        # статус с обязательным пояснением. Удаление — только админу,
        # но проверяется оно здесь же, ниже, а не общим шлагбаумом.
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
            if result["code"] == 0:
                # Кэш каталога устарел при push — обновляем сразу, иначе
                # следующая перерисовка вернёт запись из старого кэша.
                self._refresh_now()
            notice = (result["note"] and f"{entry['name']}: {result['note']}") or ""
            self._event("провайдер", str(viewer.get("email") or ""), notice or result["error"])
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
                    "getApiKeyUrl",
                )
            }
            if field("action") == "fetch":
                # Показать модели: ничего не сохраняем, только перерисовать
                # форму со списком. Ключ — введённый сейчас, иначе сохранённый.
                key = field("api_key") or admin_space.keys.get(provider_id)
                models: list[str] = []
                error = ""
                if not changes["url"]:
                    error = "введите адрес провайдера — по нему запросим модели"
                else:
                    factory = self._make_provider or Provider
                    try:
                        models = [
                            item["name"]
                            for item in factory(changes["url"], timeout=15.0).fetch_models(key)
                        ]
                        if not models:
                            error = "провайдер не вернул ни одной модели"
                    except NeedsKeyError:
                        error = (
                            "без ключа список недоступен — введите API-ключ "
                            "и нажмите кнопку ещё раз"
                        )
                    except Exception as exc:  # noqa: BLE001 — причина уходит в подсказку
                        error = f"список не получился ({exc})"
                values = {name: field(name) for name in (
                    "name", "url", "model", "fallbackPriority",
                    "getApiKeyUrl", "api_key",
                )}
                self._send_html(
                    handler,
                    200,
                    edit_page(
                        entry,
                        values=values,
                        models=models if models else None,
                        models_error=error,
                        has_key=bool(admin_space.keys.get(provider_id)),
                        oauth=copilot.is_copilot_url(changes["url"] or entry.get("url")),
                    ),
                )
                return
            # Ключ живёт на устройстве (хранилище), поля определения — в каталоге.
            key_note = ""
            if field("api_key"):
                admin_space.keys.set(provider_id, field("api_key"))
                key_note = " · ключ обновлён"
                self._event(
                    "ключ", str(viewer.get("email") or ""),
                    f"{entry['name']}: сохранён при правке",
                )
            result = ops.edit(
                self._server, self._entries_for(viewer), self._providers_source(),
                provider_id, changes, can_edit=True, snapshot=admin_space.snapshot,
            )
            if result["error"] is None:
                self._refresh_now()  # кэш устарел при push — свежий каталог
            notice = f"{entry['name']}: сохранено" if result["error"] is None else result["error"]
            if result["error"] is None and result.get("note"):
                notice += f" · {result['note']}"
            notice += key_note
            self._event(
                "правка",
                str(viewer.get("email") or ""),
                notice,
                **{key: value for key, value in changes.items() if value},
            )
            self._redirect(handler, notice, to=f"/edit/{urllib.parse.quote(provider_id)}")
            return

        if clean == "/users/block":
            target = field("id")
            if target == user_id:
                self._redirect(handler, "себя заблокировать нельзя", bad=True)
                return
            try:
                remote = {item["id"]: item for item in self._server.people()}
                blocked = not bool(remote.get(target, {}).get("blocked"))
                self._server.set_blocked(target, blocked)
            except ServerError as exc:
                self._redirect(handler, f"на реестре не получилось ({exc})", bad=True)
                return
            self._redirect(
                handler,
                "заблокирован — не пускает ни одна приставка" if blocked else "разблокирован",
            )
            self._event(
                "блокировка",
                str(viewer.get("email") or ""),
                f"{remote.get(target, {}).get('email') or target}: "
                f"{'заблокирован' if blocked else 'разблокирован'}",
            )
            return

        if clean == "/users/delete":
            target = field("id")
            if target == user_id:
                self._redirect(handler, "себя удалить нельзя", bad=True)
                return
            # Почта — до purge(): очищенный профиль не ответит, кто удалён.
            profile = self._users.get(target) or {}
            victim = str(profile.get("email") or target)
            try:
                self._server.delete_user(target)
            except ServerError as exc:
                self._redirect(handler, f"на сервере не удалилось ({exc})", bad=True)
                return
            self._user_tokens.drop_user(target)
            self._sessions.drop_user(target)
            self._users.purge(target)
            self._event(
                "удаление", str(viewer.get("email") or ""),
                f"пользователь {victim} удалён: аккаунт, токены и пространство",
            )
            self._redirect(handler, "пользователь удалён: аккаунт, токены и пространство")
            return

        if clean == "/stats/release":
            if not is_admin or self._reputation is None:
                self._forbidden(handler, "прощение модели — только администратору")
                return
            key = field("key")
            self._reputation.release(key)
            self._event("репутация", str(viewer.get("email") or ""), f"модель {key} прощена")
            self._redirect(handler, f"модель {key} прощена — штрафы сняты")
            return

        if clean.startswith("/stats/save/"):
            # Правка списков руками: файл в состоянии приставки, действие
            # мгновенное (файлы перечитаются на каждый запрос).
            if not is_admin or self._lists is None:
                self._forbidden(handler, "правка списков — только администратору")
                return
            which = urllib.parse.unquote(clean[len("/stats/save/") :])
            ok = self._lists.write_text(which, field("text"))
            self._event(
                "списки", str(viewer.get("email") or ""),
                f"{which}: {'сохранено' if ok else 'неизвестный файл'}",
            )
            self._redirect(
                handler,
                f"{which}: сохранено" if ok else f"{which}: неизвестный файл",
                bad=not ok, to="/stats",
            )
            return

        if clean == "/stats/codes/create":
            if not is_admin or self._lists is None:
                self._forbidden(handler, "создание списков — только администратору")
                return
            name = field("name").strip().lower()
            if not self._lists.code_valid(name):
                self._redirect(handler, "имя — кодовое слово: латиница, цифры, дефис",
                               bad=True, to="/stats")
                return
            self._lists.code_write(name, field("text"))
            self._event("списки", str(viewer.get("email") or ""), f"создан список {name}")
            self._redirect(handler, f"список {name} создан", to="/stats")
            return

        if clean.startswith("/stats/codes/save/"):
            if not is_admin or self._lists is None:
                self._forbidden(handler, "правка списков — только администратору")
                return
            name = urllib.parse.unquote(clean[len("/stats/codes/save/"):]).strip().lower()
            ok = self._lists.code_write(name, field("text"))
            self._event(
                "списки", str(viewer.get("email") or ""),
                f"{name}: {'сохранено' if ok else 'невалидное имя'}",
            )
            self._redirect(
                handler,
                f"{name}: сохранено" if ok else f"{name}: невалидное имя",
                bad=not ok, to="/stats",
            )
            return

        if clean.startswith("/stats/codes/drop/"):
            if not is_admin or self._lists is None:
                self._forbidden(handler, "удаление списков — только администратору")
                return
            name = urllib.parse.unquote(clean[len("/stats/codes/drop/"):]).strip().lower()
            ok = self._lists.code_drop(name)
            self._event("списки", str(viewer.get("email") or ""), f"удалён список {name}")
            self._redirect(
                handler,
                f"список {name} удалён" if ok else f"{name}: не найден",
                bad=not ok, to="/stats",
            )
            return

        if clean == "/logs/clear":
            if self._log is not None:
                self._log.clear()
            self._event("журнал", str(viewer.get("email") or ""), "очищен")
            self._redirect(handler, "журнал очищен", to="/logs")
            return

        self._not_found(handler)

    # ------------------------------------------------------------ вход


    def _post_login(self, handler: BaseHTTPRequestHandler, field) -> None:
        token = field("token")
        if token:
            if self._device_token and _same(token, self._device_token):
                owner = self._user_tokens.owner(self._device_token) or self._admin_id
                profile = self._users.get(owner) or {}
                self._event("вход", str(profile.get("email") or "админ"), "вход по токену")
                self._start_session(
                    handler, {"id": owner, "email": profile.get("email", ""), "role": ROLE_ADMIN}
                )
                return
            self._event("вход", "—", "отказ: неверный токен")
            self._send_html(handler, 200, login_page("неверный токен"))
            return

        email, password = field("email"), field("password")
        if not email or not password:
            self._send_html(handler, 200, login_page("введите почту и пароль"))
            return
        try:
            user_id, email, can_edit = self._authenticator(email, password)
        except ServerError:
            self._event("вход", email or "—", "отказ: почта или пароль не подошли")
            self._send_html(handler, 200, login_page("почта или пароль не подошли"))
            return
        if not user_id:
            self._send_html(handler, 200, login_page("сервер не сказал, кто вы"))
            return
        role = ROLE_ADMIN if can_edit else "user"
        self._users.remember(user_id, email, role=role)
        self._event("вход", email, "успешный вход")
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
            self._event("регистрация", email or "—", f"отказ: {exc}")
            self._send_html(handler, 200, signup_page(f"регистрация не прошла: {exc}"))
            return
        self._event("регистрация", email, "аккаунт создан")
        # Новый участник — админ должен знать сразу.
        self._push(
            self._admin_id,
            f"Новый человек на приставке: {email}\n{self._site_link(handler)}/users",
        )
        try:
            user_id, email, can_edit = self._authenticator(email, password)
        except ServerError:
            self._send_html(handler, 200, signup_page("аккаунт создан — войдите паролем"))
            return
        role = ROLE_ADMIN if can_edit else "user"
        self._users.remember(user_id, email, role=role)
        self._start_session(handler, {"id": user_id, "email": email, "role": role})
