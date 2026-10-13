"""Сетевая обвязка: сервер PocketBase и сами провайдеры.

Чистое ядро из ``core`` сюда не переносится — здесь только запросы
и превращение ответов в данные, понятные ядру.
"""

from __future__ import annotations

from typing import Any

import httpx

from core.catalogue import normalize


class ServerError(RuntimeError):
    """Сервер недоступен или ответил отказом."""


class NeedsKeyError(ServerError):
    """Провайдер ждёт ключ.

    401/403 при отсутствующем ключе — не поломка, а просьба ввести ключ;
    поломка — это 401 при уже введённом ключе (обычный ServerError).
    """


class PocketBase:
    """Клиент сервера каталога: вход, список провайдеров, статистика.

    Секреты сюда не передаются: токен живёт только в памяти процесса,
    ключи API провайдеров на сервер не уходят вообще.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)
        self._token = ""
        self.user_id = ""
        self.user_name = ""
        # Запоминается с каталога: суперпользо­вателю флаги не отправляем.
        self._can_edit = False

    # ------------------------------------------------------------- сеанс

    def login(self, email: str, password: str) -> None:
        """Вход в сервер каталога.

        Пробуются обе коллекции: обычные люди живут в ``users``, а
        суперпользо­ватель PocketBase (>= 0.23) — в ``_superusers``,
        и попытка входа админа через ``users`` даёт HTTP 400. Если не
        вышло ни там, ни там — в ошибке остаются обе причины.
        """
        failures = []
        for collection in ("users", "_superusers"):
            try:
                self._auth(email, password, collection=collection)
                return
            except ServerError as exc:
                failures.append(f"{collection}: {exc}")
        raise ServerError(" ; ".join(failures))

    def _auth(self, email: str, password: str, *, collection: str) -> None:
        self._request(
            "POST",
            f"/api/collections/{collection}/auth-with-password",
            json={"identity": email, "password": password},
            auth=False,
        )
        # Ответ уже сохранён в self._remember()

    def _remember(self, body: dict[str, Any]) -> None:
        self._token = str(body.get("token") or "")
        record = body.get("record") or {}
        self.user_id = str(record.get("id") or "")
        self.user_name = str(record.get("name") or record.get("email") or "")

    # ----------------------------------------------------------- каталог

    def fetch_catalogue(self) -> tuple[list[dict], str, bool, dict[str, bool]]:
        """Каталог с сервера: (провайдеры, обновление, canEdit, флаги).

        Флаги — «включён/выключён» глазами сервера для этого пользователя;
        у суперпользо­вателя их нет вовсе (сервер хранит только каталог),
        тогда обычному пользователю они помогают на новом устройстве.
        """
        return self._parse_config(self._request("GET", "/api/ai-config"))

    def save_flags(self, providers: list[dict]) -> tuple[list[dict], str, bool, dict[str, bool]]:
        """Отправляет переключатели вместе со списком и берёт ответ сервера.

        Шлётся **весь** видимый список с полными записями: сервер хранит
        флаги целиком (неотправленное стёрлось бы), а незнакомые записи
        превращаются в предложения этого пользователя. Выжившие записи,
        которых каталог больше не отдаёт, уходят тем же способом — так
        они переживают на сервере и подхватываются с другого устройства.

        Осторожно: для суперпользо­вателя этот PUT заменяет каталог —
        поэтому здесь есть защита, а вызывающий код передаёт
        ``can_edit`` из каталога.
        """
        if getattr(self, "_can_edit", False):
            raise ServerError(
                "отправка переключателей суперпользо­вателем заменила бы каталог — "
                "отменено"
            )

        payload = {
            "version": 2,
            "providers": [_sync_shape(entry) for entry in providers],
        }
        body = self._request("PUT", "/api/ai-config", json=payload)
        return self._parse_config(body)

    def _parse_config(self, body: dict) -> tuple[list[dict], str, bool, dict[str, bool]]:
        raw = body.get("providers")
        providers = [entry for entry in (normalize(item) for item in raw or []) if entry]
        flags = {
            str(item.get("id")): item.get("enabled") is True
            for item in raw or []
            if isinstance(item, dict) and item.get("id")
        }
        can_edit = bool(body.get("canEdit"))
        self._can_edit = can_edit
        return providers, str(body.get("updated") or ""), can_edit, flags

    # -------------------------------------------------------- статистика

    def report_stats(
        self,
        *,
        provider_id: str,
        model: str,
        ok: bool,
        error: str = "",
    ) -> dict[str, Any]:
        """Отчёт о проверке: какая модель, получилось ли, и почему нет."""
        return self._request(
            "POST",
            "/api/ai-stats",
            json={
                "providerId": provider_id,
                "model": model,
                "ok": ok,
                "error": error,
            },
        )

    def fetch_stats(self) -> dict[str, Any]:
        """Свежая статистика по всем провайдерам: популярность и доступность."""
        return self._request("GET", "/api/ai-stats")

    # ------------------------------------------------------------ внутренности

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        auth: bool = True,
    ) -> Any:
        headers = {}
        if auth:
            if not self._token:
                raise ServerError("сначала нужно войти")
            # PocketBase ждёт токен без префикса Bearer.
            headers["Authorization"] = self._token

        try:
            response = self._http.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise ServerError(f"сервер недоступен: {exc}") from exc

        if response.status_code >= 400:
            raise ServerError(_failure(method, path, response.status_code, _json_or_none(response)))

        body = response.json() if response.content else {}
        # Токен приходит вместе с ответом на вход — запоминаем сразу, даже
        # когда сам запрос шёл без авторизации.
        if isinstance(body, dict) and "token" in body:
            self._remember(body)
        return body

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PocketBase":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Provider:
    """Один провайдер: список моделей и загрузка модели в память (Ollama).

    У провайдеров два разных корня: OpenAI-совместимый список моделей лежит
    по ``<адрес>/models`` (обычно это ``.../v1/models``), а нативный API
    Ollama — в корне сервера (``/api/pull``). Поэтому запоминаем оба.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.openai_base = url.strip().rstrip("/")
        self.root = _root_url(url)
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def fetch_models(self, api_key: str = "") -> list[dict[str, Any]]:
        """Список моделей; для локальных серверов ключ не нужен.

        401/403 без ключа — NeedsKeyError (введите ключ, не поломка);
        тот же отказ с ключом — обычный ServerError.
        """
        from core.models import extract_models

        try:
            response = self._http.get(
                f"{self.openai_base}/models",
                headers=_auth_headers(api_key),
            )
        except httpx.HTTPError as exc:
            raise ServerError(f"провайдер недоступен: {exc}") from exc

        if response.status_code in (401, 403):
            if not api_key:
                raise NeedsKeyError("без ключа провайдер отвечает 401 — введите ключ")
            raise ServerError(f"ключ не принят: HTTP {response.status_code}")

        if response.status_code >= 400:
            raise ServerError(f"провайдер ответил HTTP {response.status_code}")

        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise ServerError("провайдер ответил не-JSON на список моделей") from exc

        return [{"name": name} for name in extract_models(payload)]

    def pull_model(self, model: str, *, api_key: str = "", timeout: float = 180.0):
        """Генератор событий прогресса: POST /api/pull (Ollama).

        Большая модель грузится минутами, поэтому поток отдаётся наружу
        по мере поступления, а таймаут кладёт конец попытке.
        """
        from core.progress import ProgressEvent, parse_line, split_lines

        with self._http.stream(
            "POST",
            f"{self.root}/api/pull",
            json={"name": model, "stream": True},
            headers={"Content-Type": "application/json", **_auth_headers(api_key)},
            timeout=timeout,
        ) as response:
            if response.status_code in (404, 405):
                raise ServerError("автоматическая загрузка есть только у Ollama")
            response.raise_for_status()

            buffer = b""
            for chunk in response.iter_bytes():
                lines, buffer = split_lines(buffer + chunk)
                for line in lines:
                    event: ProgressEvent | None = parse_line(line)
                    if event is not None:
                        yield event

    def forward_chat(
        self,
        body: bytes,
        *,
        api_key: str = "",
        timeout: float = 600.0,
        stream: bool = True,
    ) -> httpx.Response:
        """Проксирует POST /chat/completions — тело и поток как есть.

        Это сердце приставки: приложение шлёт запрос сюда, а приставка
        доставляет его провайдеру, подставив свой ключ. Тело не разбирается —
        любые поля клиента (temperature, tools, …) уходят провайдеру
        нетронутыми. Вызывающий обязан закрыть ответ.
        """
        request = self._http.build_request(
            "POST",
            f"{self.openai_base}/chat/completions",
            content=body,
            headers={"Content-Type": "application/json", **_auth_headers(api_key)},
        )
        try:
            return self._http.send(request, stream=stream)
        except httpx.HTTPError as exc:
            raise ServerError(f"провайдер недоступен: {exc}") from exc


def _failure(method: str, path: str, status_code: int, payload: object) -> str:
    """Причина отказа сервера: статус и его собственное сообщение.

    PocketBase в ответах отдаёт ``{"message": "..."}`` — без него диагностика
    сводится к голому «HTTP 400», из-за чего настоящая причина («Failed to
    authenticate», «не найдено») теряется.
    """
    text = f"{method} {path} -> HTTP {status_code}"
    if isinstance(payload, dict):
        message = str(payload.get("message") or "").strip()
        if message:
            text += f": {message}"
    return text


def _json_or_none(response: httpx.Response) -> object:
    """JSON из ответа, если это JSON; промежуточный прокси может вернуть HTML."""
    try:
        return response.json()
    except ValueError:
        return None


def _sync_shape(entry: dict) -> dict:
    """Форма записи для PUT /api/ai-config — только известные серверу поля.

    Ключ сюда не попадает по построению: его в записи нормализованного
    каталога нет вовсе, а своё поле добавлять нельзя — сервер бы его всё
    равно отбросил sanitize(), но слать секрет зря не стоит.
    """
    return {
        "id": entry.get("id", ""),
        "name": entry.get("name", ""),
        "enabled": bool(entry.get("enabled")),
        "url": entry.get("url", ""),
        "model": entry.get("model", ""),
        "fallbackPriority": int(entry.get("fallbackPriority") or 0),
        "loginUrl": entry.get("loginUrl", ""),
        "getApiKeyUrl": entry.get("getApiKeyUrl", ""),
        "logoUrl": entry.get("logoUrl", ""),
        "logoAsset": entry.get("logoAsset", ""),
        "status": entry.get("status", "active"),
        "origin": entry.get("origin", "admin"),
        "authorName": entry.get("authorName", ""),
    }


def _auth_headers(api_key: str) -> dict[str, str]:
    # Без ключа заголовок не шлём вовсе: пустой Bearer путает провайдеров.
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _root_url(url: str) -> str:
    """Корень сервера: с адреса снимается хвостовой /v1 (у Ollama его нет)."""
    value = url.strip().rstrip("/")
    if value.endswith("/v1"):
        value = value[: -len("/v1")]
    return value
