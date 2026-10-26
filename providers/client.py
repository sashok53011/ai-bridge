"""Сетевая обвязка: сервер PocketBase и сами провайдеры.

Чистое ядро из ``core`` сюда не переносится — здесь только запросы
и превращение ответов в данные, понятные ядру.
"""

from __future__ import annotations

import threading
from typing import Any
from urllib.parse import quote

import httpx

from core.catalogue import normalize
from providers import copilot


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
        # Токен PocketBase живёт не вечно: со временем сервер отвечает
        # HTTP 401 «Missing or invalid authentication». Логин и пароль
        # остаются в памяти процесса (сюда они и пришли из окружения) —
        # чтобы перевести сеанс, не нужен рестарт службы.
        self._email = ""
        self._password = ""
        self._login_lock = threading.Lock()

    # ------------------------------------------------------------- сеанс

    def login(self, email: str, password: str) -> None:
        """Вход в сервер каталога.

        Пробуются обе коллекции: обычные люди живут в ``users``, а
        суперпользо­ватель PocketBase (>= 0.23) — в ``_superusers``,
        и попытка входа админа через ``users`` даёт HTTP 400. Если не
        вышло ни там, ни там — в ошибке остаются обе причины.

        Учётные данные запоминаются для самовосстановления: токен
        PocketBase протухает (сервер отвечает 401), и тогда запрос
        сам входит заново — без этого приставка умирает до рестарта.
        """
        self._email = email
        self._password = password
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

    def signup(self, email: str, password: str) -> None:
        """Регистрация нового человека (открытая, без подтверждения почты).

        Сервер принимает её, только когда в настройках коллекции ``users``
        разрешена регистрация; иначе — понятный отказ сервера.
        """
        self._request(
            "POST",
            "/api/collections/users/records",
            json={
                "email": email,
                "password": password,
                "passwordConfirm": password,
            },
            auth=False,
        )

    def delete_user(self, user_id: str) -> None:
        """Удаляет аккаунт человека на сервере — модерация админом."""
        self._request("DELETE", f"/api/collections/users/records/{user_id}")

    # -------------------------------------------------------- реестр доступа

    def access_state(self, user_id: str) -> str:
        """Состояние человека в реестре: 'ok', 'missing' или 'blocked'.

        Приставка сверяется с сервером каталога перед каждым запросом —
        онлайн-проверка «зарегистрирован и не заблокирован». Суперпользо­ватель
        живёт в отдельной коллекции `_superusers`: не найден в `users` —
        проверяем там, он зарегистрирован по определению. Сервер молчит
        или отваливается — ServerError: вызывающий закрывается, а не гадает.
        """
        try:
            body = self._request("GET", f"/api/collections/users/records/{user_id}")
        except ServerError as exc:
            if "HTTP 404" not in str(exc):
                raise
            try:
                self._request("GET", f"/api/collections/_superusers/records/{user_id}")
            except ServerError as inner:
                if "HTTP 404" in str(inner):
                    return "missing"
                raise
            return "ok"
        return "blocked" if (body or {}).get("blocked") else "ok"

    def set_blocked(self, user_id: str, blocked: bool) -> None:
        """Блокировка аккаунта: обратимая, в отличие от удаления."""
        self._request(
            "PATCH", f"/api/collections/users/records/{user_id}", json={"blocked": bool(blocked)}
        )

    def people(self) -> list[dict]:
        """Все аккаунты людей: [{id, email, blocked}] — для страницы админа."""
        body = self._request("GET", "/api/collections/users/records") or {}
        items = body.get("items") or []
        return [
            {
                "id": str(item.get("id") or ""),
                "email": str(item.get("email") or ""),
                "blocked": bool(item.get("blocked")),
            }
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]

    def ensure_blocked_field(self) -> bool:
        """Поле blocked в коллекции людей: создаёт при первом запуске.

        Возвращает True, если поле создалось; если сервер отказал —
        проверка «заблокирован» просто не сработает (деградация, не падение).
        """
        try:
            collection = self._request("GET", "/api/collections/users") or {}
        except ServerError:
            return False
        fields = collection.get("fields") or []
        if any(isinstance(field, dict) and field.get("name") == "blocked" for field in fields):
            return False
        fields.append({"name": "blocked", "type": "bool", "required": False})
        payload = {
            key: collection.get(key)
            for key in ("name", "type", "fields", "indexes")
            if key in collection
        }
        try:
            self._request("PUT", "/api/collections/users", json=payload)
        except ServerError:
            return False
        return True

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

    def push_catalogue(self, providers: list[dict]) -> tuple[list[dict], str, bool, dict[str, bool]]:
        """Отправляет каталог целиком — только для суперпользо­вателя.

        Это тот же PUT, которым пользуется веб-панель администратора.
        Осторожно: **чего нет в списке, уходит в корзину** (восстановимо).
        Поэтому сюда отправляют видимый каталог целиком плюс новую
        запись, а не одно изменение.
        """
        if not getattr(self, "_can_edit", False):
            raise ServerError("каталог целиком меняет только суперпользо­ватель")

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
        for_user: str = "",
    ) -> dict[str, Any]:
        """Отчёт о проверке: какая модель, получилось ли, и почему нет.

        ``for_user`` — записать успех от имени этого человека (сервер
        доверяет суперпользо­вателю приставки); пусто — от себя.
        """
        payload: dict[str, Any] = {
            "providerId": provider_id,
            "model": model,
            "ok": ok,
            "error": error,
        }
        if for_user:
            payload["userId"] = for_user
        return self._request("POST", "/api/ai-stats", json=payload)

    def fetch_stats(self) -> dict[str, Any]:
        """Свежая статистика по всем провайдерам: популярность и доступность."""
        return self._request("GET", "/api/ai-stats")

    # ------------------------------------------------------------ внутренности

    def _relogin(self, *, stale: str) -> None:
        """Войти заново после протухшего токена.

        Под замком: потоки, долбившие реестр одновременно, не ломятся
        с паролем все разом — входит первый, остальные видят свежий
        токен и просто продолжают. Если перелогин невозможен (нет
        сохранённых учётных данных) — честный ServerError.
        """
        with self._login_lock:
            if self._token and self._token != stale:
                return  # сосед уже перевёл сеанс
            if not self._email or not self._password:
                raise ServerError(
                    "токен каталога истёк, а логин не запомнен — перезапустите приставку"
                )
            self.login(self._email, self._password)

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

        if response.status_code == 401 and auth:
            # Токен PocketBase протух — без перевода сеанса приставка
            # умирала бы до рестарта (в журнале сплошняком «HTTP 401:
            # Missing or invalid authentication»). Вход заново и один
            # повтор; токен устаревший — уже не пригодится.
            self._relogin(stale=headers["Authorization"])
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

    def _headers_for(self, api_key: str) -> dict[str, str]:
        """Заголовки авторизации: у Copilot токен обменивается на токен API.

        Copilot принимает не сырой GitHub-токен, а короткоживущий токен
        обмена (иначе 401); обмен кэшируется в providers/copilot. Остальным
        провайдерам — обычный Bearer, как и раньше.
        """
        if api_key and copilot.is_copilot_url(self.openai_base):
            try:
                return copilot.auth_headers(api_key, client=self._http)
            except copilot.CopilotError as exc:
                raise ServerError(str(exc)) from exc
        return _auth_headers(api_key)

    def _models_payload(self, api_key: str) -> Any:
        """GET /models с общими правилами ошибок.

        401/403 без ключа — NeedsKeyError (введите ключ, не поломка);
        тот же отказ с ключом — обычный ServerError.
        """
        try:
            response = self._http.get(
                f"{self.openai_base}/models",
                headers=self._headers_for(api_key),
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
            return response.json()
        except ValueError as exc:
            raise ServerError("провайдер ответил не-JSON на список моделей") from exc

    def fetch_models(self, api_key: str = "") -> list[dict[str, Any]]:
        """Список моделей; для локальных серверов ключ не нужен."""
        from core.models import extract_models

        payload = self._models_payload(api_key)

        # У Copilot каталог /models шире тарифа: фильтр по utility-семействам
        # (sku уже известен — _headers_for выше обменял токен).
        if copilot.is_copilot_url(self.openai_base):
            return [{"name": name} for name in copilot.usable_models(payload)]
        return [{"name": name} for name in extract_models(payload)]

    def fetch_model_meta(self, api_key: str = "") -> dict[str, dict[str, Any]]:
        """Метаданные каталога: {имя: {category, endpoints, paid}}.

        Нужны автотесту: генерация картинок и звука проверяется своими
        моделями — тип читается из ``category``/``supported_endpoints``.
        У провайдеров без такой информации (обычный OpenAI-список) —
        пустой словарь; запасной выбор по именам живёт в гейтвее.
        """
        payload = self._models_payload(api_key)
        out: dict[str, dict[str, Any]] = {}
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return out
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("id") or item.get("name") or "").strip()
            category = str(item.get("category") or "").strip().lower()
            raw_endpoints = item.get("supported_endpoints")
            endpoints = (
                [str(value) for value in raw_endpoints if isinstance(value, str)]
                if isinstance(raw_endpoints, list) else []
            )
            if name and (category or endpoints):
                out[name] = {
                    "category": category,
                    "endpoints": endpoints,
                    "paid": bool(item.get("paid_only")),
                }
        return out

    def fetch_audio_catalogue(self, api_key: str = "") -> dict[str, list[str]]:
        """Голоса аудиомоделей: ``{имя модели: [голоса]}`` из /audio/models.

        У Pollinations ответ — JSON внутри строки; модели без voices в
        результат не попадают. Нужно полю «голос» — как подсказка при
        вводе (свободный ввод остаётся), поэтому любой отказ каталога —
        просто пустой словарь: форма обязана работать и без подсказок.
        """
        try:
            response = self._http.get(
                f"{self.root}/audio/models",
                headers=self._headers_for(api_key),
            )
        except httpx.HTTPError:
            return {}
        if response.status_code >= 400:
            return {}
        try:
            payload: Any = response.json()
        except ValueError:
            return {}
        if isinstance(payload, str):  # Pollinations: JSON-строка в строке
            import json as _json

            try:
                payload = _json.loads(payload)
            except ValueError:
                return {}
        out: dict[str, list[str]] = {}
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                voices = item.get("voices")
                if name and isinstance(voices, list):
                    clean = [str(v).strip() for v in voices if str(v).strip()]
                    if clean:
                        out[name] = clean
        return out

    def fetch_json(self, path: str, *, api_key: str = "") -> tuple[int, Any]:
        """GET <корень><path> → ``(статрос, JSON)`` — опрос заданий jobs."""
        try:
            response = self._http.get(
                f"{self.root}{path}", headers=self._headers_for(api_key),
            )
        except httpx.HTTPError as exc:
            raise ServerError(f"провайдер недоступен: {exc}") from exc
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, {}

    def local_status(self) -> str:
        """Что происходит сейчас в локальном сервере — строка для каталога.

        Ollama: ``GET /api/ps`` — модели в памяти и доля VRAM (это
        мгновенный запрос по оперативке, ничего не грузится). LM Studio
        и прочие: пробуем ``/api/v0/loaded`` и ``/api/v0/health`` с
        полем loaded_model. Таймаут короткий: мёртвый или висящий
        сервер не должен держать страницу. Ничего непонятного — пустая
        строка: лучше промолчать, чем соврать.
        """
        for path in ("/api/ps", "/api/v0/loaded", "/api/v0/health"):
            try:
                response = self._http.get(f"{self.root}{path}")
            except httpx.HTTPError:
                return ""  # сервер не отвечает — дальше незачем
            if response.status_code != 200:
                continue
            try:
                payload: Any = response.json()
            except ValueError:
                continue
            line = _format_local_status(payload, ollama=(path == "/api/ps"))
            if line:
                return line
        return ""

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

    def forward_path(
        self,
        path: str,
        body: bytes,
        *,
        api_key: str = "",
        timeout: float = 60.0,
        stream: bool = True,
    ) -> httpx.Response:
        """POST <база><path> — чат, картинки, звук, векторы: тело как есть.

        Тело не разбирается — любые поля уходят провайдеру нетронутыми.
        Вызывающий обязан закрыть ответ.
        """
        request = self._http.build_request(
            "POST",
            f"{self.openai_base}{path}",
            content=body,
            headers={"Content-Type": "application/json", **self._headers_for(api_key)},
        )
        try:
            return self._http.send(request, stream=stream)
        except httpx.HTTPError as exc:
            raise ServerError(f"провайдер недоступен: {exc}") from exc

    def forward_chat(
        self,
        body: bytes,
        *,
        api_key: str = "",
        timeout: float = 600.0,
        stream: bool = True,
    ) -> httpx.Response:
        """Проксирует POST /chat/completions — сердце приставки.

        Приложение шлёт запрос сюда, а приставка доставляет его провайдеру,
        подставив свой ключ. Вызывающий обязан закрыть ответ.
        """
        return self.forward_path(
            "/chat/completions", body,
            api_key=api_key, timeout=timeout, stream=stream,
        )

    def forward_form(
        self,
        path: str,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        *,
        api_key: str = "",
        from_root: bool = False,
        stream: bool = False,
    ) -> httpx.Response:
        """Multipart-POST (звук): поля + файл, тело как есть.

        ``from_root`` — эндпоинт от корня сервера (``/alpha/…`` живёт не
        под ``/v1``), без него путь считается от OpenAI-базы.
        """
        base = self.root if from_root else self.openai_base
        request = self._http.build_request(
            "POST",
            f"{base}{path}",
            data=data,
            files=files,
            headers=self._headers_for(api_key),
        )
        try:
            return self._http.send(request, stream=stream)
        except httpx.HTTPError as exc:
            raise ServerError(f"провайдер недоступен: {exc}") from exc

    def media_get(
        self,
        kind: str,
        prompt: str,
        model: str,
        *,
        api_key: str = "",
        params: dict[str, str] | None = None,
        prefer_async: bool = False,
        stream: bool = True,
    ) -> httpx.Response:
        """GET <корень>/<kind>/<промпт>?… — видео, 3D, озвучка.

        Медиа-файлы большие, поэтому ответ остаётся потоком: вызывающий
        читает заголовки (в них публичная ссылка `Link: … rel="enclosure"`)
        и, если ссылки нет, первый кусок — тело целиком не тянем.
        ``params`` — доп. query-параметры (голос, картинка-источник,
        разрешение); пустые значения не мусорят в адресе.
        ``prefer_async`` — не удерживать соединение: провайдер отвечает
        202 с заданием (jobs); без jobs заголовок игнорируется, ответ
        обычный.
        """
        url = f"{self.root}/{kind}/{quote(prompt, safe='')}"
        query: dict[str, str] = {}
        if model:
            query["model"] = model
        for name, value in (params or {}).items():
            if value:
                query[str(name)] = str(value)
        headers = self._headers_for(api_key)
        if prefer_async:
            headers = {**headers, "Prefer": "respond-async"}
        request = self._http.build_request(
            "GET",
            url,
            params=query or None,
            headers=headers,
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
        # Справочная информация о модели (цена, контекст, способности) —
        # сервер хранит только после правки своего sanitize().
        "pricing": entry.get("pricing", ""),
        "context": entry.get("context", ""),
        "capabilities": entry.get("capabilities", ""),
        "infoUrl": entry.get("infoUrl", ""),
    }


def _auth_headers(api_key: str) -> dict[str, str]:
    # Без ключа заголовок не шлём вовсе: пустой Bearer путает провайдеров.
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _format_local_status(payload: object, *, ollama: bool) -> str:
    """Строка состояния локального сервера из его JSON; непонятное — "".

    Ollama (``/api/ps``): ``{"models": [{"name", "size", "size_vram"}]}``
    — имена в памяти и доля VRAM (GPU%). Пустой список — «в памяти
    пусто». LM Studio и прочие: ищем имя загруженной модели в полях
    loaded_model/model (в корне и в data). Ни того ни другого — молчим:
    лучше пустая строка, чем выдумка.
    """
    if not isinstance(payload, dict):
        return ""
    if ollama and isinstance(payload.get("models"), list):
        models = [item for item in payload["models"] if isinstance(item, dict)]
        if not models:
            return "в памяти пусто"
        parts = []
        for item in models:
            name = str(item.get("name") or item.get("model") or "?")
            size = int(item.get("size") or 0)
            vram = int(item.get("size_vram") or 0)
            gpu = f" · GPU {round(vram * 100 / size)}%" if size and vram else ""
            parts.append(f"{name}{gpu}")
        return "загружено: " + ", ".join(parts)
    for key in ("loaded_model", "model"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return f"загружено: {value}"
        data = payload.get("data")
        if isinstance(data, dict):
            value = data.get(key)
            if isinstance(value, str) and value:
                return f"загружено: {value}"
    return ""


def _root_url(url: str) -> str:
    """Корень сервера: с адреса снимается хвостовой /v1 (у Ollama его нет)."""
    value = url.strip().rstrip("/")
    if value.endswith("/v1"):
        value = value[: -len("/v1")]
    return value
