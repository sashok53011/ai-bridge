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


class PocketBase:
    """Клиент сервера каталога: вход, список провайдеров, статистика.

    Секреты сюда не передаются: токен живёт только в памяти процесса,
    ключи API провайдеров на сервер не уходят вообще.
    """

    def __init__(self, base_url: str, *, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)
        self._token = ""
        self.user_id = ""
        self.user_name = ""

    # ------------------------------------------------------------- сеанс

    def login(self, email: str, password: str) -> None:
        """Вход под обычным пользователем каталога."""
        self._request(
            "POST",
            "/api/collections/users/auth-with-password",
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

    def fetch_catalogue(self) -> tuple[list[dict], str, bool]:
        """Каталог с сервера: (провайдеры, время обновления, canEdit).

        Провайдеры уже приведены к виду, который понимает ядро —
        мусорные записи отброшены.
        """
        body = self._request("GET", "/api/ai-config")
        raw = body.get("providers")
        providers = [entry for entry in (normalize(item) for item in raw or []) if entry]
        return providers, str(body.get("updated") or ""), bool(body.get("canEdit"))

    def save_flags(self, providers: list[dict]) -> list[dict]:
        """Отправляет переключатели и получает каталог обратно.

        Обычному пользователю сервер хранит только включённые записи,
        остальное приходит тем же каталогом.
        """
        body = self._request(
            "PUT",
            "/api/ai-config",
            json={"providers": [{"id": p["id"], "enabled": bool(p.get("enabled"))} for p in providers]},
        )
        raw = body.get("providers")
        return [entry for entry in (normalize(item) for item in raw or []) if entry]

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
            raise ServerError(f"{method} {path} -> HTTP {response.status_code}")

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

    def __init__(self, url: str, *, timeout: float = 20.0) -> None:
        self.openai_base = url.strip().rstrip("/")
        self.root = _root_url(url)
        self._http = httpx.Client(timeout=timeout)

    def fetch_models(self, api_key: str = "") -> list[dict[str, Any]]:
        """Список моделей; для локальных серверов ключ не нужен."""
        from core.models import extract_models

        response = self._http.get(
            f"{self.openai_base}/models",
            headers=_auth_headers(api_key),
        )
        if response.status_code in (401, 403) and not api_key:
            raise ServerError("без ключа провайдер отвечает 401 — введите ключ")
        response.raise_for_status()
        return [{"name": name} for name in extract_models(response.json())]

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


def _auth_headers(api_key: str) -> dict[str, str]:
    # Без ключа заголовок не шлём вовсе: пустой Bearer путает провайдеров.
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _root_url(url: str) -> str:
    """Корень сервера: с адреса снимается хвостовой /v1 (у Ollama его нет)."""
    value = url.strip().rstrip("/")
    if value.endswith("/v1"):
        value = value[: -len("/v1")]
    return value
