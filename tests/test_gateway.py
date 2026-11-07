"""Локальный сервис: модели, чат, фолбэк между провайдерами.

Тесты поднимают настоящий HTTP-сервер на свободном порту; провайдер
подменяется на httpx.MockTransport, чтобы сеть не участвовала.
"""

import json
import threading
from pathlib import Path

import httpx
import pytest

from providers.client import Provider
from service.gateway import Gateway, _Handler, _Server, _hidden_error, _needs_of
from service.gateway import test_chat as run_test_chat  # имя «test_*» путает pytest
from storage.keys import KeyStore


def _entry(**kwargs) -> dict:
    base = {
        "id": "ollama",
        "name": "Ollama",
        "url": "http://ollama.test/v1",
        "model": "llama3.2",
        "fallbackPriority": 6,
        "enabled": True,
    }
    base.update(kwargs)
    return base


class _Harness:
    """Сервис на эфемерном порту + журнал обращений к провайдерам."""

    def __init__(self, directory: Path, entries: list[dict], handlers: dict, keys: dict | None = None,
                 lists=None):
        self.seen: list[httpx.Request] = []
        provider_keys = KeyStore(directory)
        for provider_id, value in (keys or {}).items():
            provider_keys.set(provider_id, value)

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            inner = handlers.get(base)

            def handler(request: httpx.Request) -> httpx.Response:
                self.seen.append(request)
                return inner(request)

            return Provider(url, transport=httpx.MockTransport(handler), **kwargs)

        self.gateway = Gateway(lambda: entries, provider_keys, provider_factory=factory,
                               lists=lists)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.gateway = self.gateway
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def harness(tmp_path):
    """Собирает сервис на тестовых данных; закрывает за собой."""
    holders: list[_Harness] = []

    def build(entries, handlers, keys=None, lists=None) -> _Harness:
        h = _Harness(tmp_path, entries, handlers, keys, lists=lists)
        holders.append(h)
        return h

    yield build
    for h in holders:
        h.close()


class TestModelsEndpoint:
    def test_lists_code_words(self, harness, tmp_path) -> None:
        from storage.lists import ModelLists

        lists = ModelLists(tmp_path)
        lists.code_write("vision_tools_1m", "ollama/llama3.2\n")
        h = harness([_entry()], {}, lists=lists)
        with httpx.Client(base_url=h.base) as client:
            data = client.get("/v1/models").json()
        assert data["object"] == "list"
        assert data["data"] == [{"id": "vision_tools_1m", "owned_by": "list"}]

    def test_no_lists_means_empty(self, harness) -> None:
        h = harness([_entry()], {})
        with httpx.Client(base_url=h.base) as client:
            data = client.get("/v1/models").json()
        assert data["data"] == []

    def test_a_switched_off_provider_is_not_offered(self, harness) -> None:
        h = harness([_entry(enabled=False)], {})
        with httpx.Client(base_url=h.base) as client:
            data = client.get("/v1/models").json()
        assert data["data"] == []

    def test_an_unknown_path_is_a_json_error(self, harness) -> None:
        h = harness([_entry()], {})
        with httpx.Client(base_url=h.base) as client:
            response = client.get("/v1/nope")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "unknown_path"


class TestChatEndpoint:
    @staticmethod
    def _ok(body: bytes = b'{"choices": []}'):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})

        return handler

    def test_a_chat_goes_to_the_right_provider_with_its_key(self, harness) -> None:
        h = harness(
            [_entry(), _entry(id="openai", name="OpenAI", url="https://openai.test/v1", model="gpt-4o-mini", fallbackPriority=1)],
            {"http://ollama.test": self._ok(), "https://openai.test": self._ok()},
            keys={"openai": "sk-test"},
        )
        payload = json.dumps({"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "привет"}]})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload, headers={"content-type": "application/json"})

        assert response.status_code == 200
        assert len(h.seen) == 1
        sent = h.seen[0]
        assert sent.url.host == "openai.test"
        # Ключ подставлен и уходит только этому провайдеру.
        assert sent.headers["authorization"] == "Bearer sk-test"
        # Тело дошло нетронутым — любые поля клиента важны.
        assert json.loads(sent.content) == {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "привет"}]}

    def test_the_provider_gets_the_resolved_model_not_the_alias(self, harness) -> None:
        """«ollama/qwen3:0.6b» — это просьба к ollama; сама модель в теле
        должна стать чистой, иначе провайдер её не найдёт."""
        h = harness([_entry()], {"http://ollama.test": self._ok()})
        payload = json.dumps({"model": "ollama/qwen3:0.6b", "messages": []})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        sent = json.loads(h.seen[0].content)
        assert sent["model"] == "qwen3:0.6b"
        # Остальные поля клиента не тронуты.
        assert sent["messages"] == []

    def test_an_unnamed_model_gets_the_provider_default(self, harness) -> None:
        """Клиент не назвал модель — провайдеру говорят, какую подставить."""
        h = harness([_entry()], {"http://ollama.test": self._ok()})
        payload = json.dumps({"messages": [{"role": "user", "content": "привет"}]})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        sent = json.loads(h.seen[0].content)
        assert sent["model"] == "llama3.2"
        assert sent["messages"] == [{"role": "user", "content": "привет"}]

    def test_a_body_that_is_not_json_reaches_the_provider_as_is(self, harness) -> None:
        """Разобрать нечего — честная пересылка; отклонит уже провайдер."""
        h = harness([_entry()], {"http://ollama.test": self._ok()})
        with httpx.Client(base_url=h.base) as client:
            response = client.post(
                "/v1/chat/completions",
                content="{просто текст".encode("utf-8"),
                headers={"content-type": "application/json"},
            )

        assert response.status_code == 200
        assert h.seen[0].content == "{просто текст".encode("utf-8")

    def test_a_failing_provider_falls_back_to_the_next(self, harness) -> None:
        """Клиент попросил llama3.2 — уступает только тот, кто её и обслуживает."""
        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b'{"error": "busy"}')

        h = harness(
            [
                _entry(fallbackPriority=1),
                _entry(id="b", name="B", url="https://b.test/v1", fallbackPriority=2),
            ],
            {"http://ollama.test": broken, "https://b.test": self._ok(b'{"choices": [1]}')},
        )
        payload = json.dumps({"model": "llama3.2"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        assert response.json() == {"choices": [1]}
        # Оба провайдера получили запрос: сначала отказавший, потом успех.
        assert [request.url.host for request in h.seen] == ["ollama.test", "b.test"]

    def test_a_failed_model_falls_back_to_the_next_provider(self, harness) -> None:
        """Модель отказалась у своего провайдера — приставка продолжает
        следующей включённой: пустой ответ хуже чужого."""
        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b'{"error": "busy"}')

        h = harness(
            [
                _entry(fallbackPriority=1),
                _entry(id="b", name="B", url="https://b.test/v1", model="m-b", fallbackPriority=2),
            ],
            {"http://ollama.test": broken, "https://b.test": self._ok()},
        )
        payload = json.dumps({"model": "llama3.2"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        # Оба провайдера получили запрос: отказавший, потом подхвативший.
        assert [request.url.host for request in h.seen] == ["ollama.test", "b.test"]
        # Кто в итоге ответил — видно в заголовках, подмена не тайна.
        assert response.headers["x-ai-bridge-provider"] == "B"
        assert response.headers["x-ai-bridge-model"] == "m-b"

    def test_a_402_balance_error_falls_back_like_any_outage(self, harness) -> None:
        """Пустой баланс (402) у единственного владельца модели — не тупик:
        следующий провайдер отвечает своей моделью."""
        def broke(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                402, content='{"error": {"message": "купите кредиты"}}'.encode("utf-8")
            )

        h = harness(
            [
                _entry(fallbackPriority=1),
                _entry(id="local", name="Local", url="http://local.test/v1", model="qwen3", fallbackPriority=9),
            ],
            {"http://ollama.test": broke, "http://local.test": self._ok()},
        )
        payload = json.dumps({"model": "llama3.2"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-model"] == "qwen3"

    def test_a_404_model_error_switches_too(self, harness) -> None:
        """Провайдер не знает модель (404) — смена безвредна."""
        def unknown(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, content=b'{"error": {"message": "model not found"}}')

        h = harness(
            [
                _entry(fallbackPriority=1),
                _entry(id="b", name="B", url="https://b.test/v1", model="llama3.2", fallbackPriority=2),
            ],
            {"http://ollama.test": unknown, "https://b.test": self._ok()},
        )
        payload = json.dumps({"model": "llama3.2"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-provider"] == "B"

    def test_a_bad_request_is_not_retried(self, harness) -> None:
        def rejected(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, content=b'{"error": "missing messages"}')

        h = harness(
            [_entry(), _entry(id="b", name="B", url="https://b.test/v1", model="m-b", fallbackPriority=2)],
            {"http://ollama.test": rejected, "https://b.test": self._ok()},
        )
        payload = json.dumps({"model": "llama3.2"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        # 422 — вина клиента: смена провайдера не поможет, ошибка уходит как есть.
        assert response.status_code == 422
        assert [request.url.host for request in h.seen] == ["ollama.test"]

    def test_an_unknown_model_falls_back_instead_of_refusing(self, harness) -> None:
        """Неизвестная модель — не отказ, а последний шанс: приставка
        пробует включённые по приоритету."""
        h = harness([_entry()], {"http://ollama.test": self._ok()})
        payload = json.dumps({"model": "gpt-9-turbo"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-model"] == "llama3.2"

    def test_nothing_configured_names_what_is_available(self, harness) -> None:
        """Провайдер включён, но модели не настроены — честный отказ
        со списком доступного."""
        h = harness([_entry(model="")], {})
        payload = json.dumps({"model": "gpt-9-turbo"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "model_not_available"

    def test_an_empty_body_is_a_client_error(self, harness) -> None:
        h = harness([_entry()], {})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=b"")
        assert response.status_code == 400

    def test_everyone_down_reports_the_last_problem(self, harness) -> None:
        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"oops")

        h = harness([_entry()], {"http://ollama.test": broken})
        payload = json.dumps({"model": "llama3.2"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 502
        assert "Ollama" in response.json()["error"]["message"]


class TestServerErrors:
    """socketserver зовёт handle_error на СЕРВЕРЕ, не на обработчике."""

    def test_a_reset_client_leaves_no_traceback(self, capsys) -> None:
        server = _Server(("127.0.0.1", 0), _Handler)

        def provoke() -> None:
            try:
                raise ConnectionResetError("[WinError 10054] connection reset")
            except ConnectionResetError:
                server.handle_error(None, ("127.0.0.1", 55555))

        try:
            provoke()
        finally:
            server.server_close()

        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "Exception occurred" not in captured.err

    def test_a_real_failure_still_reaches_stderr(self, capsys) -> None:
        """Глушить можно только обрыв связи — поломка обязана быть видна."""
        server = _Server(("127.0.0.1", 0), _Handler)

        def provoke() -> None:
            try:
                raise ValueError("настоящая поломка")
            except ValueError:
                server.handle_error(None, ("127.0.0.1", 55555))

        try:
            provoke()
        finally:
            server.server_close()

        captured = capsys.readouterr()
        assert "ValueError" in captured.err
        assert "настоящая поломка" in captured.err


class TestStreaming:
    def test_an_sse_stream_passes_through(self, harness) -> None:
        # Кириллица в потоке — не поломка: тело уходит клиенту как есть.
        sse = (
            'data: {"choices":[{"delta":{"content":"прив"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"ет"}}]}\n\n'
            "data: [DONE]\n\n"
        ).encode("utf-8")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=sse, headers={"content-type": "text/event-stream"}
            )

        h = harness([_entry()], {"http://ollama.test": handler})
        payload = json.dumps({"model": "llama3.2", "stream": True})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.content == sse


class _FakeModel:
    """Провайдер с заранее заданным ответом — для тестового чата."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.seen: dict = {}

    def forward_chat(self, body, *, api_key, timeout, stream):
        self.seen = json.loads(body)
        return httpx.Response(200, json=self._payload)


class TestTestChat:
    def test_the_answer_comes_from_the_message_without_a_token_limit(self) -> None:
        fake = _FakeModel({"choices": [{"message": {"content": "работает"}}]})

        result = run_test_chat(_entry(), "sk", make_provider=lambda url, **kw: fake)

        assert result["answer"] == "работает"
        assert result["error"] == ""
        # Лимита по токенам нет: ответ всегда полный.
        assert "max_tokens" not in fake.seen

    def test_a_thinking_model_is_not_reported_as_empty(self) -> None:
        """qwen3 тратит лимит на рассуждение — покажем его, а не пустоту."""
        fake = _FakeModel(
            {
                "choices": [
                    {"message": {"content": "", "reasoning": "всё в порядке, отвечу: работает"}}
                ]
            }
        )

        result = run_test_chat(_entry(), "sk", make_provider=lambda url, **kw: fake)

        assert "рассуждала" in result["answer"]
        assert "работает" in result["answer"]

    def test_no_model_or_no_key_is_honest(self) -> None:
        assert "не выбрана модель" in run_test_chat(_entry(model=""), "sk")["error"]
        assert "нужен ключ" in run_test_chat(_entry(), "")["error"]

    def test_an_image_url_rides_along_for_vision_and_3d_agents(self) -> None:
        """Своё поле «картинка»: агенты вроде 3D для Blender ждут image_url."""
        fake = _FakeModel({"choices": [{"message": {"content": "готово"}}]})

        result = run_test_chat(_entry(), "sk", image_url="https://x/pic.png",
                               make_provider=lambda url, **kw: fake)

        assert result["error"] == ""
        content = fake.seen["messages"][0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[0]["text"]  # вопрос ушёл текстом
        assert content[1]["image_url"]["url"] == "https://x/pic.png"

    def test_without_an_image_url_the_body_stays_a_plain_string(self) -> None:
        """Обычный вопрос — как раньше: content строкой, не списком."""
        fake = _FakeModel({"choices": [{"message": {"content": "ок"}}]})

        run_test_chat(_entry(), "sk", make_provider=lambda url, **kw: fake)

        assert isinstance(fake.seen["messages"][0]["content"], str)

    def test_a_chosen_model_overrides_the_configured_one(self) -> None:
        """Поле «модель»: тест можно пустить на любой модели из каталога."""
        fake = _FakeModel({"choices": [{"message": {"content": "ок"}}]})

        result = run_test_chat(_entry(), "sk", model="openai/gpt-5.4-nano",
                               make_provider=lambda url, **kw: fake)

        assert result["error"] == ""
        assert fake.seen["model"] == "openai/gpt-5.4-nano"

    def test_a_chosen_model_replaces_even_an_empty_configured_one(self) -> None:
        """Без настроенной модели, но с выбранной — тест всё равно идёт."""
        fake = _FakeModel({"choices": [{"message": {"content": "ок"}}]})

        result = run_test_chat(_entry(model=""), "sk", model="moonshotai/kimi-k3",
                               make_provider=lambda url, **kw: fake)

        assert result["error"] == ""
        assert fake.seen["model"] == "moonshotai/kimi-k3"

    def test_a_hidden_error_in_a_200_is_reported_not_swallowed(self) -> None:
        fake = _FakeModel({"error": {"message": "insufficient quota"}})

        result = run_test_chat(_entry(), "sk", make_provider=lambda url, **kw: fake)

        assert "quota" in result["error"]


class _StatusModel:
    """Провайдер, отвечающий произвольным статусом и телом (как Copilot)."""

    def __init__(self, status: int, text: str, headers: dict | None = None):
        self._status = status
        self._text = text
        self._headers = headers or {}

    def forward_chat(self, body, *, api_key, timeout, stream):
        return httpx.Response(self._status, text=self._text, headers=self._headers)


class TestTestChatCopilotErrors:
    """Отказ Copilot (400 model_not_supported / 429) — причиной, а не JSON."""

    _ENTRY = {"url": "https://api.githubcopilot.com", "model": "claude-haiku-4.5"}
    _MODEL_400 = ('{"error":{"message":"The requested model is not supported.",'
                  '"code":"model_not_supported","param":"model",'
                  '"type":"invalid_request_error"}}')

    @pytest.fixture(autouse=True)
    def _sku(self, monkeypatch):
        from providers import copilot
        monkeypatch.setattr(copilot, "_last_sku", "free_limited_copilot")

    def test_model_not_supported_becomes_advice_not_raw_json(self) -> None:
        fake = _StatusModel(400, self._MODEL_400)

        result = run_test_chat(_entry(**self._ENTRY), "gho_x",
                               make_provider=lambda url, **kw: fake)

        assert "model_not_supported" not in result["error"]
        assert "gpt-4o-mini" in result["error"]  # что делать вместо «вот JSON»

    def test_rate_limit_says_how_long_to_wait(self) -> None:
        fake = _StatusModel(
            429,
            "Sorry, you've exceeded your rate limit for utility models.",
            {"retry-after": "1183"},
        )

        result = run_test_chat(_entry(**self._ENTRY), "gho_x",
                               make_provider=lambda url, **kw: fake)

        assert "лимит" in result["error"].lower()
        assert "20 мин" in result["error"]

    def test_a_foreign_provider_keeps_the_raw_body(self) -> None:
        """Чужой провайдер — как раньше: сырая причина, без выдумок."""
        fake = _StatusModel(400, '{"error":{"code":"other"}}')

        result = run_test_chat(_entry(), "sk",
                               make_provider=lambda url, **kw: fake)

        assert "HTTP 400" in result["error"]
        assert "other" in result["error"]


class TestProxyTransparency:
    """Клиент должен знать, кто реально ответил, — не только журнал приставки."""

    def _two(self) -> list[dict]:
        return [
            _entry(),  # первый по приоритету, ответит отказом
            _entry(
                id="openai",
                name="OpenAI",
                url="https://api.openai.test/v1",
                model="gpt-4o-mini",
                fallbackPriority=9,
            ),
        ]

    def test_a_served_response_carries_provider_and_model_headers(self, harness) -> None:
        h = harness(
            self._two(),
            {
                "http://ollama.test": lambda req: httpx.Response(
                    429, json={"error": {"message": "insufficient quota"}}
                ),
                "https://api.openai.test": lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": "ok"}}]}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post(
                "/v1/chat/completions", content=json.dumps({"messages": [{"role": "user", "content": "привет"}]})
            )

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-provider"] == "OpenAI"
        assert response.headers["x-ai-bridge-model"] == "gpt-4o-mini"

    def test_a_quota_exhausted_provider_hands_over_to_the_next(self, harness) -> None:
        """402 (пустой баланс) — тоже повод переключиться, как и 429."""
        h = harness(
            self._two(),
            {
                "http://ollama.test": lambda req: httpx.Response(
                    402, json={"error": {"message": "no credits"}}
                ),
                "https://api.openai.test": lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": "ok"}}]}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=json.dumps({"messages": []}))

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-provider"] == "OpenAI"

    def test_when_nobody_answers_the_error_lists_every_attempt(self, harness) -> None:
        h = harness(
            self._two(),
            {
                "http://ollama.test": lambda req: httpx.Response(
                    429, json={"error": {"message": "quota"}}
                ),
                "https://api.openai.test": lambda req: httpx.Response(
                    500, json={"error": {"message": "сломался"}}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=json.dumps({"messages": []}))

        assert response.status_code == 502
        text = response.text
        assert "ни один провайдер не ответил" in text
        assert "Ollama" in text and "HTTP 429" in text
        assert "OpenAI" in text and "HTTP 500" in text


class TestCapabilityFallback:
    """Требование запроса (картинка/инструменты) сохраняется при переключении."""

    VISION_BODY = json.dumps(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "что на картинке?"},
                        {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                    ],
                }
            ]
        }
    )

    @staticmethod
    def _mixed() -> list[dict]:
        return [
            _entry(id="vis", name="Vis", url="https://vis.test/v1", model="m-vis",
                   fallbackPriority=1, capabilities="vision"),
            _entry(id="plain", name="Plain", url="https://plain.test/v1", model="m-plain",
                   fallbackPriority=2),
            _entry(id="both", name="Both", url="https://both.test/v1", model="m-both",
                   fallbackPriority=3, capabilities="vision, tools"),
        ]

    def test_a_vision_fallback_lands_on_a_vision_model(self, harness) -> None:
        h = harness(
            self._mixed(),
            {
                "https://vis.test": lambda req: httpx.Response(
                    429, json={"error": {"message": "quota"}}
                ),
                "https://both.test": lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": "вижу"}}]}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=self.VISION_BODY)

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-provider"] == "Both"

    def test_a_vision_request_lands_on_a_vision_model_first(self, harness) -> None:
        """Способная идёт первой; немаркированная — только хвост цепочки.

        Немаркированные не выбрасываются совсем (иначе замкнутая петля:
        выучить vision можно лишь получив картинку, а фильтр давал бы её
        только уже помеченным). Они идут последним шансом — чтобы отказ
        выучился или успех открыл vision.
        """
        def boom(req):
            raise httpx.ConnectError("оба мертвы")

        h = harness(
            self._mixed(),
            {
                "https://vis.test": lambda req: httpx.Response(
                    429, json={"error": {"message": "quota"}}
                ),
                "https://both.test": boom,
                # Немаркированная отвечает — доказывает, что её пробуют
                # последним шансом (иначе vision выучить невозможно).
                "https://plain.test": lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": "вдруг умею"}}]}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=self.VISION_BODY)

        # Способная (vis/both) отказалась, немаркированная plain подхватила
        # последним шансом — запрос обслужен, а не 502.
        assert response.status_code == 200
        # Порядок: сначала способные, немаркированная — хвост цепочки.
        urls = [req.url.host for req in h.seen]
        assert urls.index("vis.test") < urls.index("plain.test")
        assert urls.index("both.test") < urls.index("plain.test")

    def test_a_400_refusal_about_images_hands_over(self, harness) -> None:
        h = harness(
            [
                _entry(id="vis", name="Vis", url="https://vis.test/v1", model="m-vis",
                       fallbackPriority=1, capabilities="vision"),
                _entry(id="both", name="Both", url="https://both.test/v1", model="m-both",
                       fallbackPriority=2, capabilities="vision, tools"),
            ],
            {
                "https://vis.test": lambda req: httpx.Response(
                    400, json={"error": {"message": "image_url is not supported by this model"}}
                ),
                "https://both.test": lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": "вижу"}}]}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=self.VISION_BODY)

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-provider"] == "Both"

    def test_a_400_refusal_about_websocket_only_models_switches(self, harness) -> None:
        """Live-модель Gemini отвечает только через WebSocket — отказ
        по существу (модель не может обслужить наш канал), цепочка
        продолжает следующей включённой моделью."""
        h = harness(
            [
                _entry(id="gemini", name="Google Gemini", url="https://gemini.test/v1",
                       model="models/gemini-3.1-flash-live-preview", fallbackPriority=1),
                _entry(id="local", name="Local", url="http://local.test/v1",
                       model="qwen3:0.6b", fallbackPriority=9),
            ],
            {
                "https://gemini.test": lambda req: httpx.Response(
                    400, json={"error": {"message": (
                        "models/gemini-3.1-flash-live-preview only supports real-time "
                        "bidirectional streaming via WebSocket (bidiGenerateContent). "
                        "Please use the Gemini Live API (bidiGenerateContent via WebSocket) "
                        "instead of generateContent."
                    ), "status": "INVALID_ARGUMENT"}},
                ),
                "http://local.test": lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": "жив"}}]}
                ),
            },
        )
        payload = json.dumps({"model": "models/gemini-3.1-flash-live-preview"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-provider"] == "Local"
        # Оба провайдера получили запрос: живая-модель отвергла, локальная ответила.
        assert [request.url.host for request in h.seen] == ["gemini.test", "local.test"]

    def test_a_plain_400_still_goes_to_the_client(self, harness) -> None:
        h = harness(
            [_entry()],
            {
                "http://ollama.test": lambda req: httpx.Response(
                    400, json={"error": {"message": "messages is required"}}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=json.dumps({"wrong": True}))

        assert response.status_code == 400
        assert "messages is required" in response.text


class TestHiddenErrors:
    """Шлюзы иногда отвечают 200, а кладут в JSON ошибку — это отказ."""

    @staticmethod
    def _two() -> list[dict]:
        return [
            _entry(),  # первый по приоритету, ответит «скрытой ошибкой»
            _entry(
                id="openai",
                name="OpenAI",
                url="https://api.openai.test/v1",
                model="gpt-4o-mini",
                fallbackPriority=9,
            ),
        ]

    def test_a_200_with_an_error_body_hands_over_to_the_next(self, harness) -> None:
        h = harness(
            self._two(),
            {
                "http://ollama.test": lambda req: httpx.Response(
                    200, json={"error": {"message": "insufficient quota"}}
                ),
                "https://api.openai.test": lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": "ok"}}]}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=json.dumps({"messages": []}))

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-provider"] == "OpenAI"

    def test_the_reason_lands_in_the_error_when_nobody_answers(self, harness) -> None:
        h = harness(
            self._two(),
            {
                "http://ollama.test": lambda req: httpx.Response(
                    200, json={"error": {"message": "quota exceeded"}}
                ),
                "https://api.openai.test": lambda req: httpx.Response(
                    200, json={"error": {"message": "also broken"}}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=json.dumps({"messages": []}))

        assert response.status_code == 502
        assert "скрытая ошибка" in response.text
        assert "quota exceeded" in response.text

    def test_a_healthy_200_is_not_disturbed(self, harness) -> None:
        h = harness(
            [_entry()],
            {
                "http://ollama.test": lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": "привет"}}]}
                ),
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=json.dumps({"messages": []}))

        assert response.status_code == 200
        assert response.headers["x-ai-bridge-provider"] == "Ollama"


class TestHiddenErrorShapes:
    """Что считается ошибкой, а что здоровым ответом."""

    def test_error_objects_are_failures(self) -> None:
        assert "quota" in _hidden_error(b'{"error": {"message": "quota"}}')
        assert "упал" in _hidden_error('{"error": "упал"}'.encode("utf-8"))
        assert "скрытая ошибка" in _hidden_error(b'{"type": "error", "message": "x"}')

    def test_a_chat_answer_is_not_an_error(self) -> None:
        healthy = b'{"choices": [{"message": {"content": "ok"}}]}'
        assert _hidden_error(healthy) == ""

    def test_garbage_and_empty_bodies_are_not_errors(self) -> None:
        assert _hidden_error(b"") == ""
        assert _hidden_error("<html>привет</html>".encode("utf-8")) == ""
        assert _hidden_error(b'{"junk": 1}') == ""


class TestBodiesInTheJournal:
    """Тела запросов пишутся всегда (переключателя нет)."""

    def _chat_event(self, tmp_path) -> dict:
        from service.eventlog import EventLog
        from storage.usertokens import UserTokens

        log = EventLog(tmp_path)
        tokens = UserTokens(tmp_path)
        token = tokens.create("u1", "телефон")
        gateway = Gateway(
            lambda: [_entry()],
            KeyStore(tmp_path),
            user_tokens=tokens,
            event_log=log,
        )
        server = _Server(("127.0.0.1", 0), _Handler)
        server.gateway = gateway
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with httpx.Client(base_url=base) as client:
                client.post(
                    "/v1/chat/completions",
                    content=json.dumps(
                        {"messages": [{"role": "user", "content": "привет"}]},
                        ensure_ascii=False,
                    ),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            server.shutdown()
            server.server_close()
        return next(event for event in log.recent() if event["kind"] == "чат")

    def test_a_chat_event_always_records_the_request_body(self, tmp_path) -> None:
        event = self._chat_event(tmp_path)
        assert "привет" in event.get("req", "")


class TestApiGate:
    """Тумблер «доступ приложений»: выключил — ни один токен не пройдёт."""

    def _gateway(self, tmp_path, gate) -> tuple[Gateway, str]:
        from storage.usertokens import UserTokens

        tokens = UserTokens(tmp_path)
        token = tokens.create("u1", "телефон")
        gateway = Gateway(
            lambda: [],
            KeyStore(tmp_path),
            user_tokens=tokens,
            api_gate=gate,
        )
        return gateway, token

    def test_a_switched_off_person_gets_a_clear_401(self, tmp_path) -> None:
        gateway, token = self._gateway(tmp_path, lambda uid: False)

        allowed, user_id, reason = gateway.authenticate(f"Bearer {token}")

        assert allowed is False
        assert user_id == ""
        assert "отключён" in reason

    def test_a_switched_on_person_passes_with_their_id(self, tmp_path) -> None:
        from storage.usertokens import UserTokens

        tokens = UserTokens(tmp_path)
        token = tokens.create("u1", "телефон")
        gateway = Gateway(
            lambda: [], KeyStore(tmp_path), user_tokens=tokens, api_gate=lambda uid: True
        )

        allowed, user_id, reason = gateway.authenticate(f"Bearer {token}")

        assert (allowed, user_id, reason) == (True, "u1", "")

    def test_an_unknown_token_is_refused_without_sermons(self, tmp_path) -> None:
        gateway, _ = self._gateway(tmp_path, lambda uid: True)

        allowed, user_id, reason = gateway.authenticate("Bearer uib-чужой")

        assert (allowed, user_id, reason) == (False, "", "")


class TestLocalTimeout:
    """Локальная модель может грузиться в память до пяти минут."""

    def test_local_providers_get_the_patient_timeout(self, tmp_path) -> None:
        gateway = Gateway(lambda: [], KeyStore(tmp_path), timeout=180.0)

        assert gateway._chat_timeout({"url": "http://192.168.0.133:11434/v1"}) == 600.0
        assert gateway._chat_timeout({"url": "http://localhost:1234/v1"}) == 600.0

    def test_remote_providers_keep_the_configured_timeout(self, tmp_path) -> None:
        gateway = Gateway(lambda: [], KeyStore(tmp_path), timeout=180.0)

        assert gateway._chat_timeout({"url": "https://api.openai.com/v1"}) == 180.0
        assert gateway._chat_timeout({"url": "https://8.8.8.8/v1"}) == 180.0

    def test_a_bigger_setting_is_never_shrunk(self, tmp_path) -> None:
        gateway = Gateway(lambda: [], KeyStore(tmp_path), timeout=900.0)

        assert gateway._chat_timeout({"url": "http://127.0.0.1:1/v1"}) == 900.0

    def test_the_test_chat_waits_for_a_cold_local_model(self) -> None:
        seen: dict = {}

        class _Fake:
            def forward_chat(self, body, *, api_key, timeout, stream):
                seen["timeout"] = timeout
                import httpx as _httpx

                return _httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

        entry = _entry(url="http://192.168.0.133:11434/v1")
        result = run_test_chat(entry, "sk", make_provider=lambda url, **kw: _Fake())

        assert result["answer"] == "ок"
        assert seen["timeout"] == 600.0


class TestAccessRegistry:
    """Онлайн-проверка реестра: зарегистрирован/заблокирован, с кэшем."""

    def _gateway(self, tmp_path, checker) -> tuple[Gateway, list]:
        calls: list[str] = []

        def wrapped(user_id: str) -> bool:
            calls.append(user_id)
            return checker(user_id)

        return Gateway(lambda: [], KeyStore(tmp_path), access_checker=wrapped), calls

    def test_an_ok_person_passes_and_is_remembered(self, tmp_path) -> None:
        gateway, calls = self._gateway(tmp_path, lambda uid: True)

        assert gateway.authorize("u1") == (True, "")
        assert gateway.authorize("u1") == (True, "")  # в пределах TTL — из кэша
        assert calls == ["u1"]  # десятки запросов = один поход к реестру

    def test_a_blocked_person_is_refused_and_remembered(self, tmp_path) -> None:
        gateway, calls = self._gateway(tmp_path, lambda uid: False)

        ok, reason = gateway.authorize("u1")
        gateway.authorize("u1")

        assert ok is False
        assert "не зарегистрирован или заблокирован" in reason
        assert calls == ["u1"]

    def test_a_broken_registry_cools_down_without_dolba(self, tmp_path) -> None:
        def broken(uid: str) -> bool:
            raise RuntimeError("реестр лёг")

        gateway, calls = self._gateway(tmp_path, broken)

        ok, reason = gateway.authorize("u1")
        ok_second, _ = gateway.authorize("u2")  # другой человек — тоже отказ

        assert ok is False and ok_second is False
        assert "реестр недоступен" in reason
        assert calls == ["u1"]  # после поломки — ни одного нового звонка

    def test_without_a_checker_the_registry_is_off(self, tmp_path) -> None:
        gateway = Gateway(lambda: [], KeyStore(tmp_path))
        assert gateway.authorize("u1") == (True, "")


class TestAccessOverHttp:
    def test_a_blocked_person_gets_403_with_a_clear_reason(self, tmp_path) -> None:
        from storage.usertokens import UserTokens

        tokens = UserTokens(tmp_path)
        token = tokens.create("u1", "телефон")
        gateway = Gateway(
            lambda: [_entry()],
            KeyStore(tmp_path),
            user_tokens=tokens,
            access_checker=lambda uid: False,
        )
        server = _Server(("127.0.0.1", 0), _Handler)
        server.gateway = gateway
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with httpx.Client(base_url=base) as client:
                response = client.get(
                    "/v1/models", headers={"Authorization": f"Bearer {token}"}
                )
        finally:
            server.shutdown()
            server.server_close()

        assert response.status_code == 403
        assert "заблокирован" in response.text

    def test_a_refused_post_closes_the_connection(self, tmp_path) -> None:
        """Отказ до чтения тела закрывает соединение.

        Регрессия: 403 до чтения тела на keep-alive оставлял JSON в
        сокете, следующий запрос читался из этого мусора — браузер
        получал 501 «Unsupported method» с куском тела вместо имени
        метода, и страница падала при каждом запросе.
        """
        import socket

        from storage.usertokens import UserTokens

        tokens = UserTokens(tmp_path)
        token = tokens.create("u1", "телефон")
        gateway = Gateway(
            lambda: [_entry()],
            KeyStore(tmp_path),
            user_tokens=tokens,
            access_checker=lambda uid: False,
        )
        server = _Server(("127.0.0.1", 0), _Handler)
        server.gateway = gateway
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps({"model": "m", "messages": []}).encode()
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            sock.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\n"
                b"Host: x\r\n"
                + f"Authorization: Bearer {token}\r\n".encode()
                + b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            # Читаем до конца соединения: сервер обязан его закрыть,
            # иначе остаток тела поедет в следующий запрос.
            sock.settimeout(5)
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
            sock.close()
        finally:
            server.shutdown()
            server.server_close()

        head = data.split(b"\r\n\r\n", 1)[0].decode("utf-8", "replace")
        assert "403" in head.splitlines()[0]
        assert "connection: close" in head.lower()


class TestUsageRecording:
    """Расходы: токены и запросы падают в статистику из ответов провайдеров."""

    def _gateway(self, tmp_path, entries, handlers):
        """Шлюз с личным токеном u1 и хранилищем расходов."""
        from providers.client import Provider
        from storage.usertokens import UserTokens
        from storage.usage import UsageStore

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            inner = handlers[base]
            return Provider(url, transport=httpx.MockTransport(inner), **kwargs)

        tokens = UserTokens(tmp_path)
        token = tokens.create("u1", "телефон")
        usage = UsageStore(tmp_path)
        gateway = Gateway(
            lambda: entries,
            KeyStore(tmp_path),
            provider_factory=factory,
            user_tokens=tokens,
            usage_store=usage,
        )
        return gateway, usage, token

    @staticmethod
    def _chat(gateway, token, model="llama3.2"):
        server = _Server(("127.0.0.1", 0), _Handler)
        server.gateway = gateway
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with httpx.Client(base_url=base) as client:
                return client.post(
                    "/v1/chat/completions",
                    content=json.dumps({"model": model, "messages": []}),
                    headers={"Authorization": f"Bearer {token}"},
                )
        finally:
            server.shutdown()
            server.server_close()

    def test_tokens_of_a_plain_answer_are_recorded(self, tmp_path) -> None:
        body = json.dumps(
            {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 40}}
        ).encode("utf-8")

        def ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})

        gateway, usage, token = self._gateway(
            tmp_path, [_entry()], {"http://ollama.test": ok}
        )
        response = self._chat(gateway, token)

        assert response.status_code == 200
        cell = usage.for_user("u1")["ollama/llama3.2"]
        assert cell["requests"] == 1
        assert cell["prompt"] == 120
        assert cell["completion"] == 40

    def test_a_fallback_counts_as_switched(self, tmp_path) -> None:
        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b'{"error": "busy"}')

        body = json.dumps(
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        ).encode("utf-8")

        def ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})

        entries = [
            _entry(fallbackPriority=1),
            _entry(id="b", name="B", url="https://b.test/v1", fallbackPriority=2),
        ]
        gateway, usage, token = self._gateway(
            tmp_path, entries, {"http://ollama.test": broken, "https://b.test": ok}
        )
        self._chat(gateway, token)

        cell = usage.for_user("u1")["b/llama3.2"]
        assert cell["requests"] == 1
        assert cell["switches"] == 1
        assert cell["prompt"] == 10
        assert "ollama/llama3.2" not in usage.for_user("u1")

    def test_a_total_failure_is_recorded_as_a_fail(self, tmp_path) -> None:
        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b'{"error": "busy"}')

        gateway, usage, token = self._gateway(
            tmp_path, [_entry()], {"http://ollama.test": broken}
        )
        response = self._chat(gateway, token)

        assert response.status_code == 502
        cell = usage.for_user("u1")["—/llama3.2"]
        assert cell["requests"] == 1
        assert cell["fails"] == 1

    def test_usage_found_inside_a_stream_is_recorded(self, tmp_path) -> None:
        sse = (
            'data: {"choices": [{"delta": {"content": "п"}}]}\n\n'
            'data: {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}\n\n'
            "data: [DONE]\n\n"
        ).encode("utf-8")

        def stream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=sse, headers={"content-type": "text/event-stream"}
            )

        gateway, usage, token = self._gateway(
            tmp_path, [_entry()], {"http://ollama.test": stream}
        )
        response = self._chat(gateway, token)

        assert response.status_code == 200
        cell = usage.for_user("u1")["ollama/llama3.2"]
        assert cell["requests"] == 1
        assert cell["prompt"] == 7
        assert cell["completion"] == 3

    def test_no_usage_field_means_requests_only(self, tmp_path) -> None:
        def ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b'{"choices": []}', headers={"content-type": "application/json"}
            )

        gateway, usage, token = self._gateway(
            tmp_path, [_entry()], {"http://ollama.test": ok}
        )
        self._chat(gateway, token)

        cell = usage.for_user("u1")["ollama/llama3.2"]
        assert cell["requests"] == 1
        assert cell["prompt"] == 0  # провайдер не сообщил — не выдумываем


class TestUsageParsing:
    """Разбор usage: из тела ответа и из строк SSE-потока."""

    def test_usage_is_pulled_out_of_a_json_body(self) -> None:
        from service.gateway import _usage_from

        body = b'{"choices": [], "usage": {"prompt_tokens": 5}}'
        assert _usage_from(body) == {"prompt_tokens": 5}

    def test_a_body_without_usage_is_empty(self) -> None:
        from service.gateway import _usage_from

        assert _usage_from(b'{"choices": []}') == {}
        assert _usage_from("не json".encode("utf-8")) == {}

    def test_sse_lines_fill_the_state_until_done(self) -> None:
        from service.gateway import _absorb_usage

        state: dict = {}
        _absorb_usage(state, 'data: {"choices": [1]}')
        assert state == {}
        _absorb_usage(state, 'data: {"usage": {"prompt_tokens": 9}}')
        _absorb_usage(state, "data: [DONE]")
        assert state["usage"] == {"prompt_tokens": 9}

    def test_noise_and_lies_are_ignored(self) -> None:
        from service.gateway import _absorb_usage

        state: dict = {}
        _absorb_usage(state, ": комментарий")
        _absorb_usage(state, "data: [сломанный json")
        _absorb_usage(state, 'data: {"usage": "не объект"}')
        assert state == {}


class _Journal:
    """Мини-журнал событий: собирает kind+text для проверки."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def record(self, kind: str, user: str, text: str, **fields) -> dict:
        self.events.append((kind, text))
        return {"kind": kind, "text": text}

    def texts(self, kind: str) -> list[str]:
        return [text for event_kind, text in self.events if event_kind == kind]


class TestAutotestMedia:
    """Медиа-автотест: генерация картинок/звука/видео и векторы.

    Каждый тип ходит своим эндпоинтом и проверяет успех по-своему;
    список моделей берётся из метаданных каталога (category/endpoints),
    а без них — по именам. Сеть замокана, ключи не нужны.
    """

    @staticmethod
    def _build(tmp_path, handler) -> Gateway:
        from providers.client import Provider
        from storage.capabilities import CapabilityStore
        from storage.lists import ModelLists

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            return Provider(url, transport=httpx.MockTransport(handler), **kwargs)

        return Gateway(
            lambda: [_entry()], KeyStore(tmp_path), provider_factory=factory,
            lists=ModelLists(tmp_path), capabilities=CapabilityStore(tmp_path),
        )

    @staticmethod
    def _models(monkeypatch, models, meta=None) -> None:
        """Каталог: имена обязательны, метаданные — по желанию теста."""
        from providers.client import Provider

        monkeypatch.setattr(
            Provider, "fetch_models",
            lambda self, key: [{"name": name} for name in models],
        )
        monkeypatch.setattr(
            Provider, "fetch_model_meta", lambda self, key: (meta or {})
        )

    @staticmethod
    def _row(result: dict) -> dict:
        assert result["results"], "результатов нет"
        return result["results"][0]

    # ------------------------------------------------------------ image

    def test_image_generates_and_reports_the_url(self, tmp_path, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/images/generations"
            body = json.loads(request.content)
            assert body["prompt"] == "кот в космосе"
            assert body["response_format"] == "url"
            return httpx.Response(200, json={"data": [
                {"url": "https://media.test/pic.png"},
            ]})

        self._models(monkeypatch, ["img-1"], {
            "img-1": {"category": "image",
                      "endpoints": ["/v1/images/generations"], "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "кот в космосе", ["image"]))

        assert row["status"] == "ok"
        assert row["response"] == "https://media.test/pic.png"

    def test_image_base64_is_never_stored(self, tmp_path, monkeypatch) -> None:
        """Картинка в base64 — мегабайты: в журнал идёт только пометка."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"b64_json": "A" * 50000}]})

        self._models(monkeypatch, ["img-1"], {
            "img-1": {"category": "image",
                      "endpoints": ["/v1/images/generations"], "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "кот", ["image"]))

        assert row["status"] == "ok"
        assert "b64_json" in row["response"]
        assert len(row["response"]) < 120
        assert len(row["resp"]) < 200  # сырое тело не сохранено

    def test_a_two_hundred_without_a_picture_is_a_failure(self, tmp_path,
                                                          monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        self._models(monkeypatch, ["img-1"], {
            "img-1": {"category": "image",
                      "endpoints": ["/v1/images/generations"], "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "кот", ["image"]))

        assert row["status"] == "fail"
        assert "нет картинки" in row["response"]

    # ------------------------------------------------------------ tts

    def test_tts_prefers_the_public_link(self, tmp_path, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/audio/speech"
            body = json.loads(request.content)
            assert body["input"] == "привет"
            assert body["voice"] == "nova"
            return httpx.Response(
                200, content=b"ID3" + b"\0" * 500,
                headers={"content-type": "audio/mpeg",
                         "Link": '<https://media.test/voice.mp3>; rel="enclosure"'},
            )

        self._models(monkeypatch, ["tts-1"], {
            "tts-1": {"category": "audio", "endpoints": ["/v1/audio/speech"],
                      "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "привет", ["tts"],
                                             voice="nova"))

        assert row["status"] == "ok"
        assert row["response"] == "https://media.test/voice.mp3"

    def test_tts_bytes_without_a_link_report_size(self, tmp_path, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"ID3" * 400,
                                  headers={"content-type": "audio/mpeg"})

        self._models(monkeypatch, ["tts-1"], {
            "tts-1": {"category": "audio", "endpoints": ["/v1/audio/speech"],
                      "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "привет", ["tts"]))

        assert row["status"] == "ok"
        assert "КБ" in row["response"]
        assert "ID3" not in row["resp"]  # звук в журнал не пишем

    # ------------------------------------------------------------ stt

    def test_stt_sends_multipart_and_shows_the_text(self, tmp_path, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/audio/transcriptions"
            assert "multipart/form-data" in request.headers.get("content-type", "")
            assert b'name="model"' in request.content
            assert b'name="file"' in request.content
            return httpx.Response(200, json={"text": "привет"})

        self._models(monkeypatch, ["whisper-1"], {
            "whisper-1": {"category": "audio",
                          "endpoints": ["/v1/audio/transcriptions"], "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "распознай", ["stt"],
                                             audio=b"RIFF....WAVE", audio_name="t.wav"))

        assert row["status"] == "ok"
        assert row["response"] == "привет"

    def test_stt_without_audio_says_what_is_missing(self, tmp_path, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("без аудио запроса быть не должно")

        self._models(monkeypatch, ["whisper-1"], {
            "whisper-1": {"category": "audio",
                          "endpoints": ["/v1/audio/transcriptions"], "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "распознай", ["stt"]))

        assert row["status"] == "fail"
        assert "нет аудио" in row["response"]

    # ------------------------------------------------------------ video / 3d

    def test_video_is_fetched_from_the_root_and_shows_its_link(self, tmp_path,
                                                               monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.startswith("/video/")
            assert request.url.params.get("model") == "veo-3"
            return httpx.Response(
                200, content=b"\x00\x00\x00\x18ftypmp42",
                headers={"content-type": "video/mp4",
                         "Link": '<https://media.test/clip.mp4>; rel="enclosure"'},
            )

        self._models(monkeypatch, ["veo-3"], {
            "veo-3": {"category": "video", "endpoints": [], "paid": True},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "закат", ["video"]))

        assert row["status"] == "ok"
        assert row["response"] == "https://media.test/clip.mp4"

    def test_video_without_a_link_is_still_a_clear_result(self, tmp_path,
                                                          monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"MP4DATA",
                                  headers={"content-type": "video/mp4"})

        self._models(monkeypatch, ["veo-3"], {
            "veo-3": {"category": "video", "endpoints": [], "paid": True},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "закат", ["video"]))

        assert row["status"] == "ok"
        assert "ссылка не пришла" in row["response"]

    # ------------------------------------------------------------ embedding

    def test_embedding_reports_the_vector_length(self, tmp_path, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/embeddings"
            assert json.loads(request.content)["input"] == "привет"
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

        self._models(monkeypatch, ["emb-1"], {
            "emb-1": {"category": "embedding", "endpoints": ["/v1/embeddings"],
                      "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "привет", ["embedding"]))

        assert row["status"] == "ok"
        assert row["response"] == "вектор из 3 чисел"

    # ------------------------------------------------------------ выбор моделей

    def test_each_type_runs_only_models_of_its_kind(self, tmp_path, monkeypatch) -> None:
        """Image-тест не трогает tts-модели и наоборот."""
        asked: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            asked.append(body["model"])
            return httpx.Response(200, json={"data": [
                {"url": "https://media.test/p.png"},
            ]})

        self._models(monkeypatch, ["img-1", "tts-1", "veo-3"], {
            "img-1": {"category": "image",
                      "endpoints": ["/v1/images/generations"], "paid": False},
            "tts-1": {"category": "audio", "endpoints": ["/v1/audio/speech"],
                      "paid": False},
            "veo-3": {"category": "video", "endpoints": [], "paid": True},
        })
        gateway = self._build(tmp_path, handler)
        result = gateway.run_autotest("ollama", "", "кот", ["image"])

        assert asked == ["img-1"]
        assert len(result["results"]) == 1

    def test_no_models_of_that_type_is_an_honest_failure(self, tmp_path,
                                                         monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("запросов быть не должно")

        self._models(monkeypatch, ["txt-1"], {
            "txt-1": {"category": "text", "endpoints": ["/v1/chat/completions"],
                      "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "кот", ["image"]))

        assert row["status"] == "fail"
        assert "нет моделей типа image" in row["response"]
        assert row["model"] == "—"

    def test_without_metadata_models_are_picked_by_name(self, tmp_path,
                                                        monkeypatch) -> None:
        """Провайдер без типов в каталоге (OpenAI) — фильтр по именам."""
        asked: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(json.loads(request.content)["model"])
            return httpx.Response(200, json={"data": [
                {"url": "https://media.test/p.png"},
            ]})

        self._models(monkeypatch, ["dall-e-3", "gpt-4o"], meta={})
        gateway = self._build(tmp_path, handler)
        gateway.run_autotest("ollama", "", "кот", ["image"])

        assert asked == ["dall-e-3"]

    def test_a_named_model_narrows_the_run(self, tmp_path, monkeypatch) -> None:
        """Поле «модель» в форме: гоняем только указанную."""
        asked: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(json.loads(request.content)["model"])
            return httpx.Response(200, json={"data": [
                {"url": "https://media.test/p.png"},
            ]})

        self._models(monkeypatch, ["a", "b"], {
            "a": {"category": "image",
                  "endpoints": ["/v1/images/generations"], "paid": False},
            "b": {"category": "image",
                  "endpoints": ["/v1/images/generations"], "paid": False},
        })
        gateway = self._build(tmp_path, handler)
        row = self._row(gateway.run_autotest("ollama", "", "кот", ["image"],
                                             only_model="my/pic"))

        assert asked == ["my/pic"]
        assert row["model"] == "my/pic"


class TestImageTest:
    """Дополнительный тест на странице «Тест»: генерация картинки одной строкой.

    Операция чистая (сеть за моком), страница её вызывает формой:
    промпт + модель → картинка в поле результата.
    """

    @staticmethod
    def _factory(handler):
        from providers.client import Provider

        def make(url: str, **kwargs) -> Provider:
            return Provider(url, transport=httpx.MockTransport(handler), **kwargs)

        return make

    def test_a_generated_url_comes_back_clean(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/images/generations"
            body = json.loads(request.content)
            assert body["prompt"] == "кот в космосе"
            assert body["model"] == "flux.1-schnell"
            assert body["response_format"] == "url"
            return httpx.Response(200, json={"data": [
                {"url": "https://media.test/pic.png"},
            ]})

        from service.gateway import generate_image

        result = generate_image(
            _entry(), "sk-1", prompt="кот в космосе", model="flux.1-schnell",
            make_provider=self._factory(handler),
        )

        assert result["error"] == ""
        assert result["url"] == "https://media.test/pic.png"
        assert result["model"] == "flux.1-schnell"

    def test_base64_is_kept_for_the_page_to_wrap(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"b64_json": "QUJD"}]})

        from service.gateway import generate_image

        result = generate_image(
            _entry(), "sk-1", prompt="кот", model="flux",
            make_provider=self._factory(handler),
        )

        assert result["error"] == ""
        assert result["b64"] == "QUJD"
        assert result["url"] == ""

    def test_an_empty_answer_is_an_honest_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        from service.gateway import generate_image

        result = generate_image(
            _entry(), "sk-1", prompt="кот", model="flux",
            make_provider=self._factory(handler),
        )

        assert "нет картинки" in result["error"]
        assert result["url"] == "" and result["b64"] == ""

    def test_an_http_error_reports_status_and_detail(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(402, json={"error": {"message": "нет кредитов"}})

        from service.gateway import generate_image

        result = generate_image(
            _entry(), "sk-1", prompt="кот", model="flux",
            make_provider=self._factory(handler),
        )

        assert result["error"].startswith("HTTP 402")
        assert "нет кредитов" in result["error"]

    def test_not_json_is_named_as_such(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content="<html>привет</html>".encode("utf-8"))

        from service.gateway import generate_image

        result = generate_image(
            _entry(), "sk-1", prompt="кот", model="flux",
            make_provider=self._factory(handler),
        )

        assert "не-JSON" in result["error"]

    def test_without_prompt_model_or_key_nothing_is_sent(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "b.test" in str(request.url):
                raise AssertionError("запроса к b быть не должно")
            return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

        from service.gateway import generate_image

        factory = self._factory(handler)
        assert "промпт" in generate_image(
            _entry(), "sk", prompt="  ", model="m", make_provider=factory)["error"]
        assert "модель" in generate_image(
            _entry(), "sk", prompt="кот", model="", make_provider=factory)["error"]
        assert "ключ" in generate_image(
            _entry(), "", prompt="кот", model="m", make_provider=factory)["error"]

    def test_media_models_split_the_catalogue_by_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        from service.gateway import media_models

        class _Meta:
            @staticmethod
            def fetch_model_meta(key: str = "") -> dict:
                return {
                    "flux-free": {"category": "image",
                                  "endpoints": ["/v1/images/generations"],
                                  "paid": False},
                    "flux-pro": {"category": "image",
                                 "endpoints": ["/v1/images/generations"],
                                 "paid": True},
                    "tts-1": {"category": "audio",
                              "endpoints": ["/v1/audio/speech"], "paid": False},
                    "whisper-1": {"category": "audio",
                                  "endpoints": ["/v1/audio/transcriptions"],
                                  "paid": False},
                    "trellis-2": {"category": "3d", "endpoints": [], "paid": False},
                    "emb-1": {"category": "embedding",
                              "endpoints": ["/v1/embeddings"], "paid": False},
                    "veo": {"category": "video", "endpoints": [], "paid": True},
                }

        def make(url, **kwargs):
            return _Meta()

        media = media_models(_entry(), "sk", make_provider=make)

        assert media["image"] == [
            {"name": "flux-free", "paid": False},
            {"name": "flux-pro", "paid": True},
        ]
        # stt-модель к озвучке не идёт — у каждой секции свой эндпоинт.
        assert [m["name"] for m in media["audio"]] == ["tts-1"]
        assert [m["name"] for m in media["stt"]] == ["whisper-1"]
        assert [m["name"] for m in media["three"]] == ["trellis-2"]
        assert [m["name"] for m in media["embedding"]] == ["emb-1"]

    def test_music_and_voice_processing_split_out(self) -> None:
        """Музыка — по именам (ее endpoints не отличимы от TTS);
        обработка звука — по своим эндпоинтам, с пометкой действия."""
        from service.gateway import media_models

        class _Meta:
            @staticmethod
            def fetch_model_meta(key: str = "") -> dict:
                return {
                    "tts-1": {"category": "audio",
                              "endpoints": ["/v1/audio/speech"], "paid": False},
                    "google/lyria-3.5": {"category": "audio",
                                         "endpoints": ["/v1/audio/speech"],
                                         "paid": True},
                    "elevenlabs/stem-separation": {
                        "category": "audio",
                        "endpoints": ["/alpha/audio/stem-separation"], "paid": True},
                    "elevenlabs/voice-isolator": {
                        "category": "audio",
                        "endpoints": ["/v1/audio/voice-isolator"], "paid": True},
                    "elevenlabs/eleven-multilingual-sts-v2": {
                        "category": "audio",
                        "endpoints": ["/v1/audio/voice-changer"], "paid": True},
                }

        def make(url, **kwargs):
            return _Meta()

        media = media_models(_entry(), "sk", make_provider=make)

        assert [m["name"] for m in media["audio"]] == ["tts-1"]
        assert [m["name"] for m in media["music"]] == ["google/lyria-3.5"]
        assert media["voice"] == [
            {"name": "elevenlabs/eleven-multilingual-sts-v2", "paid": True,
             "action": "changer"},
            {"name": "elevenlabs/stem-separation", "paid": True, "action": "stem"},
            {"name": "elevenlabs/voice-isolator", "paid": True, "action": "isolator"},
        ]

    def test_a_provider_without_meta_offers_no_sections(self) -> None:
        """Нет метаданных — секций генерации нет (не у всех подряд)."""
        from providers.client import ServerError
        from service.gateway import media_models

        def make(url, **kwargs):
            class _Broken:
                @staticmethod
                def fetch_model_meta(key: str = "") -> dict:
                    raise ServerError("каталог недоступен")

            return _Broken()

        media = media_models(_entry(), "sk", make_provider=make)

        assert media == {"image": [], "audio": [], "three": [],
                         "embedding": [], "stt": [], "music": [], "voice": []}

    # ------------------------------------------------- озвучка, 3D, вектор

    def test_transcript_turns_speech_into_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/audio/transcriptions"
            assert "multipart/form-data" in request.headers.get("content-type", "")
            assert b'name="model"' in request.content
            assert b'name="file"' in request.content
            return httpx.Response(200, json={"text": "привет от приставки"})

        from service.gateway import generate_transcript

        result = generate_transcript(
            _entry(), "sk", model="whisper-1", audio=b"RIFF....WAVE",
            audio_name="note.wav", make_provider=self._factory(handler),
        )

        assert result["error"] == ""
        assert result["text"] == "привет от приставки"

    def test_transcript_without_audio_says_what_is_missing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("без аудио запроса быть не должно")

        from service.gateway import generate_transcript

        factory = self._factory(handler)
        result = generate_transcript(_entry(), "sk", model="whisper-1",
                                     make_provider=factory)
        assert "нет аудио" in result["error"]

        result = generate_transcript(_entry(), "", model="whisper-1",
                                     audio=b"RIFF", make_provider=factory)
        assert "ключ" in result["error"]

    def test_transcript_without_text_is_an_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        from service.gateway import generate_transcript

        result = generate_transcript(
            _entry(), "sk", model="whisper-1", audio=b"RIFF",
            make_provider=self._factory(handler),
        )

        assert "нет текста" in result["error"]

    # ------------------------------------------------- озвучка, 3D, вектор

    def test_audio_comes_back_as_a_public_link(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.startswith("/audio/")
            assert "привет" in request.url.path or "%20" in str(request.url)
            assert request.url.params.get("voice") == "nova"
            assert request.url.params.get("model") == "tts-1"
            return httpx.Response(
                200, content=b"ID3mp3",
                headers={"content-type": "audio/mpeg",
                         "Link": '<https://media.test/voice.mp3>; rel="enclosure"'},
            )

        from service.gateway import generate_audio

        result = generate_audio(
            _entry(), "sk", text="привет мир", model="tts-1", voice="nova",
            make_provider=self._factory(handler),
        )

        assert result["error"] == ""
        assert result["url"] == "https://media.test/voice.mp3"

    def test_audio_without_a_link_cannot_be_shown(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"ID3mp3",
                                  headers={"content-type": "audio/mpeg"})

        from service.gateway import generate_audio

        result = generate_audio(
            _entry(), "sk", text="привет", model="tts-1",
            make_provider=self._factory(handler),
        )

        assert "ссылка не пришла" in result["error"]

    def test_audio_needs_text_and_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "b.test" in str(request.url):
                raise AssertionError("запроса к b быть не должно")
            return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

        from service.gateway import generate_audio

        factory = self._factory(handler)
        assert "текст" in generate_audio(
            _entry(), "sk", text="  ", model="m", make_provider=factory)["error"]
        assert "ключ" in generate_audio(
            _entry(), "", text="привет", model="m", make_provider=factory)["error"]

    def test_3d_needs_an_http_image_and_a_model(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "b.test" in str(request.url):
                raise AssertionError("запроса к b быть не должно")
            return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

        from service.gateway import generate_3d

        factory = self._factory(handler)
        assert "картинку" in generate_3d(
            _entry(), "sk", image="", model="m", make_provider=factory)["error"]
        assert "http" in generate_3d(
            _entry(), "sk", image="javascript:alert(1)", model="m",
            make_provider=factory)["error"]
        assert "модель" in generate_3d(
            _entry(), "sk", image="https://x/p.png", model="",
            make_provider=factory)["error"]
        assert "ключ" in generate_3d(
            _entry(), "", image="https://x/p.png", model="m",
            make_provider=factory)["error"]

    def test_3d_sends_its_parameters_and_returns_the_link(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.startswith("/3d/")
            assert request.url.params.get("image") == "https://x/p.png"
            assert request.url.params.get("model") == "trellis-2"
            assert request.url.params.get("resolution") == "low"
            return httpx.Response(
                200, content=b"glTF",
                headers={"content-type": "model/gltf-binary",
                         "Link": '<https://media.test/model.glb>; rel="enclosure"'},
            )

        from service.gateway import generate_3d

        result = generate_3d(
            _entry(), "sk", image="https://x/p.png", model="trellis-2",
            resolution="low", make_provider=self._factory(handler),
        )

        assert result["error"] == ""
        assert result["url"] == "https://media.test/model.glb"

    def test_embedding_counts_the_vector_and_shows_a_sample(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/embeddings"
            body = json.loads(request.content)
            assert body["input"] == "привет" and body["model"] == "emb-1"
            return httpx.Response(200, json={"data": [
                {"embedding": [0.25, -0.5, 0.125]},
            ]})

        from service.gateway import embed_text

        result = embed_text(
            _entry(), "sk", text="привет", model="emb-1",
            make_provider=self._factory(handler),
        )

        assert result["error"] == ""
        assert result["count"] == 3
        assert "0.25" in result["sample"]

    def test_embedding_without_a_vector_is_an_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        from service.gateway import embed_text

        result = embed_text(
            _entry(), "sk", text="привет", model="emb-1",
            make_provider=self._factory(handler),
        )

        assert "нет вектора" in result["error"]

    def test_embedding_needs_text_model_and_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "b.test" in str(request.url):
                raise AssertionError("запроса к b быть не должно")
            return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

        from service.gateway import embed_text

        factory = self._factory(handler)
        assert "текст" in embed_text(
            _entry(), "sk", text=" ", model="m", make_provider=factory)["error"]
        assert "модель" in embed_text(
            _entry(), "sk", text="x", model="", make_provider=factory)["error"]
        assert "ключ" in embed_text(
            _entry(), "", text="x", model="m", make_provider=factory)["error"]

    # --------------------------------------------------------- jobs (async)

    def test_async_audio_waits_for_the_job_and_returns_its_url(self) -> None:
        """Prefer: respond-async →202 → опрос /v1/jobs → ссылка результата."""
        seen: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/audio/hello":
                assert request.headers.get("Prefer") == "respond-async"
                return httpx.Response(202, json={"id": "job-9"})
            if request.url.path == "/v1/jobs/job-9":
                seen.append("poll")
                return httpx.Response(200, json={"status": "succeeded",
                                                 "result_url": "https://media.test/j.mp3"})
            raise AssertionError(f"неожиданный путь: {request.url.path}")

        from service.gateway import generate_audio

        result = generate_audio(
            _entry(), "sk", text="hello", model="tts-1",
            make_provider=self._factory(handler), job_wait=0.0,
        )

        assert result["error"] == ""
        assert result["url"] == "https://media.test/j.mp3"
        assert seen == ["poll"]

    def test_a_failed_job_is_an_honest_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/audio/hello":
                return httpx.Response(202, json={"id": "job-bad"})
            return httpx.Response(200, json={"status": "failed",
                                             "error": "модель упала"})

        from service.gateway import generate_audio

        result = generate_audio(
            _entry(), "sk", text="hello", model="tts-1",
            make_provider=self._factory(handler), job_wait=0.0,
        )

        assert "модель упала" in result["error"]

    def test_a_sync_answer_still_works_when_the_provider_ignores_prefer(self) -> None:
        """Провайдер без jobs отдаёт ответ сразу — путь не меняется."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"ID3mp3",
                                  headers={"content-type": "audio/mpeg",
                                           "Link": '<https://media.test/s.mp3>; rel="enclosure"'})

        from service.gateway import generate_audio

        result = generate_audio(
            _entry(), "sk", text="hello", model="tts-1",
            make_provider=self._factory(handler), job_wait=0.0,
        )

        assert result["url"] == "https://media.test/s.mp3"

    # -------------------------------------------------- обработка звука

    def test_voice_processing_posts_the_file_to_its_endpoint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/audio/voice-changer"
            ctype = request.headers.get("content-type", "")
            assert "multipart/form-data" in ctype
            assert b'name="model"' in request.content
            assert b'name="voice"' in request.content
            assert b'name="file"' in request.content
            return httpx.Response(
                200, content=b"ID3mp3",
                headers={"content-type": "audio/mpeg",
                         "Link": '<https://media.test/changed.mp3>; rel="enclosure"'},
            )

        from service.gateway import process_audio

        result = process_audio(
            _entry(), "sk", action="changer", model="sts-v2", voice="nova",
            audio=b"RIFF....", audio_name="talk.wav",
            make_provider=self._factory(handler),
        )

        assert result["error"] == ""
        assert result["url"] == "https://media.test/changed.mp3"

    def test_voice_processing_needs_file_model_and_known_action(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "b.test" in str(request.url):
                raise AssertionError("запроса к b быть не должно")
            return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

        from service.gateway import process_audio

        factory = self._factory(handler)
        assert "аудио" in process_audio(
            _entry(), "sk", action="isolator", model="m",
            make_provider=factory)["error"]
        assert "модель" in process_audio(
            _entry(), "sk", action="isolator", model="", audio=b"x",
            make_provider=factory)["error"]
        assert "действие" in process_audio(
            _entry(), "sk", action="нет", model="m", audio=b"x",
            make_provider=factory)["error"]
        assert "ключ" in process_audio(
            _entry(), "", action="stem", model="m", audio=b"x",
            make_provider=factory)["error"]

    def test_voice_processing_without_a_link_reports_size(self) -> None:
        """Ссылки нет — честно говорим, что показывать нечего (тело не храним)."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"ID3mp3",
                                  headers={"content-type": "audio/mpeg"})

        from service.gateway import process_audio

        result = process_audio(
            _entry(), "sk", action="isolator", model="m", audio=b"RIFF",
            make_provider=self._factory(handler),
        )

        assert "ссылка не пришла" in result["error"]


