"""Веб-интерфейс приставки: страница в браузере поверх того же порта.

Формы без JavaScript: POST → redirect (паттерн post/redirect/get),
поэтому кнопка «назад» в браузере не повторяет действие.

Защита локальной службы:

  * **Host** должен указывать на нас (loopback или адрес, на который
    привязан сервер) — иначе это DNS-rebinding чужого домена;
  * **Origin**, если браузер его прислал, должен совпадать с нашим
    хостом — чужой сайт не может нажать наши кнопки.

Ключи по-прежнему живут только на устройстве: страница их принимает
и показывает лишь «задан/не задан».
"""

from __future__ import annotations

import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Callable

from core.page import catalogue_page, key_page
from providers.client import PocketBase, ServerError
from service import ops
from storage.keys import KeyStore


class WebUi:
    """Маршруты страницы; владелец — HTTP-служба на том же порту.

    ``entries_provider`` — свежий список провайдеров (каталог + флаги),
    ``keys`` — хранилище ключей, ``server`` — залогиненный клиент каталога
    (нужен, чтобы сохранять флаги и отчёты), ``bound_host`` — адрес
    привязки службы для проверки Host.
    """

    def __init__(
        self,
        entries_provider: Callable[[], list[dict]],
        keys: KeyStore,
        server: PocketBase,
        *,
        can_edit: bool = False,
        bound_host: str = "127.0.0.1",
        make_provider=None,
    ) -> None:
        self._entries_provider = entries_provider
        self._keys = keys
        self._server = server
        self._can_edit = can_edit
        self._bound_host = bound_host
        self._make_provider = make_provider

    # ------------------------------------------------------------ защита

    def _refusal(self, handler: BaseHTTPRequestHandler) -> str | None:
        """Причина отказать в обслуживании запроса, если она есть."""
        host = (handler.headers.get("Host") or "").strip().lower()
        hostname = host.split(":")[0].strip("[]")
        allowed = {"127.0.0.1", "localhost", "::1", self._bound_host.strip("[]").lower()}
        if hostname not in allowed:
            return f"неожиданный хост {hostname!r} — это может быть DNS-rebinding"

        origin = (handler.headers.get("Origin") or "").strip()
        if origin and origin != "null":
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
    def _redirect(cls, handler: BaseHTTPRequestHandler, notice: str = "") -> None:
        location = "/" + (f"?notice={urllib.parse.quote(notice)}" if notice else "")
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

    # ------------------------------------------------------------ страницы

    def _notice_from_query(self, path: str) -> str:
        query = urllib.parse.urlsplit(path).query
        return urllib.parse.parse_qs(query).get("notice", [""])[0]

    def handle_get(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        refusal = self._refusal(handler)
        if refusal:
            self._forbidden(handler, refusal)
            return

        clean = urllib.parse.urlsplit(path).path
        if clean in ("/", "/index.html"):
            self._send_html(
                handler,
                200,
                catalogue_page(
                    self._entries_provider(),
                    can_edit=self._can_edit,
                    keyed_ids=frozenset(self._keys.ids()),
                    notice=self._notice_from_query(path),
                    role="вы вошли как администратор" if self._can_edit else "",
                ),
            )
            return

        if clean.startswith("/key/"):
            provider_id = urllib.parse.unquote(clean[len("/key/") :])
            entry = next(
                (item for item in self._entries_provider() if item["id"] == provider_id),
                None,
            )
            if entry is None:
                self._not_found(handler)
                return
            self._send_html(
                handler,
                200,
                key_page(entry["name"], has_key=bool(self._keys.get(provider_id))),
            )
            return

        self._not_found(handler)

    # ------------------------------------------------------------ действия

    def _find(self, provider_id: str) -> dict | None:
        return next(
            (item for item in self._entries_provider() if item["id"] == provider_id),
            None,
        )

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

        if clean.startswith("/toggle/"):
            provider_id = urllib.parse.unquote(clean[len("/toggle/") :])
            entry = self._find(provider_id)
            if entry is None:
                self._not_found(handler)
                return
            result = ops.set_enabled(
                self._server,
                self._entries_provider(),
                [provider_id],
                enabled=not entry.get("enabled"),
                can_edit=self._can_edit,
            )
            word = "включён" if not entry.get("enabled") else "выключен"
            notice = f"{entry['name']}: {word}"
            if result["error"]:
                notice = result["error"]
            self._redirect(handler, notice)
            return

        if clean.startswith("/check/"):
            provider_id = urllib.parse.unquote(clean[len("/check/") :])
            entry = self._find(provider_id)
            if entry is None:
                self._not_found(handler)
                return
            kwargs = {} if self._make_provider is None else {"make_provider": self._make_provider}
            result = ops.check(
                self._server, entry, self._keys.get(provider_id), **kwargs
            )
            self._redirect(handler, self._check_notice(entry, result))
            return

        if clean.startswith("/remove/"):
            provider_id = urllib.parse.unquote(clean[len("/remove/") :])
            entry = self._find(provider_id)
            if entry is None:
                self._not_found(handler)
                return
            result = ops.remove(
                self._server,
                self._entries_provider(),
                [],
                provider_id,
                can_edit=self._can_edit,
            )
            notice = result["note"] and f"{entry['name']}: {result['note']}" or ""
            self._redirect(handler, result["error"] or notice)
            return

        if clean == "/add":
            result = ops.add(
                self._server,
                self._entries_provider(),
                [],
                name=field("name"),
                url=field("url"),
                model=field("model"),
                key=field("api_key"),
                can_edit=self._can_edit,
            )
            if result["entry"] is None:
                self._redirect(handler, result["error"] or "не получилось")
                return
            notice = f"{result['entry']['name']}: {result['note'] or result['error']}"
            self._redirect(handler, notice)
            return

        if clean == "/key":
            provider_id = field("id")
            entry = self._find(provider_id)
            if entry is None:
                self._not_found(handler)
                return
            self._keys.set(provider_id, field("api_key"))
            self._redirect(handler, f"{entry['name']}: ключ сохранён на устройстве")
            return

        self._not_found(handler)

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
