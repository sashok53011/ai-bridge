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

    def __init__(self, directory: Path, entries: list[dict], handlers: dict, keys: dict | None = None):
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

        self.gateway = Gateway(lambda: entries, provider_keys, provider_factory=factory)
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

    def build(entries, handlers, keys=None) -> _Harness:
        h = _Harness(tmp_path, entries, handlers, keys)
        holders.append(h)
        return h

    yield build
    for h in holders:
        h.close()


class TestModelsEndpoint:
    def test_lists_models_of_enabled_providers(self, harness) -> None:
        h = harness([_entry()], {})
        with httpx.Client(base_url=h.base) as client:
            data = client.get("/v1/models").json()
        assert data["object"] == "list"
        assert data["data"] == [{"id": "llama3.2", "owned_by": "ollama"}]

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


class TestReputation:
    """Репутация: поломки уводят модель на паузу, успех всё прощает."""

    @staticmethod
    def _build(tmp_path, entries, handlers):
        """Гейтвей с репутацией на тестовых данных."""
        from providers.client import Provider
        from storage.reputation import ReputationStore

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            inner = handlers[base]
            return Provider(url, transport=httpx.MockTransport(inner), **kwargs)

        reputation = ReputationStore(tmp_path)
        gateway = Gateway(lambda: entries, KeyStore(tmp_path), provider_factory=factory,
                          reputation=reputation)
        return gateway, reputation

    @staticmethod
    def _ask(gateway, model="llama3.2"):
        return gateway.chat(json.dumps({"model": model}).encode("utf-8"), model)

    @staticmethod
    def _refusal(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": (
                "models/gemini-3.1-flash-live-preview only supports real-time "
                "bidirectional streaming via WebSocket (bidiGenerateContent)."
            )}}
        )

    @staticmethod
    def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "жив"}}]},
            headers={"content-type": "application/json"},
        )

    def test_faults_pause_a_model_and_the_chain_skips_it(self, tmp_path) -> None:
        entries = [
            _entry(id="gemini", name="Gemini", url="https://gemini.test/v1", fallbackPriority=1),
            _entry(id="local", name="Local", url="http://local.test/v1",
                   model="qwen3", fallbackPriority=9),
        ]
        seen: list[str] = []

        def refusal(request: httpx.Request) -> httpx.Response:
            seen.append("gemini")
            return self._refusal(request)

        def ok(request: httpx.Request) -> httpx.Response:
            seen.append("local")
            return self._ok(request)

        gateway, reputation = self._build(
            tmp_path, entries,
            {"https://gemini.test": refusal, "http://local.test": ok},
        )

        # Три запроса: каждый раз gemini отказывает, local подхватывает.
        for _ in range(3):
            status, _, _, provider, _ = self._ask(gateway)
            assert status == 200
            assert provider == "Local"
        assert seen == ["gemini", "local"] * 3
        assert reputation.remaining("gemini/llama3.2") > 0  # третья поломка: пауза

        # Четвёртый запрос: gemini на паузе — цепочка идёт сразу к local.
        self._ask(gateway)
        assert seen[-1] == "local" and "gemini" not in seen[6:]

    def test_a_success_forgives_a_paused_model(self, tmp_path) -> None:
        entries = [_entry()]
        gateway, reputation = self._build(tmp_path, entries, {"http://ollama.test": self._ok})
        for _ in range(3):
            reputation.fault("ollama/llama3.2")
        # Один провайдер — цепочка берёт и наказанную (лучше так, чем мёртвый отказ).
        assert reputation.remaining("ollama/llama3.2") > 0

        status, _, _, provider, _ = self._ask(gateway)

        assert status == 200
        assert provider == "Ollama"
        # Удачный ответ снял наказание само.
        assert reputation.remaining("ollama/llama3.2") == 0

    def test_garbage_instead_of_json_is_a_fault(self, tmp_path) -> None:
        entries = [
            _entry(id="a", name="A", url="https://a.test/v1", fallbackPriority=1),
            _entry(id="b", name="B", url="https://b.test/v1", model="m-b", fallbackPriority=2),
        ]

        def garbage(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content="<html>ой, что-то пошло не так</html>",
                headers={"content-type": "application/json"},
            )

        gateway, reputation = self._build(
            tmp_path, entries, {"https://a.test": garbage, "https://b.test": self._ok}
        )

        status, _, _, provider, _ = self._ask(gateway)

        assert status == 200
        assert provider == "B"  # мусор переключён, не отдан клиенту
        assert reputation.penalized()  # мусорщика записали в штрафники

    def test_a_plain_client_error_is_not_a_fault(self, tmp_path) -> None:
        entries = [_entry()]
        gateway, reputation = self._build(tmp_path, entries, {"http://ollama.test": self._ok})

        for _ in range(5):
            gateway.chat(b"", "")  # пустое тело — вина клиента

        assert reputation.penalized() == []

    def test_a_404_is_a_fault_for_the_model(self, tmp_path) -> None:
        entries = [_entry()]

        def unknown(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"message": "model not found"}})

        gateway, reputation = self._build(tmp_path, entries, {"http://ollama.test": unknown})

        for _ in range(3):
            status, _, _, _, _ = self._ask(gateway)

        assert status == 502  # никто не ответил
        assert reputation.remaining("ollama/llama3.2") > 0


class TestLearnedCapabilities:
    """Способности учатся сами: успех доказывает «умеет», отказ — «не умеет»."""

    @staticmethod
    def _build(tmp_path, entries, handlers, event_log=None):
        """Гейтвей с выученными способностями на тестовых данных."""
        from providers.client import Provider
        from storage.capabilities import CapabilityStore

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            inner = handlers[base]
            return Provider(url, transport=httpx.MockTransport(inner), **kwargs)

        capabilities = CapabilityStore(tmp_path)
        gateway = Gateway(lambda: entries, KeyStore(tmp_path), provider_factory=factory,
                          capabilities=capabilities, event_log=event_log)
        return gateway, capabilities

    @staticmethod
    def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "жив"}}]},
            headers={"content-type": "application/json"},
        )

    @staticmethod
    def _vision_body(model: str = "") -> bytes:
        body: dict = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "что на картинке?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                ],
            }],
        }
        if model:
            body["model"] = model
        return json.dumps(body).encode("utf-8")

    @staticmethod
    def _tools_body(model: str = "") -> bytes:
        body: dict = {
            "messages": [{"role": "user", "content": "привет"}],
            "tools": [{"type": "function", "function": {"name": "ping", "parameters": {}}}],
        }
        if model:
            body["model"] = model
        return json.dumps(body).encode("utf-8")

    def test_a_successful_vision_request_teaches_vision(self, tmp_path) -> None:
        # Провайдер без справки о способностях; человек назвал модель сам.
        entries = [_entry()]
        gateway, capabilities = self._build(tmp_path, entries, {"http://ollama.test": self._ok})

        status, _, _, _, _ = gateway.chat(self._vision_body("llama3.2"), "llama3.2")

        assert status == 200
        assert capabilities.known("ollama/llama3.2") == {"vision": True}
        # Безымянный запрос с картинкой теперь строит цепочку: раньше
        # пустая справка отсекала модель и был 502 «не обслуживается».
        status, _, _, provider, _ = gateway.chat(self._vision_body(), "")
        assert status == 200
        assert provider == "Ollama"

    def test_a_refusal_teaches_not_tools_even_if_handwritten(self, tmp_path) -> None:
        # Ручная справка врёт: «tools» записано, а провайдер отказывает.
        entries = [_entry(capabilities="tools")]
        refuser = httpx.Response(
            400, json={"error": {"message": "This model does not support tools"}}
        )
        gateway, capabilities = self._build(
            tmp_path, entries, {"http://ollama.test": lambda request: refuser}
        )

        status, _, _, _, _ = gateway.chat(self._tools_body("llama3.2"), "llama3.2")

        assert status == 502  # единственная модель отказалась
        # Кто последний, тот и прав: живой отказ стёр рукописное «tools».
        assert capabilities.known("ollama/llama3.2") == {"tools": False}
        taught = gateway._taught_entries(entries)
        assert taught[0]["capabilities"] == ""
        assert entries[0]["capabilities"] == "tools"  # оригинал не тронут

    def test_a_learned_no_is_flipped_back_by_a_success(self, tmp_path) -> None:
        entries = [_entry(capabilities="tools")]

        gateway, capabilities = self._build(
            tmp_path, entries, {"http://ollama.test": self._ok}
        )
        capabilities.learn("ollama/llama3.2", "tools", False)

        status, _, _, _, _ = gateway.chat(self._tools_body("llama3.2"), "llama3.2")

        assert status == 200
        assert capabilities.known("ollama/llama3.2") == {"tools": True}

    def test_a_websocket_refusal_teaches_nothing(self, tmp_path) -> None:
        # «Только через WebSocket» — не про способности: не учим ни vision, ни tools.
        entries = [_entry()]
        refusal = httpx.Response(
            400, json={"error": {"message": (
                "model only supports real-time bidirectional streaming via WebSocket"
            )}}
        )
        gateway, capabilities = self._build(
            tmp_path, entries, {"http://ollama.test": lambda request: refusal}
        )

        gateway.chat(self._vision_body("llama3.2"), "llama3.2")

        assert capabilities.known("ollama/llama3.2") == {}

    def test_a_paraphrased_refusal_is_still_recognised(self, tmp_path) -> None:
        """Отказ другой формулировкой — не «вина клиента», а отказ модели.

        Cohere отвечает «image content is not supported» — ни одна точная
        фраза не совпадает, но широкий путь (отрицание + способность из
        запроса) должна распознать и выучить vision.
        """
        from storage.capabilities import CapabilityStore

        entries = [_entry()]
        refusal = httpx.Response(
            400, json={"error": {"message": (
                "invalid request: image content is not supported for this model"
            )}}
        )
        gateway, capabilities = self._build(
            tmp_path, entries, {"http://ollama.test": lambda request: refusal}
        )

        status, _, _, _, _ = gateway.chat(self._vision_body("llama3.2"), "llama3.2")

        # Отказ распознан: выучено «не умеет vision», а не «вина клиента».
        assert capabilities.known("ollama/llama3.2") == {"vision": False}
        # И журнал говорит «отвергла», не «вина клиента».
        journal = _Journal()
        gateway2, _ = self._build(
            tmp_path, entries, {"http://ollama.test": lambda request: refusal},
            event_log=journal,
        )
        gateway2.chat(self._vision_body("llama3.2"), "llama3.2")
        joined = " ".join(journal.texts("решение"))
        assert "отвергла" in joined
        assert "вина клиента" not in joined

    def test_a_truly_bad_request_is_not_a_refusal(self, tmp_path) -> None:
        """Настояще кривой запрос — не отказ модели, vision не учим."""
        from storage.capabilities import CapabilityStore

        entries = [_entry()]
        bad = httpx.Response(
            400, json={"error": {"message": "invalid temperature: must be between 0 and 2"}}
        )
        gateway, capabilities = self._build(
            tmp_path, entries, {"http://ollama.test": lambda request: bad}
        )

        status, _, _, _, _ = gateway.chat(self._vision_body("llama3.2"), "llama3.2")

        assert status == 400  # отдаём как есть
        assert capabilities.known("ollama/llama3.2") == {}  # не учим


class _Journal:
    """Мини-журнал событий: собирает kind+text для проверки."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def record(self, kind: str, user: str, text: str, **fields) -> dict:
        self.events.append((kind, text))
        return {"kind": kind, "text": text}

    def texts(self, kind: str) -> list[str]:
        return [text for event_kind, text in self.events if event_kind == kind]


class TestCapabilityJournal:
    """Журнал подробно рассказывает, как модель училась — на каждом шаге."""

    @staticmethod
    def _refusal(message: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": message}})

        return handler

    def test_an_unknown_model_learns_vision_and_the_journal_says_so(self, tmp_path) -> None:
        journal = _Journal()
        learn = TestLearnedCapabilities
        gateway, _ = learn._build(
            tmp_path, [_entry()], {"http://ollama.test": learn._ok}, event_log=journal
        )

        gateway.chat(learn._vision_body("llama3.2"), "llama3.2")

        lines = journal.texts("способности")
        assert any("ollama/llama3.2" in line and "выучила vision" in line for line in lines)

    def test_a_repeated_success_confirms_the_fact(self, tmp_path) -> None:
        journal = _Journal()
        learn = TestLearnedCapabilities
        gateway, _ = learn._build(
            tmp_path, [_entry()], {"http://ollama.test": learn._ok}, event_log=journal
        )

        gateway.chat(learn._vision_body("llama3.2"), "llama3.2")
        gateway.chat(learn._vision_body("llama3.2"), "llama3.2")

        lines = journal.texts("способности")
        assert any("подтвердила vision" in line for line in lines)

    def test_a_success_flips_an_old_refusal_and_the_journal_says_so(self, tmp_path) -> None:
        journal = _Journal()
        learn = TestLearnedCapabilities
        gateway, capabilities = learn._build(
            tmp_path, [_entry()], {"http://ollama.test": learn._ok}, event_log=journal
        )
        capabilities.learn("ollama/llama3.2", "vision", False)

        gateway.chat(learn._vision_body("llama3.2"), "llama3.2")

        lines = journal.texts("способности")
        assert any("перевернула" in line and "vision" in line for line in lines)

    def test_a_refusal_names_the_capability_and_the_reason(self, tmp_path) -> None:
        journal = _Journal()
        entries = [_entry(capabilities="tools")]
        learn = TestLearnedCapabilities

        gateway, _ = learn._build(
            tmp_path, entries,
            {"http://ollama.test": self._refusal("This model does not support tools")},
            event_log=journal,
        )
        gateway.chat(learn._tools_body("llama3.2"), "llama3.2")

        lines = journal.texts("способности")
        assert any(
            "не умеет tools" in line and "does not support tool" in line for line in lines
        )

    def test_a_websocket_refusal_is_logged_as_a_channel(self, tmp_path) -> None:
        journal = _Journal()
        learn = TestLearnedCapabilities

        gateway, _ = learn._build(
            tmp_path, [_entry()],
            {"http://ollama.test": self._refusal(
                "only supports real-time bidirectional streaming via WebSocket"
            )},
            event_log=journal,
        )
        gateway.chat(learn._vision_body("llama3.2"), "llama3.2")

        lines = journal.texts("способности")
        assert any("не про способности" in line for line in lines)


class TestCapabilityDiagnostics:
    """Пустой фолбэк объясняется: сколько моделей отсеяно и почему."""

    def test_a_vision_request_reaches_an_unmarked_model(self, tmp_path) -> None:
        # Ни у одной модели нет vision — но немаркированная всё равно
        # пробуется последним шансом (иначе vision выучить невозможно).
        learn = TestLearnedCapabilities
        gateway, _ = learn._build(
            tmp_path, [_entry()], {"http://ollama.test": learn._ok}
        )

        status, _, chunks, _, _ = gateway.chat(learn._vision_body(), "")

        # Оливама ответила — запрос обслужен, а не 502 «нет маршрута».
        assert status == 200

    def test_a_failed_chain_still_reports_the_causes(self, tmp_path) -> None:
        """Названная модель отказала, остальные — не по способностям."""
        entries = [
            _entry(),
            _entry(id="b", name="B", url="https://b.test/v1", model="m-b",
                   fallbackPriority=2),
        ]
        refusal = httpx.Response(
            400, json={"error": {"message": "image_url is not supported"}}
        )
        learn = TestLearnedCapabilities

        def dead(request):
            raise httpx.ConnectError("мертв")

        gateway, _ = learn._build(
            tmp_path, entries,
            {"http://ollama.test": lambda request: refusal,
             "https://b.test": dead},
        )

        status, _, chunks, _, _ = gateway.chat(learn._vision_body("llama3.2"), "llama3.2")

        assert status == 502
        text = b"".join(chunks).decode("utf-8")
        # Причины обоих отказов на месте.
        assert "image_url is not supported" in text
        assert "мертв" in text


class TestModelListsFiltering:
    """Четыре уровня: белый → неизвестный → серый (проснувшийся) → чёрный."""

    @staticmethod
    def _build(tmp_path, entries, handlers, event_log=None, user_dir_for=None):
        """Гейтвей со списками на тестовых данных."""
        from providers.client import Provider
        from storage.lists import ModelLists

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            inner = handlers[base]
            return Provider(url, transport=httpx.MockTransport(inner), **kwargs)

        gateway = Gateway(
            lambda: entries, KeyStore(tmp_path), provider_factory=factory,
            lists=ModelLists(tmp_path), event_log=event_log,
            user_dir_for=user_dir_for,
        )
        return gateway

    @staticmethod
    def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "жив"}}]},
            headers={"content-type": "application/json"},
        )

    def test_a_whitelisted_model_goes_before_a_better_priority(self, tmp_path) -> None:
        from storage.lists import WHITELIST_FILES

        tmp_path.joinpath(WHITELIST_FILES["text"]).write_text(
            "ollama/llama3.2\n", encoding="utf-8"
        )
        entries = [
            _entry(fallbackPriority=1),
            _entry(id="b", name="B", url="https://b.test/v1", model="m-b",
                   fallbackPriority=2),
        ]
        gateway = self._build(
            tmp_path, entries,
            {"http://ollama.test": self._ok, "https://b.test": self._ok},
            event_log=_Journal(),
        )

        status, _, _, provider, _ = gateway.chat(b"", "")

        assert status == 200
        assert provider == "Ollama"  # подтверждённая идёт первой, хотя приоритет ниже

    def test_a_blacklisted_model_is_the_last_resort(self, tmp_path) -> None:
        from storage.lists import BLACKLIST

        tmp_path.joinpath(BLACKLIST).write_text("ollama/llama3.2\n", encoding="utf-8")
        entries = [
            _entry(fallbackPriority=1),
            _entry(id="b", name="B", url="https://b.test/v1", model="m-b",
                   fallbackPriority=2),
        ]
        gateway = self._build(
            tmp_path, entries,
            {"http://ollama.test": self._ok, "https://b.test": self._ok},
            event_log=_Journal(),
        )

        status, _, _, provider, _ = gateway.chat(b"", "")

        assert status == 200
        assert provider == "B"  # сперва неизвестная B

    def test_a_blacklisted_model_serves_when_nothing_else_left(self, tmp_path) -> None:
        """«Пустой ответ хуже чужого»: чёрный берут, если больше некому."""
        from storage.lists import BLACKLIST

        tmp_path.joinpath(BLACKLIST).write_text("llama3.2\n", encoding="utf-8")
        gateway = self._build(
            tmp_path, [_entry()],
            {"http://ollama.test": self._ok},
            event_log=_Journal(),
        )

        status, _, _, provider, _ = gateway.chat(b"", "")

        assert status == 200
        assert provider == "Ollama"

    def test_a_nonempty_whitelist_does_not_starve_the_rest(self, tmp_path) -> None:
        """Белый — первый уровень, а не фильтр: неизвестные доступны."""
        from storage.lists import WHITELIST_FILES

        tmp_path.joinpath(WHITELIST_FILES["text"]).write_text(
            "b/m-b\n", encoding="utf-8"
        )
        entries = [
            _entry(fallbackPriority=1),
            _entry(id="b", name="B", url="https://b.test/v1", model="m-b",
                   fallbackPriority=2),
        ]
        gateway = self._build(
            tmp_path, entries,
            {"http://ollama.test": self._ok, "https://b.test": self._ok},
            event_log=_Journal(),
        )

        status, _, _, provider, _ = gateway.chat(b"", "")

        assert status == 200
        assert provider == "B"  # подтверждённая идёт первой

    def test_empty_lists_change_nothing(self, tmp_path) -> None:
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": self._ok}, event_log=_Journal()
        )

        status, _, _, provider, _ = gateway.chat(b"", "")

        assert status == 200
        assert provider == "Ollama"

    def test_a_sleeping_grey_model_is_skipped(self, tmp_path) -> None:
        from storage.lists import GREYLIST
        from datetime import datetime, timedelta, timezone

        until = datetime.now(timezone.utc) + timedelta(hours=12)
        iso = until.isoformat().replace("+00:00", "Z")
        tmp_path.joinpath(GREYLIST).write_text(
            f"ollama/llama3.2  # до {iso}: лимит · цикл 1\n", encoding="utf-8"
        )
        gateway = self._build(
            tmp_path, [_entry()],
            {"http://ollama.test": self._ok},
            event_log=_Journal(),
            user_dir_for=lambda uid: tmp_path,
        )

        status, _, _, _, _ = gateway.chat(b"", "", "u1")

        assert status == 502  # спит — не обслужит

    def test_an_awakened_grey_model_serves_after_unknown(self, tmp_path) -> None:
        """Проснувшийся серый идёт после неизвестной, но до чёрного."""
        from storage.lists import BLACKLIST, GREYLIST
        from datetime import datetime, timedelta, timezone

        until = datetime.now(timezone.utc) - timedelta(hours=1)
        iso = until.isoformat().replace("+00:00", "Z")
        tmp_path.joinpath(GREYLIST).write_text(
            f"ollama/llama3.2  # до {iso}: лимит · цикл 1\n", encoding="utf-8"
        )
        tmp_path.joinpath(BLACKLIST).write_text("b/m-b\n", encoding="utf-8")
        entries = [
            _entry(fallbackPriority=1),
            _entry(id="b", name="B", url="https://b.test/v1", model="m-b",
                   fallbackPriority=2),
        ]
        gateway = self._build(
            tmp_path, entries,
            {"http://ollama.test": self._ok, "https://b.test": self._ok},
            event_log=_Journal(),
            user_dir_for=lambda uid: tmp_path,
        )

        status, _, _, provider, _ = gateway.chat(b"", "", "u1")

        assert status == 200
        assert provider == "Ollama"  # проснувшийся серый обошёл чёрный B

    def test_a_grey_list_is_per_user(self, tmp_path) -> None:
        """Серый одного человека не мешает другому."""
        from storage.lists import GREYLIST
        from datetime import datetime, timedelta, timezone

        user_dir = tmp_path / "users" / "u1"
        user_dir.mkdir(parents=True)
        until = datetime.now(timezone.utc) + timedelta(hours=12)
        iso = until.isoformat().replace("+00:00", "Z")
        user_dir.joinpath(GREYLIST).write_text(
            f"ollama/llama3.2  # до {iso}: лимит · цикл 1\n", encoding="utf-8"
        )
        gateway = self._build(
            tmp_path, [_entry()],
            {"http://ollama.test": self._ok},
            event_log=_Journal(),
            user_dir_for=lambda uid: user_dir if uid == "u1" else None,
        )

        # u2 не имеет серого списка — модель для него доступна.
        status, _, _, provider, _ = gateway.chat(b"", "u2")

        assert status == 200
        assert provider == "Ollama"

    def test_a_blacklist_hit_is_not_silently_dropped(self, tmp_path) -> None:
        """Чёрный остаётся в цепочке (последний шанс) — не выбрасывается."""
        from storage.lists import BLACKLIST

        tmp_path.joinpath(BLACKLIST).write_text("ollama/llama3.2\n", encoding="utf-8")
        gateway = self._build(
            tmp_path, [_entry()],
            {"http://ollama.test": self._ok},
            event_log=_Journal(),
        )

        tiered = gateway._apply_lists([_entry()], "u1")

        assert tiered and tiered[0]["_tier"] == 4  # помечен уровнем, не удалён


class TestModelDecisions:
    """Решение после каждого запроса: белый / серый / чёрный / не тронуты."""

    @staticmethod
    def _build(tmp_path, entries, handlers, event_log=None, user_dir_for=None,
               rules=None):
        from providers.client import Provider
        from storage.capabilities import CapabilityStore
        from storage.lists import ModelLists

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            return Provider(url, transport=httpx.MockTransport(handlers[base]), **kwargs)

        return Gateway(
            lambda: entries, KeyStore(tmp_path), provider_factory=factory,
            lists=ModelLists(tmp_path), event_log=event_log, user_dir_for=user_dir_for,
            capabilities=CapabilityStore(tmp_path), rules=rules,
        )

    @staticmethod
    def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "жив"}}]},
            headers={"content-type": "application/json"},
        )

    def test_a_success_writes_the_model_to_the_whitelist(self, tmp_path) -> None:
        from storage.lists import ModelLists

        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": self._ok},
            event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        assert "ollama/llama3.2" in ModelLists(tmp_path).whitelist()
        lines = journal.texts("решение")
        assert any("подтвердила себя" in t and "HTTP 200" in t for t in lines)

    def test_a_repeated_success_does_not_spam_the_journal(self, tmp_path) -> None:
        """Уже подтверждённая модель молчит — журнал не захлебнётся."""
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": self._ok},
            event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")
        first = len(journal.texts("решение"))
        gateway.chat(b"", "", "u1")
        gateway.chat(b"", "", "u1")

        assert len(journal.texts("решение")) == first  # новых решений нет

    def test_a_402_goes_to_grey_for_a_day(self, tmp_path) -> None:
        from storage.lists import GREYLIST, ModelLists

        refusal = httpx.Response(
            402, json={"error": {"message": "This request requires more credits"}}
        )
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": lambda r: refusal},
            event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        status, _, _, _, _ = gateway.chat(b"", "", "u1")

        assert status == 502
        grey = ModelLists(tmp_path).grey()
        assert "ollama" in grey  # серый — провайдером, со всеми моделями
        assert grey["ollama"]["cycle"] == 1
        lines = journal.texts("решение")
        assert any("серый список до" in t for t in lines)

    def test_a_sleeping_provider_hides_all_its_models(self, tmp_path) -> None:
        """Спит провайдер — не обслуживается ни одна его модель.

        Регрессия перехода на серый «по провайдеру»: раньше спала одна
        пара «провайдер/модель», теперь — провайдер целиком.
        """
        from storage.lists import GREYLIST, ModelLists

        from datetime import datetime, timedelta, timezone

        until = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        tmp_path.joinpath(GREYLIST).write_text(
            f"ollama  # до {until.replace('+00:00', 'Z')}: лимит · цикл 1\n",
            encoding="utf-8",
        )
        # Провайдер отвечает — но к нему не должны обращаться.
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": self._ok},
            event_log=_Journal(), user_dir_for=lambda uid: tmp_path,
        )

        status, _, chunks, _, _ = gateway.chat(b"", "llama3.2", "u1")

        body = b"".join(chunks).decode("utf-8", errors="replace")
        assert status == 502  # модели спят — некому служить
        assert "не обслуживается" in body
        assert not ModelLists(tmp_path).whitelist()  # и успеха не было

    def test_a_hidden_credit_warning_in_200_is_also_grey(self, tmp_path) -> None:
        """200 со спрятанным «мало кредитов» — мягкий отказ, не чёрный."""
        from storage.lists import GREYLIST, ModelLists

        hidden = httpx.Response(
            200, json={"error": {"message": "insufficient credits balance"}},
            headers={"content-type": "application/json"},
        )
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": lambda r: hidden},
            event_log=_Journal(), user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        assert "ollama" in ModelLists(tmp_path).grey()  # провайдер, не модель

    def test_the_third_grey_cycle_is_black(self, tmp_path) -> None:
        """Три цикла сна подряд — в чёрный список."""
        from storage.lists import BLACKLIST, GREYLIST, ModelLists

        until = "2020-01-01T00:00:00Z"
        tmp_path.joinpath(GREYLIST).write_text(
            f"ollama  # до {until}: лимит · цикл 2\n", encoding="utf-8"
        )
        refusal = httpx.Response(
            402, json={"error": {"message": "rate limit exceeded, try later"}}
        )
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": lambda r: refusal},
            event_log=_Journal(), user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        lists = ModelLists(tmp_path)
        assert "ollama/llama3.2" in lists.blacklist()  # чёрный — по модели
        assert "ollama" not in lists.grey()  # провайдер из серого вышел

    def test_a_hard_failure_is_black_at_once(self, tmp_path) -> None:
        from storage.lists import BLACKLIST, ModelLists

        broken = httpx.Response(500, json={"error": {"message": "upstream died"}})
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": lambda r: broken},
            event_log=_Journal(), user_dir_for=lambda uid: tmp_path,
        )

        status, _, _, _, _ = gateway.chat(b"", "", "u1")

        assert status == 502
        assert "ollama/llama3.2" in ModelLists(tmp_path).blacklist()

    def test_a_client_error_touches_no_lists(self, tmp_path) -> None:
        from storage.lists import ModelLists

        bad = httpx.Response(
            400, json={"error": {"message": "context length exceeded"}}
        )
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": lambda r: bad},
            event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        status, _, _, _, _ = gateway.chat(b"", "", "u1")

        assert status == 400  # неподдерживаемый запрос идёт как есть
        lists = ModelLists(tmp_path)
        assert not lists.whitelist() and not lists.blacklist() and not lists.grey()
        assert any("запрос отклонён" in t for t in journal.texts("решение"))

    def test_a_capability_refusal_touches_no_lists(self, tmp_path) -> None:
        """«Не поддерживает картинки» — списки не трогаем, только учим."""
        from storage.lists import ModelLists

        refusal = httpx.Response(
            400, json={"error": {"message": "image_url is not supported"}}
        )
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": lambda r: refusal},
            event_log=_Journal(), user_dir_for=lambda uid: tmp_path,
        )
        body = json.dumps({
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            ]}],
        }).encode("utf-8")

        gateway.chat(body, "", "u1")

        lists = ModelLists(tmp_path)
        assert not lists.whitelist() and not lists.blacklist() and not lists.grey()

    def test_a_success_returns_a_grey_model_to_the_whitelist(self, tmp_path) -> None:
        from storage.lists import GREYLIST, ModelLists

        until = "2020-01-01T00:00:00Z"
        tmp_path.joinpath(GREYLIST).write_text(
            f"ollama/llama3.2  # до {until}: лимит · цикл 1\n", encoding="utf-8"
        )
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": self._ok},
            event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        lists = ModelLists(tmp_path)
        assert "ollama/llama3.2" in lists.whitelist()  # белый — по модели
        assert "ollama" not in lists.grey()  # провайдер проснулся
        assert any("из серого" in t for t in journal.texts("решение"))

    def test_a_vision_success_lands_in_the_vision_group(self, tmp_path) -> None:
        """Картинка прошла — модель легла в vision-группу, не в text."""
        from storage.lists import ModelLists, WHITELIST_FILES

        learn = TestLearnedCapabilities
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry()], {"http://ollama.test": learn._ok},
            event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(learn._vision_body("llama3.2"), "llama3.2", "u1")

        lists = ModelLists(tmp_path)
        assert lists.whitelist_group("ollama/llama3.2") == "vision"
        assert "ollama/llama3.2" not in lists.whitelist_groups()["text"]
        # Событие несёт группу.
        assert any("группа vision" in t for t in journal.texts("решение"))

    def test_a_second_success_regroups_the_model(self, tmp_path) -> None:
        """Была text, доказала vision — переложена, событие об этом есть."""
        from storage.lists import ModelLists

        lists0 = ModelLists(tmp_path)
        lists0.whitelist_place("ollama/llama3.2", "text", "подтверждена успехом")
        learn = TestLearnedCapabilities
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry(capabilities="")], {"http://ollama.test": learn._ok},
            event_log=journal, user_dir_for=lambda uid: tmp_path,
        )
        # Выученный vision уже есть (модель однажды видела картинку).
        from storage.capabilities import CapabilityStore
        CapabilityStore(tmp_path).learn("ollama/llama3.2", "vision", True)

        gateway.chat(learn._vision_body("llama3.2"), "llama3.2", "u1")

        assert ModelLists(tmp_path).whitelist_group("ollama/llama3.2") == "vision"
        assert any("text → vision" in t for t in journal.texts("решение"))


class TestRuleEngine:
    """Правило админа сильнее встроенного решения по статусу.

    Строка в ответе провайдера → выбранное действие (серый / чёрный /
    белый / переключить / не трогать / выучить). Срабатывание видно в
    журнале со строкой-основанием.
    """

    @staticmethod
    def _build(tmp_path, entries, handlers, rules, event_log=None,
               user_dir_for=None):
        from providers.client import Provider
        from storage.capabilities import CapabilityStore
        from storage.lists import ModelLists

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            return Provider(url, transport=httpx.MockTransport(handlers[base]), **kwargs)

        return Gateway(
            lambda: entries, KeyStore(tmp_path), provider_factory=factory,
            lists=ModelLists(tmp_path), event_log=event_log,
            user_dir_for=user_dir_for, capabilities=CapabilityStore(tmp_path),
            rules=rules,
        )

    @staticmethod
    def _rules(tmp_path, response=None, request=None):
        from storage.rules import RuleStore

        store = RuleStore(tmp_path)
        for rule in response or []:
            store.add("response", text=rule.get("text", ""),
                      action=rule["action"],
                      provider=rule.get("provider", "ollama"),
                      req=rule.get("req", ""),
                      status=rule.get("status"))
        for rule in request or []:
            store.add("request", text=rule["text"], action=rule["action"])
        return store

    @staticmethod
    def _reply(text: str, status: int = 200):
        import httpx as _httpx

        def handler(request):
            return _httpx.Response(
                status, json={"error": {"message": text}},
                headers={"content-type": "application/json"},
            )

        return {"http://ollama.test": handler}

    def test_a_rule_overrides_a_soft_status(self, tmp_path) -> None:
        """429 (встроенное — серый) + правило «чёрный» → чёрный."""
        from storage.lists import ModelLists

        rules = self._rules(tmp_path, response=[
            {"text": "rate limited", "action": "black"},
        ])
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry()], self._reply("rate limited, try later", 429),
            rules, event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        status, _, _, _, _ = gateway.chat(b"", "", "u1")

        assert status == 502
        lists = ModelLists(tmp_path)
        assert "ollama/llama3.2" in lists.blacklist()  # правило сильнее
        assert "ollama" not in lists.grey()  # встроенный серый не применился
        assert any("правило" in t and "rate limited" in t
                   for t in journal.texts("правило"))

    def test_a_rule_can_send_a_provider_to_grey(self, tmp_path) -> None:
        from storage.lists import ModelLists

        rules = self._rules(tmp_path, response=[
            {"text": "закончились кредиты", "action": "grey"},
        ])
        gateway = self._build(
            tmp_path, [_entry()], self._reply("закончились кредиты у аккаунта", 500),
            rules, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        assert "ollama" in ModelLists(tmp_path).grey()  # 500 → серый по правилу

    def test_a_rule_can_leave_the_lists_alone(self, tmp_path) -> None:
        """Не трогать: 429 не кладёт ни в серый, ни в чёрный — только журнал."""
        from storage.lists import ModelLists

        rules = self._rules(tmp_path, response=[
            {"text": "maintenance window", "action": "untouched"},
        ])
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry()], self._reply("maintenance window tonight", 429),
            rules, event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        lists = ModelLists(tmp_path)
        assert not lists.grey() and not lists.blacklist()
        assert any("списки не тронуты" in t for t in journal.texts("правило"))

    def test_a_rule_in_a_200_forces_a_switch(self, tmp_path) -> None:
        """Строка в успешном ответе («maintenance») → уходим к следующему."""
        entries = [
            _entry(),
            _entry(id="groq", name="Groq", url="http://groq.test/v1",
                   model="llama-3", fallbackPriority=10),  # меньше = раньше
        ]
        rules = self._rules(tmp_path, response=[
            {"text": "maintenance", "action": "switch", "provider": "ollama"},
        ])
        calls = []

        def ollama(request):
            calls.append("ollama")
            import httpx as _httpx
            return _httpx.Response(
                200, json={"choices": [{"message": {"content": "maintenance soon"}}]},
                headers={"content-type": "application/json"},
            )

        def groq(request):
            calls.append("groq")
            import httpx as _httpx
            return _httpx.Response(
                200, json={"choices": [{"message": {"content": "жив"}}]},
                headers={"content-type": "application/json"},
            )

        gateway = self._build(
            tmp_path, entries,
            {"http://ollama.test": ollama, "http://groq.test": groq},
            rules,
        )

        status, _, chunks, provider, model = gateway.chat(b"", "", "u1")

        assert status == 200
        assert provider == "Groq"  # ушли от maintenance к следующему
        assert calls == ["ollama", "groq"]

    def test_a_rule_matches_case_insensitively(self, tmp_path) -> None:
        from storage.lists import ModelLists

        rules = self._rules(tmp_path, response=[
            {"text": "QUOTA EXCEEDED", "action": "black"},
        ])
        gateway = self._build(
            tmp_path, [_entry()], self._reply("quota exceeded for account", 403),
            rules, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        assert "ollama/llama3.2" in ModelLists(tmp_path).blacklist()

    def test_a_rule_for_another_provider_is_ignored(self, tmp_path) -> None:
        """Правило живёт у своего провайдера: чужой не трогает."""
        from storage.lists import ModelLists

        rules = self._rules(tmp_path, response=[
            {"text": "rate limited", "action": "black", "provider": "groq"},
        ])
        gateway = self._build(
            tmp_path, [_entry()], self._reply("rate limited", 429),
            rules, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        lists = ModelLists(tmp_path)
        assert "ollama/llama3.2" not in lists.blacklist()  # чужое правило молчит
        assert "ollama" in lists.grey()  # встроенный 429 → серый

    def test_no_rules_falls_back_to_the_builtin_decisions(self, tmp_path) -> None:
        from storage.lists import ModelLists

        gateway = self._build(
            tmp_path, [_entry()], self._reply("rate limited", 429),
            None, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        assert "ollama" in ModelLists(tmp_path).grey()  # встроенное 429 → серый

    def test_all_matching_rules_fire_not_just_the_first(self, tmp_path) -> None:
        """Одна строка в двух правилах — срабатывают оба.

        Например: выучить «умеет картинки» и сразу в белый список.
        """
        from storage.capabilities import CapabilityStore
        from storage.lists import ModelLists

        rules = self._rules(tmp_path, response=[
            {"text": "картинка принята", "action": "can-vision"},
            {"text": "картинка принята", "action": "white"},
        ])
        journal = _Journal()
        gateway = self._build(
            tmp_path, [_entry(capabilities="")],
            self._reply("картинка принята и обработана", 200),
            rules, event_log=journal, user_dir_for=lambda uid: tmp_path,
        )

        gateway.chat(b"", "", "u1")

        known = CapabilityStore(tmp_path).known("ollama/llama3.2")
        assert known.get("vision") is True  # первое правило выучило
        assert "ollama/llama3.2" in ModelLists(tmp_path).whitelist()  # второе — в белый
        fired = journal.texts("правило")
        assert any("умеет картинки" in t for t in fired)
        assert any("в белый список" in t for t in fired)

    def test_a_composite_condition_needs_the_request_string(self, tmp_path) -> None:
        """Строка в запросе + статус: без строки запроса правило молчит."""
        from storage.capabilities import CapabilityStore

        rules = self._rules(tmp_path, response=[
            {"text": "ok", "action": "can-vision", "req": "image_url",
             "status": 200, "provider": "ollama"},
        ])
        journal = _Journal()

        # Запрос БЕЗ картинки — условие не совпало, ничего не выучено.
        gateway = self._build(
            tmp_path, [_entry(capabilities="")],
            self._reply("ok, nothing special", 200),
            rules, event_log=journal, user_dir_for=lambda uid: tmp_path,
        )
        gateway.chat(b"", "", "u1")
        assert CapabilityStore(tmp_path).known("ollama/llama3.2") == {}

        # Запрос С картинкой — условие совпало, выучено «умеет».
        rules2 = self._rules(tmp_path.__class__(tmp_path / "two"), response=[
            {"text": "ok", "action": "can-vision", "req": "image_url",
             "status": 200, "provider": "ollama"},
        ])
        gateway2 = self._build(
            tmp_path / "two", [_entry(capabilities="")],
            self._reply("ok, image accepted", 200),
            rules2, user_dir_for=lambda uid: tmp_path / "two",
        )
        body = json.dumps({"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]}]}).encode("utf-8")
        gateway2.chat(body, "", "u1")
        known = CapabilityStore(tmp_path / "two").known("ollama/llama3.2")
        assert known.get("vision") is True

    def test_learn_cant_sends_the_request_further(self, tmp_path) -> None:
        """«Не умеет картинки» — уходим к следующему, ответ не отдаём."""
        entries = [
            _entry(),
            _entry(id="groq", name="Groq", url="http://groq.test/v1",
                   model="llama-3", fallbackPriority=10),
        ]
        rules = self._rules(tmp_path, response=[
            {"text": "не вижу картинки", "action": "cant-vision"},
        ])
        calls = []

        def ollama(request):
            calls.append("ollama")
            import httpx as _httpx
            return _httpx.Response(
                200, json={"choices": [{"message": {"content": "не вижу картинки, извини"}}]},
                headers={"content-type": "application/json"},
            )

        def groq(request):
            calls.append("groq")
            import httpx as _httpx
            return _httpx.Response(
                200, json={"choices": [{"message": {"content": "вижу"}}]},
                headers={"content-type": "application/json"},
            )

        gateway = self._build(
            tmp_path, entries,
            {"http://ollama.test": ollama, "http://groq.test": groq},
            rules,
        )

        status, _, _, provider, _ = gateway.chat(b"", "", "u1")

        assert status == 200
        assert provider == "Groq"  # «не умеет» → ушли дальше
        assert calls == ["ollama", "groq"]

    def test_learn_can_keeps_the_answer(self, tmp_path) -> None:
        """«Умеет картинки» — выучили и отдаём ответ клиенту как есть."""
        from storage.capabilities import CapabilityStore

        rules = self._rules(tmp_path, response=[
            {"text": "принято", "action": "can-vision"},
        ])
        gateway = self._build(
            tmp_path, [_entry(capabilities="")],
            self._reply("принято к сведению", 200),
            rules, user_dir_for=lambda uid: tmp_path,
        )

        status, _, chunks, _, _ = gateway.chat(b"", "", "u1")

        assert status == 200  # ответ не уходит дальше, а отдаётся
        body = b"".join(chunks).decode("utf-8", errors="replace")
        assert "принято" in body
        known = CapabilityStore(tmp_path).known("ollama/llama3.2")
        assert known.get("vision") is True


class TestJournalBodiesAlways:
    """Тела запроса и ответа пишутся в журнал всегда — при успехе и при ошибке."""

    def test_an_error_records_the_request_body_in_the_decision(self, tmp_path) -> None:
        from service.eventlog import EventLog
        from storage.usertokens import UserTokens

        tokens = UserTokens(tmp_path)
        token = tokens.create("u1", "телефон")
        journal = EventLog(tmp_path)
        gateway = TestModelDecisions._build(
            tmp_path, [_entry()], {"http://ollama.test": lambda r: httpx.Response(
                402, json={"error": {"message": "rate limited"}},
            )},
            event_log=journal, user_dir_for=lambda uid: tmp_path,
        )
        status, _, _, _, _ = gateway.chat(
            json.dumps({"messages": [{"role": "user", "content": "тестовый запрос"}]},
                       ensure_ascii=False).encode("utf-8"),
            "", "u1",
        )
        assert status == 502
        decisions = [e for e in journal.recent() if e["kind"] == "решение"]
        assert decisions, "решение не записано"
        assert any("req" in e for e in decisions), "тело запроса не попало в решение"
        assert any("тестовый запрос" in e.get("req", "") for e in decisions)


class TestAutotestBodies:
    """Автотесты: полные тела без обрезки и отметка времени у каждого теста."""

    @staticmethod
    def _build(tmp_path, handlers, autotest_store=None):
        from providers.client import Provider
        from storage.capabilities import CapabilityStore
        from storage.lists import ModelLists

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            return Provider(url, transport=httpx.MockTransport(handlers[base]), **kwargs)

        return Gateway(
            lambda: [_entry()], KeyStore(tmp_path), provider_factory=factory,
            lists=ModelLists(tmp_path), capabilities=CapabilityStore(tmp_path),
            autotest_store=autotest_store,
        )

    def test_a_long_answer_is_kept_whole(self, tmp_path) -> None:
        """Ответ длиннее 4000 знаков не обрезается (ни в теле, ни в тесте)."""
        long_text = "д" * 9000

        def handler(request):
            return httpx.Response(
                200, json={"choices": [{"message": {"content": long_text}}]},
                headers={"content-type": "application/json"},
            )

        # Подменяем fetch_models, чтобы не ходить в сеть.
        from providers.client import Provider

        original = Provider.fetch_models

        def fake_models(self, key, timeout=None):
            return [{"name": "m-1"}]

        Provider.fetch_models = fake_models
        try:
            gateway = self._build(tmp_path, {"http://ollama.test": handler})
            result = gateway.run_autotest("ollama", "", "привет", ["text"])
        finally:
            Provider.fetch_models = original

        row = result["results"][0]
        assert row["status"] == "ok"
        assert len(row["resp"]) > 4000  # тело сохранено целиком
        assert "ts" in row and row["ts"]  # время каждого теста

    def test_no_token_limit_is_sent(self, tmp_path) -> None:
        seen: dict = {}

        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ок"}}]},
                headers={"content-type": "application/json"},
            )

        from providers.client import Provider

        original = Provider.fetch_models

        def fake_models(self, key, timeout=None):
            return [{"name": "m-1"}]

        Provider.fetch_models = fake_models
        try:
            gateway = self._build(tmp_path, {"http://ollama.test": handler})
            gateway.run_autotest("ollama", "", "привет", ["text"])
        finally:
            Provider.fetch_models = original

        assert "max_tokens" not in seen  # лимита по токенам нет

    def test_an_empty_prompt_is_refused(self, tmp_path) -> None:
        gateway = self._build(tmp_path, {"http://ollama.test": lambda r: httpx.Response(
            200, json={"choices": [{"message": {"content": "ок"}}]},
            headers={"content-type": "application/json"},
        )})

        result = gateway.run_autotest("ollama", "", "   ", ["text"])

        assert "error" in result
        assert result["results"] == []

    def test_stop_breaks_the_run_after_the_current_test(self, tmp_path) -> None:
        """Запрос остановки прерывает прогон, частичные результаты остаются."""
        from storage.autotest import AutotestStore

        store = AutotestStore(tmp_path)
        store.begin("ollama")
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                store.request_stop()  # пользователь нажал «остановить»
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ок"}}]},
                headers={"content-type": "application/json"},
            )

        from providers.client import Provider

        original = Provider.fetch_models

        def fake_models(self, key, timeout=None):
            return [{"name": "m-1"}, {"name": "m-2"}, {"name": "m-3"}]

        Provider.fetch_models = fake_models
        try:
            gateway = self._build(tmp_path, {"http://ollama.test": handler},
                                  autotest_store=store)
            result = gateway.run_autotest("ollama", "", "привет", ["text"])
        finally:
            Provider.fetch_models = original

        assert result.get("stopped") is True
        assert len(result["results"]) == 1  # только первый тест успел
        assert calls["n"] == 1  # остальные не запускались
        assert store.status()["running"] is False  # прогон завершён
        assert len(store.load()["results"]) == 1  # частичный результат сохранён


class TestGroupRouting:
    """Запрос своей группы идёт первым; текст не достаётся vision-модели.

    Сортировка внутри уровня: точное совпадение (_fit=0) → надмножество
    (_fit=1). Уровень (_tier) — белый(1) по-прежнему выше неизвестного(2).
    """

    @staticmethod
    def _ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "жив"}}]},
            headers={"content-type": "application/json"},
        )

    @staticmethod
    def _build(tmp_path, entries, handlers):
        from providers.client import Provider
        from storage.capabilities import CapabilityStore
        from storage.lists import ModelLists

        def factory(url: str, **kwargs) -> Provider:
            base = url.split("/v1")[0]
            inner = handlers[base]
            return Provider(url, transport=httpx.MockTransport(inner), **kwargs)

        return Gateway(
            lambda: entries, KeyStore(tmp_path), provider_factory=factory,
            lists=ModelLists(tmp_path), capabilities=CapabilityStore(tmp_path),
        )

    def test_text_request_prefers_the_text_group(self) -> None:
        """Текстовый запрос: текстовая группа первой, vision — надмножество."""
        entries = [
            {"id": "a", "name": "A", "model": "m-a", "enabled": True,
             "capabilities": "vision, tools", "fallbackPriority": 1},
            {"id": "b", "name": "B", "model": "m-b", "enabled": True,
             "capabilities": "", "fallbackPriority": 2},
        ]
        text_first = sorted(entries, key=lambda e: Gateway._fit_of(e, set()))

        assert text_first[0]["id"] == "b"  # точное совпадение (текст) впереди

    def test_vision_request_prefers_vision_group(self) -> None:
        entries = [
            {"id": "a", "name": "A", "model": "m-a", "enabled": True,
             "capabilities": "vision, tools", "fallbackPriority": 1},
            {"id": "b", "name": "B", "model": "m-b", "enabled": True,
             "capabilities": "vision", "fallbackPriority": 2},
            {"id": "c", "name": "C", "model": "m-c", "enabled": True,
             "capabilities": "", "fallbackPriority": 3},
        ]
        needed = {"vision"}
        fits = {e["id"]: Gateway._fit_of(e, needed) for e in entries}

        assert fits["b"] == 0  # ровно vision — точное совпадение
        assert fits["a"] == 1  # vision+tools — надмножество, дороже
        assert fits["c"] == 2  # vision нет

    def test_tools_request_prefers_tools_group(self) -> None:
        entries = [
            {"id": "a", "name": "A", "model": "m-a", "enabled": True,
             "capabilities": "vision, tools"},
            {"id": "b", "name": "B", "model": "m-b", "enabled": True,
             "capabilities": "tools"},
        ]
        needed = {"tools"}
        fits = {e["id"]: Gateway._fit_of(e, needed) for e in entries}

        assert fits["b"] == 0
        assert fits["a"] == 1

    def test_full_needs_want_vision_tools_exactly(self) -> None:
        entry = {"id": "a", "name": "A", "model": "m-a",
                 "capabilities": "vision, tools"}
        entry_only_vision = {"id": "b", "name": "B", "model": "m-b",
                             "capabilities": "vision"}

        assert Gateway._fit_of(entry, {"vision", "tools"}) == 0
        assert Gateway._fit_of(entry_only_vision, {"vision", "tools"}) == 2

    def test_a_whitelisted_text_model_beats_a_vision_one_on_text(self) -> None:
        """Сквозной порядок: белый, но внутри — точное совпадение первым."""
        from storage.lists import WHITELIST_FILES

        tmp = __import__("tempfile").mkdtemp()
        from pathlib import Path
        tmp_path = Path(tmp)
        tmp_path.joinpath(WHITELIST_FILES["text"]).write_text("b/m-b\n", encoding="utf-8")
        tmp_path.joinpath(WHITELIST_FILES["vision"]).write_text("a/m-a\n", encoding="utf-8")
        entries = [
            {"id": "a", "name": "A", "url": "https://a.test/v1", "model": "m-a",
             "enabled": True, "capabilities": "vision", "fallbackPriority": 1},
            {"id": "b", "name": "B", "url": "https://b.test/v1", "model": "m-b",
             "enabled": True, "capabilities": "", "fallbackPriority": 2},
        ]
        handlers = {"https://a.test": self._ok, "https://b.test": self._ok}
        gateway = self._build(tmp_path, entries, handlers)

        status, _, _, provider, _ = gateway.chat(b"", "")

        assert status == 200
        assert provider == "B"  # обе в белом, но текстовый запрос → текстовая группа


class TestRequestRules:
    """Правила запроса админа: строка в запросе → нужная способность.

    Встроенные признаки (image_url / tools) плюс строки из правил:
    так админ научает приставку новым маркерам картинок или инструментов.
    """

    @staticmethod
    def _rules(tmp_path, request):
        from storage.rules import RuleStore

        store = RuleStore(tmp_path)
        for rule in request:
            store.add("request", text=rule["text"], action=rule["action"])
        return store

    def test_builtin_markers_still_work(self, tmp_path) -> None:
        body = json.dumps({"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]}]}).encode("utf-8")

        assert _needs_of(body) == ("vision",)

    def test_a_rule_marks_a_custom_string_as_vision(self, tmp_path) -> None:
        """Строка админа («нарисуй») в запросе требует vision-модель."""
        rules = self._rules(tmp_path, [{"text": "нарисуй", "action": "vision"}])
        body = json.dumps({"messages": [{"role": "user", "content": "нарисуй кота"}]},
                          ensure_ascii=False).encode("utf-8")

        assert _needs_of(body, rules) == ("vision",)

    def test_a_rule_marks_a_custom_string_as_tools(self, tmp_path) -> None:
        rules = self._rules(tmp_path, [{"text": "вызови функцию", "action": "tools"}])
        body = json.dumps({"messages": [{"role": "user", "content": "вызови функцию"}]},
                          ensure_ascii=False).encode("utf-8")

        assert _needs_of(body, rules) == ("tools",)

    def test_rules_add_to_the_builtin_needs(self, tmp_path) -> None:
        """Правило дополняет встроенный признак, а не заменяет."""
        rules = self._rules(tmp_path, [{"text": "добавь инструмент", "action": "tools"}])
        body = json.dumps({"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "text", "text": "добавь инструмент"},
        ]}]}).encode("utf-8")

        assert set(_needs_of(body, rules)) == {"vision", "tools"}

    def test_a_nonmatching_rule_changes_nothing(self, tmp_path) -> None:
        rules = self._rules(tmp_path, [{"text": "нарисуй", "action": "vision"}])
        body = json.dumps({"messages": [{"role": "user", "content": "привет"}]},
                          ensure_ascii=False).encode("utf-8")

        assert _needs_of(body, rules) == ()

    def test_no_rules_falls_back_to_the_builtins(self, tmp_path) -> None:
        body = json.dumps({"messages": [{"role": "user", "content": "привет"}]},
                          ensure_ascii=False).encode("utf-8")

        assert _needs_of(body, None) == ()


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


