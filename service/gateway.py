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

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from core.routes import advertised_models, route, should_fallback
from providers.client import Provider, ServerError

#: Пустой ответ на пустой список: клиенты ждут JSON, а не пустоту.
_NO_MODELS = {"object": "list", "data": []}


def _error_body(message: str, *, kind: str = "invalid_request_error", code: str | None = None) -> bytes:
    """Ошибка в формате, который понимают OpenAI-клиенты."""
    payload = {"error": {"message": message, "type": kind, "param": None, "code": code}}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Gateway:
    """Всё, что нужно, чтобы ответить на запрос приложения.

    ``entries_provider`` — вызываемое, отдающее свежий список провайдеров
    с переключателями (каталог + снапшот устройства). ``keys`` — хранилище
    ключей; ключ уходит только к провайдеру в его заголовке Authorization.
    ``provider_factory`` подменяется в тестах.
    """

    def __init__(
        self,
        entries_provider: Callable[[], list[dict]],
        keys,  # storage.keys.KeyStore
        *,
        timeout: float = 600.0,
        provider_factory: Callable[..., Provider] = Provider,
    ) -> None:
        self._entries_provider = entries_provider
        self._keys = keys
        self._timeout = timeout
        self._provider_factory = provider_factory

    # ------------------------------------------------------------ решения

    def models_payload(self) -> dict:
        models = advertised_models(self._entries_provider())
        return {"object": "list", "data": models} if models else _NO_MODELS

    def chat(self, body: bytes, requested_model: str):
        """Проксирует чат по цепочке маршрутов.

        Возвращает ``(status, content_type, chunks, provider_name)`` —
        куски ответа отдаются как есть, в том числе поток SSE. Если никто
        не справился — ``(502, ..., [описание])``.
        """
        entries = self._entries_provider()
        attempts = route(entries, requested_model)
        if not attempts:
            available = ", ".join(model["id"] for model in advertised_models(entries))
            message = f"модель «{requested_model or '—'}» не обслуживается"
            if available:
                message += f"; доступны: {available}"
            else:
                message += "; ни один провайдер не включён"
            return 502, "application/json", [_error_body(message, code="model_not_available")], ""

        by_id = {entry["id"]: entry for entry in entries}
        last_problem = ""

        for attempt in attempts:
            entry = by_id.get(attempt["providerId"])
            if entry is None:
                continue

            key = self._keys.get(entry["id"])
            client = self._provider_factory(entry["url"], timeout=self._timeout)
            try:
                response = client.forward_chat(
                    body, api_key=key, timeout=self._timeout, stream=True
                )
            except ServerError as exc:
                last_problem = f"{entry['name']}: {exc}"
                continue

            if response.status_code >= 400 and should_fallback(response.status_code):
                # Тело читаем до конца: соединение с отказавшим провайдером
                # надо закрыть, а причина пригодится, если никто не справится.
                detail = response.read().decode("utf-8", errors="replace")[:300]
                last_problem = f"{entry['name']}: HTTP {response.status_code} {detail}".strip()
                response.close()
                continue

            content_type = response.headers.get("content-type", "application/json")

            def chunks(resp=response):
                try:
                    yield from resp.iter_bytes()
                finally:
                    resp.close()

            return response.status_code, content_type, chunks(), entry["name"]

        message = "ни один провайдер не ответил"
        if last_problem:
            message += f": {last_problem}"
        return 502, "application/json", [_error_body(message, kind="api_error", code="upstream_failed")], ""


class _Handler(BaseHTTPRequestHandler):
    """Один поток на соединение: ThreadingHTTPServer раздаёт их щедро."""

    protocol_version = "HTTP/1.1"
    server_version = "ai-bridge/1"

    # Ни тела запроса, ни ответа в журнал — там могут быть и длинный
    # контекст, и чужие слова. Видно только кто, куда и с каким итогом.
    def log_message(self, fmt: str, *args: object) -> None:
        print(f"http: {self.address_string()} {fmt % args}")

    @property
    def gateway(self) -> Gateway:
        return self.server.gateway  # type: ignore[attr-defined]

    # ------------------------------------------------------------ ответы

    def _send(self, status: int, content_type: str, chunks) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
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

    def do_GET(self) -> None:  # noqa: N802 - имя задано http.server
        path = self.path.split("?", 1)[0]
        if path in ("/v1/models", "/models"):
            self._send_json(200, self.gateway.models_payload())
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
            self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")])
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
        status, content_type, chunks, provider_name = self.gateway.chat(body, str(requested))
        if provider_name:
            self.log_message("%s -> %s", "chat/completions", provider_name)
        self._send(status, content_type, chunks)


def serve(
    entries_provider: Callable[[], list[dict]],
    keys,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    timeout: float = 600.0,
    quiet: bool = False,
) -> int:
    """Слушает, пока не придёт Ctrl+C. Возвращает код выхода."""
    gateway = Gateway(entries_provider, keys, timeout=timeout)
    server = ThreadingHTTPServer((host, port), _Handler)
    server.gateway = gateway  # type: ignore[attr-defined]

    print(f"приставка слушает http://{host}:{port}/v1 (остановить — Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            print("\nостановлено")
    finally:
        server.server_close()
    return 0
