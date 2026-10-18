"""Локальный HTTP-сервис: AI-приставка в формате OpenAI.

Другие приложения настаивают на привычном OpenAI-совместимом адресе и не
знают о провайдерах ничего: они шлют запрос сюда, а приставка сама выбирает
включённого провайдера, подставляет свой ключ и, если провайдер отказал,
пробует следующего по приоритету.

    GET  /v1/models             -> список моделей включённых провайдеров
    POST /v1/chat/completions   -> запрос к провайдеру (с фолбэком)
    GET  /                      -> короткая справка

Слушает по умолчанию 127.0.0.1 — наружу торчать нечему: ключи остаются
на устройстве и уходят только к провайдеру.
"""

from __future__ import annotations

import hmac
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from core.models import union_models
from core.routes import advertised_models, route, should_fallback
from providers.client import NeedsKeyError, Provider, ServerError

#: Пустой ответ на пустой список: клиенты ждут JSON, а не пустоту.
_NO_MODELS = {"object": "list", "data": []}

#: Как часто обновляются live-списки моделей (секунды).
_MODELS_TTL = 600.0

#: Таймаут вытягивания списка моделей у одного провайдера.
_MODELS_FETCH_TIMEOUT = 8.0

#: Вопрос тестового чата: крошечный, но настоящий.
DEFAULT_QUESTION = "Привет! Ответь одним словом: всё ли работает?"


def _hidden_error(payload: bytes) -> str:
    """Скрытая ошибка внутри 200-ответа: пустая строка — ответ здоров.

    Часть шлюзов отвечает HTTP 200, а кладёт в JSON объект ошибки
    ({"error": {...}} или {"type": "error"} без choices) — для прокси
    это отказ, а не чат: такого провайдера надо переключать.
    """
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(data, dict) or "choices" in data:
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        detail = str(error.get("message") or error.get("type") or error)
    elif isinstance(error, str):
        detail = error
    elif data.get("type") == "error" or data.get("object") == "error":
        detail = str(data.get("message") or "провайдер сообщил об ошибке")
    else:
        return ""
    return f"скрытая ошибка в ответе 200: {detail[:200]}"


def test_chat(
    entry: dict,
    key: str,
    *,
    make_provider: Callable[..., Provider] = Provider,
    question: str = DEFAULT_QUESTION,
    timeout: float = 60.0,
) -> dict:
    """Небольшой тестовый чат: настоящий запрос с крошечным лимитом.

    Возвращает {"question", "answer", "error"}: проверяет не только
    список моделей, а что ключ принят и модель отвечает. На платной
    модели это доли цента — на странице об этом сказано честно.
    """
    payload = json.dumps(
        {
            "model": entry.get("model") or "",
            "messages": [{"role": "user", "content": question}],
            # Лимит с запасом: модели с рассуждением (qwen3 и подобные)
            # тратят часть бюджета на «размышления», и при крошечном
            # лимите итог в content обрезается в пустоту.
            "max_tokens": 256,
            "temperature": 0,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    def fail(text: str) -> dict:
        return {"question": question, "answer": "", "error": text}

    if not entry.get("model"):
        return fail("у провайдера не выбрана модель — выберите её кнопкой «модель»")
    if not key:
        return fail("нужен ключ — введите его кнопкой «ключ»")

    try:
        response = make_provider(entry["url"], timeout=timeout).forward_chat(
            payload, api_key=key, timeout=timeout, stream=False
        )
    except NeedsKeyError:
        return fail("нужен ключ — введите его кнопкой «ключ»")
    except ServerError as exc:
        return fail(str(exc))

    if response.status_code >= 400:
        detail = response.read().decode("utf-8", errors="replace")[:300]
        return fail(f"HTTP {response.status_code}: {detail}".strip())

    payload = response.read()
    hidden = _hidden_error(payload)
    if hidden:
        return fail(hidden)

    try:
        data = json.loads(payload.decode("utf-8"))
        message = data["choices"][0].get("message") or {}
        answer = (message.get("content") or "").strip()
        if not answer:
            # Модель с рассуждением израсходовала лимит на «размышления» —
            # покажем их, чем молча выдать пустоту.
            thinking = (
                message.get("reasoning") or message.get("reasoning_content") or ""
            ).strip()
            if thinking:
                answer = f"(модель рассуждала, итог обрезан лимитом) {thinking[:200]}"
    except Exception:  # noqa: BLE001 — чужой формат ответа не должен ронять страницу
        answer = ""
    return {"question": question, "answer": answer or "(пустой ответ)", "error": ""}


#: Как часто приставка переспрашивает реестр доступа о человеке (секунды).
#: Десятки запросов человека в окне = один поход к серверу каталога.
ACCESS_TTL = 300.0

#: Память о недоступном реестре (секунды): не долбить сервер каждый запрос.
ACCESS_DOWN = 60.0


def _error_body(message: str, *, kind: str = "invalid_request_error", code: str | None = None) -> bytes:
    """Ошибка в формате, который понимают OpenAI-клиенты."""
    payload = {"error": {"message": message, "type": kind, "param": None, "code": code}}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _with_model(body: bytes, model: str) -> bytes:
    """Подставляет отроутенную модель в тело запроса.

    Клиент мог попросить «ollama/qwen3» (выбор провайдера) или не назвать
    модель вовсе — провайдеру в обоих случаях говорят по-человечески, что
    грузить. Тело, которое не разобрать как JSON, уходит как есть:
    отклонит уже провайдер, и это честнее, чем молча переписывать.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    if payload.get("model") == model:
        return body
    payload["model"] = model
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Gateway:
    """Всё, что нужно, чтобы ответить на запрос приложения.

    ``entries_provider`` — вызываемое, отдающее свежий список провайдеров
    с переключателями (каталог + снапшот устройства). ``keys`` — хранилище
    ключей; ключ уходит только к провайдеру в его заголовке Authorization.
    ``token`` — токен самой приставки; если задан, все /v1/* маршруты
    требуют его в Authorization: Bearer. ``user_tokens`` + ``context_for_user``
    включают персональный режим: токен принадлежит человеку, и запрос
    обслуживается его переключателями и его ключами. ``provider_factory``
    подменяется в тестах.
    """

    def __init__(
        self,
        entries_provider: Callable[[], list[dict]],
        keys,  # storage.keys.KeyStore
        *,
        timeout: float = 600.0,
        provider_factory: Callable[..., Provider] = Provider,
        token: str = "",
        user_tokens=None,  # storage.usertokens.UserTokens | None
        context_for_user: Callable[[str], tuple[Callable[[], list[dict]], object] | None] | None = None,
        api_gate: Callable[[str], bool] | None = None,
        access_checker: Callable[[str], bool] | None = None,
    ) -> None:
        self._entries_provider = entries_provider
        self._keys = keys
        self._timeout = timeout
        self._provider_factory = provider_factory
        self._token = token
        self._user_tokens = user_tokens
        self._context_for_user = context_for_user
        self._api_gate = api_gate
        self._access_checker = access_checker
        # Реестр доступа: {человек: (время, годен?)} и память о поломке.
        self._access_cache: dict[str, tuple[float, bool]] = {}
        self._access_down_until = 0.0
        # Кэш live-списков моделей: {провайдер: (время, [модели])}.
        self._live_cache: dict[str, tuple[float, list[str]]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------ аутентификация

    @property
    def auth_required(self) -> bool:
        return bool(self._token)

    def check_token(self, authorization_header: str) -> bool:
        """Есть ли в заголовке нужный токен приставки.

        Клиент шлёт Bearer-токен приставки (как в OpenRouter); настоящие
        ключи провайдеров сюда не попадают — приставка подставляет их сама.
        """
        if not self._token:
            return True
        value = authorization_header.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        return hmac.compare_digest(value.encode("utf-8"), self._token.encode("utf-8"))

    def authenticate(self, authorization_header: str) -> tuple[bool, str, str]:
        """(разрешено, id владельца токена, причина отказа). Пустой id — устройство.

        В персональном режиме токен принадлежит человеку: его id уходит
        дальше, и запрос обслуживается его пространством. Тумблер
        «доступ приложений» (api_gate) выключает все токены человека разом.
        """
        value = authorization_header.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()

        if self._user_tokens is not None:
            if not value and not self._token:
                return True, "", ""  # --no-auth: открыто, обслуживается устройство
            owner = self._user_tokens.owner(value)
            if not owner:
                return False, "", ""
            if self._api_gate is not None and not self._api_gate(owner):
                return False, "", "доступ приложений отключён владельцем токена"
            return True, owner, ""

        if not self._token:
            return True, "", ""
        return hmac.compare_digest(value.encode("utf-8"), self._token.encode("utf-8")), "", ""

    def authorize(self, user_id: str) -> tuple[bool, str]:
        """Пропустить ли человека: зарегистрирован и не заблокирован.

        Реестр — сервер каталога; его ответ помнится ACCESS_TTL секунд
        на человека (десятки запросов = один поход), ошибка реестра —
        пауза ACCESS_DOWN, без долба по каждому запросу. Без
        ``access_checker`` (тесты, режим без реестра) пускают всех.
        """
        if not user_id or self._access_checker is None:
            return True, ""
        now = time.monotonic()
        if now < self._access_down_until:
            return False, "доступ не подтверждён: реестр недоступен, попробуйте через минуту"
        cached = self._access_cache.get(user_id)
        if cached and now - cached[0] < ACCESS_TTL:
            return cached[1], (
                "" if cached[1] else "нет доступа: аккаунт не зарегистрирован или заблокирован"
            )
        try:
            ok = bool(self._access_checker(user_id))
        except Exception:  # noqa: BLE001 — любая битость реестра = пауза, а не паника
            self._access_down_until = now + ACCESS_DOWN
            return False, "доступ не подтверждён: реестр недоступен, попробуйте через минуту"
        self._access_cache[user_id] = (now, ok)
        return ok, (
            "" if ok else "нет доступа: аккаунт не зарегистрирован или заблокирован"
        )

    def _context(self, user_id: str) -> tuple[Callable[[], list[dict]], object]:
        """(список провайдеров, ключи) для владельца токена."""
        if user_id and self._context_for_user is not None:
            context = self._context_for_user(user_id)
            if context is not None:
                return context
        return self._entries_provider, self._keys

    # ------------------------------------------------------------ live-модели

    def _refresh_live_models(self, entries: list[dict]) -> None:
        """Обновляет кэш live-списков для всех включённых провайдеров.

        Вызывается из фонового потока; молчащий провайдер просто не
        попадает в кэш — его настроенные модели всё равно в списке.
        """
        now = time.monotonic()
        for entry in entries:
            if not entry.get("enabled"):
                continue
            provider_id = entry["id"]
            with self._cache_lock:
                cached = self._live_cache.get(provider_id)
                if cached and now - cached[0] < _MODELS_TTL:
                    continue
            try:
                client = self._provider_factory(
                    entry["url"], timeout=_MODELS_FETCH_TIMEOUT
                )
                key = self._keys.get(provider_id)
                models = [m["name"] for m in client.fetch_models(key)]
            except Exception:  # noqa: BLE001 — молчащий провайдер не крашит список
                continue
            with self._cache_lock:
                self._live_cache[provider_id] = (now, models)

    def start_refresh_loop(self, interval: float = _MODELS_TTL) -> None:
        """Фоновое обновление live-списков: сразу и потом по интервалу."""

        def loop() -> None:
            while True:
                try:
                    self._refresh_live_models(self._entries_provider())
                except Exception:  # noqa: BLE001 — фон не должен умирать
                    pass
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True).start()

    # ------------------------------------------------------------ решения

    def models_payload(self, user_id: str = "") -> dict:
        """Объединённый список: настроенные (без префикса) + live (с префиксом)."""
        entries_provider, keys = self._context(user_id)
        entries = entries_provider()
        configured = advertised_models(entries)

        # Берём кэш для включённых провайдеров; протухшее обновляем тут же
        # (stale-while-revalidate): сеть не блокирует ответ.
        live: dict[str, list[str]] = {}
        now = time.monotonic()
        stale: list[dict] = []
        with self._cache_lock:
            for entry in entries:
                if not entry.get("enabled"):
                    continue
                cached = self._live_cache.get(entry["id"])
                if cached and now - cached[0] < _MODELS_TTL:
                    live[entry["id"]] = cached[1]
                else:
                    stale.append(entry)

        # Протухшие обновляем синхронно — это быстро (короткий таймаут),
        # и клиент получает полный список с первого раза.
        for entry in stale:
            try:
                client = self._provider_factory(
                    entry["url"], timeout=_MODELS_FETCH_TIMEOUT
                )
                key = keys.get(entry["id"])
                models = [m["name"] for m in client.fetch_models(key)]
                live[entry["id"]] = models
                with self._cache_lock:
                    self._live_cache[entry["id"]] = (now, models)
            except Exception:  # noqa: BLE001 — молчащий провайдер не крашит список
                continue

        models = union_models(
            [{"id": e["id"], "model": e.get("model", "")} for e in entries if e.get("enabled")],
            live,
        )
        return {"object": "list", "data": models} if models else _NO_MODELS

    def chat(self, body: bytes, requested_model: str, user_id: str = ""):
        """Проксирует чат по цепочке маршрутов.

        Возвращает ``(status, content_type, chunks, provider_name, model)`` —
        куски ответа отдаются как есть, в том числе поток SSE; имя
        провайдера и модель уходят клиенту заголовками, чтобы смена
        провайдера была видна. Если никто не справился —
        ``(502, ..., [описание со всеми попытками])``.
        """
        entries_provider, keys = self._context(user_id)
        entries = entries_provider()
        attempts = route(entries, requested_model)
        if not attempts:
            available = ", ".join(model["id"] for model in advertised_models(entries))
            message = f"модель «{requested_model or '—'}» не обслуживается"
            if available:
                message += f"; доступны: {available}"
            else:
                message += "; ни один провайдер не включён"
            return 502, "application/json", [_error_body(message, code="model_not_available")], "", ""

        by_id = {entry["id"]: entry for entry in entries}
        problems: list[str] = []

        for attempt in attempts:
            entry = by_id.get(attempt["providerId"])
            if entry is None:
                continue

            key = keys.get(entry["id"])
            client = self._provider_factory(entry["url"], timeout=self._timeout)
            try:
                response = client.forward_chat(
                    _with_model(body, attempt["model"]),
                    api_key=key,
                    timeout=self._timeout,
                    stream=True,
                )
            except ServerError as exc:
                problems.append(f"{entry['name']}: {exc}")
                continue

            if response.status_code >= 400 and should_fallback(response.status_code):
                # Тело читаем до конца: соединение с отказавшим провайдером
                # надо закрыть, а причина пригодится, если никто не справится.
                detail = response.read().decode("utf-8", errors="replace")[:200]
                problems.append(f"{entry['name']}: HTTP {response.status_code} {detail}".strip())
                response.close()
                continue

            content_type = response.headers.get("content-type", "application/json")

            if response.status_code < 400 and "text/event-stream" not in content_type:
                # Успех (не-поток): тело читаем целиком и проверяем на
                # скрытую ошибку — шлюзы вроде отвечают 200, а кладут
                # {"error":}. Ошибки клиента (4xx без фолбэка) идут как есть.
                payload = response.read()
                response.close()
                hidden = _hidden_error(payload)
                if hidden:
                    problems.append(f"{entry['name']}: {hidden}")
                    continue

                def chunks(body=payload):
                    yield body

                return response.status_code, content_type, chunks(), entry["name"], attempt["model"]

            # Поток начат — ошибку в его середине переключить уже нельзя:
            # клиент начал получать данные. Это честное ограничение прокси.
            def chunks(resp=response):
                try:
                    yield from resp.iter_bytes()
                finally:
                    resp.close()

            return response.status_code, content_type, chunks(), entry["name"], attempt["model"]

        message = "ни один провайдер не ответил"
        if problems:
            # Все попытки с причинами: диагностика в одну строку.
            message += ": " + "; ".join(problems[:6])
        return 502, "application/json", [_error_body(message, kind="api_error", code="upstream_failed")], "", ""


class _Server(ThreadingHTTPServer):
    """HTTP-сервис приставки: несколько потоков и спокойствие в консоли.

    ``socketserver`` зовёт ``handle_error`` на сервере (не на обработчике),
    поэтому подавление шума живёт именно здесь.
    """

    def handle_error(self, request, client_address) -> None:
        """Оборвавшийся клиент — не ошибка.

        Для стриминга это штатно: человек жмёт Esc, приложение закрывает
        вкладку. Traceback в консоль — шум, который пугает больше любой
        поломки. Настоящие ошибки (например, битый запрос) всё равно
        видны по строке доступа и в суперклассе.
        """
        exc_info = sys.exc_info()
        if exc_info and isinstance(exc_info[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            print(f"http: {self._failed_address(client_address)}: клиент оборвал связь", flush=True)
            return
        super().handle_error(request, client_address)

    @staticmethod
    def _failed_address(client_address) -> str:
        return client_address[0] if client_address else "?"


class _Handler(BaseHTTPRequestHandler):
    """Один поток на соединение: ThreadingHTTPServer раздаёт их щедро."""

    protocol_version = "HTTP/1.1"
    server_version = "ai-bridge/1"

    # Ни тела запроса, ни ответа в журнал — там могут быть и длинный
    # контекст, и чужие слова. Видно только кто, куда и с каким итогом.
    # flush=True: службу часто запускают в пайп или фон, и без сброса
    # буфера журнал появляется только при остановке.
    def log_message(self, fmt: str, *args: object) -> None:
        print(f"http: {self.address_string()} {fmt % args}", flush=True)

    @property
    def gateway(self) -> Gateway:
        return self.server.gateway  # type: ignore[attr-defined]

    def _reject_unauthorized(self, reason: str = "", *, status: int = 401) -> None:
        """Отказ в формате, который понимают OpenAI-клиенты.

        401 — «нет токена», 403 — «токен есть, но доступа нет»
        (не зарегистрирован, заблокирован, реестр молчит).
        """
        message = reason or (
            "приставка ждёт свой токен в Authorization: Bearer <токен>; "
            "взять его: страница «приложения и токены»"
        )
        self._send(
            status,
            "application/json",
            [
                _error_body(
                    message,
                    kind="authentication_error" if status == 401 else "permission_error",
                    code="missing_token" if status == 401 else "access_denied",
                )
            ],
        )

    # ------------------------------------------------------------ ответы

    def _send(self, status: int, content_type: str, chunks, *, extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        if "event-stream" not in content_type:
            # Поток SSE идёт до закрытия соединения, остальное — с длиной.
            payload = b"".join(chunks)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Клиент ушёл — это его право, не ошибка.
            pass
        finally:
            self.close_connection = True

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json", [body])

    # ------------------------------------------------------------ методы

    def _try_ui_get(self) -> bool:
        """Отдать GET веб-слою, если он подключён. True — если разобрались."""
        ui = getattr(self.server, "ui", None)
        if ui is None:
            return False
        ui.handle_get(self, self.path)
        return True

    def _try_ui_post(self) -> bool:
        """Отдать POST веб-слою, если он подключён. True — если разобрались."""
        ui = getattr(self.server, "ui", None)
        if ui is None:
            return False
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if 0 < length <= 1024 * 1024 else b""
        ui.handle_post(self, self.path, body)
        return True

    def do_GET(self) -> None:  # noqa: N802 - имя задано http.server
        path = self.path.split("?", 1)[0]
        if path in ("/v1/models", "/models"):
            allowed, user_id, reason = self.gateway.authenticate(self.headers.get("Authorization", ""))
            if not allowed:
                self._reject_unauthorized(reason)
                return
            ok, access_reason = self.gateway.authorize(user_id)
            if not ok:
                self._reject_unauthorized(access_reason, status=403)
                return
            self._send_json(200, self.gateway.models_payload(user_id))
            return
        if path.startswith("/v1/"):
            self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")])
            return
        if self._try_ui_get():
            return
        if path == "/":
            self._send_json(
                200,
                {
                    "name": "ai-bridge",
                    "note": "OpenAI-совместимая приставка: /v1/models, /v1/chat/completions",
                },
            )
            return
        self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")])

    def do_POST(self) -> None:  # noqa: N802 - имя задано http.server
        path = self.path.split("?", 1)[0]
        if path not in ("/v1/chat/completions", "/chat/completions"):
            if path.startswith("/v1/"):
                self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")])
                return
            if self._try_ui_post():
                return
            self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")])
            return

        allowed, user_id, reason = self.gateway.authenticate(self.headers.get("Authorization", ""))
        if not allowed:
            self._reject_unauthorized(reason)
            return
        ok, access_reason = self.gateway.authorize(user_id)
        if not ok:
            self._reject_unauthorized(access_reason, status=403)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 32 * 1024 * 1024:
            self._send(
                400,
                "application/json",
                [_error_body("ожидается тело запроса (Content-Length)", code="bad_body")],
            )
            return

        body = self.rfile.read(length)
        try:
            requested = json.loads(body.decode("utf-8")).get("model", "")
        except (ValueError, AttributeError):
            requested = ""
        status, content_type, chunks, provider_name, used_model = self.gateway.chat(
            body, str(requested), user_id
        )
        if provider_name:
            self.log_message("%s -> %s %s", "chat/completions", provider_name, used_model)
        # Клиент видит, кто реально обслужил запрос: провайдер может
        # смениться (квота, лимит, поломка) — и это не должно быть тайной.
        extra = (
            {"x-ai-bridge-provider": provider_name, "x-ai-bridge-model": used_model}
            if provider_name
            else None
        )
        self._send(status, content_type, chunks, extra_headers=extra)


def serve(
    entries_provider: Callable[[], list[dict]],
    keys,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    timeout: float = 600.0,
    quiet: bool = False,
    ui=None,
    token: str = "",
    user_tokens=None,
    context_for_user=None,
    api_gate=None,
    access_checker=None,
) -> int:
    """Слушает, пока не придёт Ctrl+C. Возвращает код выхода.

    ``ui`` — service.webui.WebUi: страница на том же порту, что и API.
    ``token`` — токен приставки; если задан, /v1/* требует его в заголовке.
    ``user_tokens`` + ``context_for_user`` — персональные токены людей,
    ``api_gate`` — их тумблер «доступ приложений», ``access_checker`` —
    онлайн-проверка реестра (зарегистрирован/заблокирован).
    Без токена служба отдаёт только OpenAI-совместимые маршруты.
    """
    gateway = Gateway(
        entries_provider, keys, timeout=timeout, token=token,
        user_tokens=user_tokens, context_for_user=context_for_user,
        api_gate=api_gate, access_checker=access_checker,
    )
    server = _Server((host, port), _Handler)
    server.gateway = gateway  # type: ignore[attr-defined]
    server.ui = ui  # type: ignore[attr-defined]

    gateway.start_refresh_loop()

    print(f"приставка слушает http://{host}:{port}/v1 (остановить — Ctrl+C)", flush=True)
    if ui is not None:
        print(f"страница: http://{host}:{port}/", flush=True)
    if token:
        print("для /v1/* нужен Authorization: Bearer <токен> (--token покажет)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            print("\nостановлено")
    finally:
        server.server_close()
    return 0
