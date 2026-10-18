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
from service.gateway import Gateway, _Handler, _Server, _hidden_error
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

    def test_a_named_model_never_leaves_to_a_stranger(self, harness) -> None:
        """Провайдер с другой моделью чужой запрос не подхватывает."""
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

        # Названную модель обслуживает только ollama — молчаливого подмена
        # была бы изменой просьбы клиента.
        assert response.status_code == 502
        assert [request.url.host for request in h.seen] == ["ollama.test"]

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

    def test_an_unavailable_model_names_what_is_available(self, harness) -> None:
        h = harness([_entry()], {})
        payload = json.dumps({"model": "gpt-9-turbo"})
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=payload)

        assert response.status_code == 502
        error = response.json()["error"]
        assert error["code"] == "model_not_available"
        assert "llama3.2" in error["message"]

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
    def test_the_answer_comes_from_the_message_with_a_sane_budget(self) -> None:
        fake = _FakeModel({"choices": [{"message": {"content": "работает"}}]})

        result = run_test_chat(_entry(), "sk", make_provider=lambda url, **kw: fake)

        assert result["answer"] == "работает"
        assert result["error"] == ""
        # Запас на «размышления» thinking-моделей: 16 токенов были мало.
        assert fake.seen["max_tokens"] >= 100

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

    def test_a_vision_request_never_lands_on_a_plain_model(self, harness) -> None:
        def boom(req):
            raise httpx.ConnectError("оба мертвы")

        h = harness(
            self._mixed(),
            {
                "https://vis.test": lambda req: httpx.Response(
                    429, json={"error": {"message": "quota"}}
                ),
                "https://both.test": boom,
            },
        )
        with httpx.Client(base_url=h.base) as client:
            response = client.post("/v1/chat/completions", content=self.VISION_BODY)

        # Plain-модель в цепочку vision-запроса не попала: видно по причинам.
        assert response.status_code == 502
        assert "Plain" not in response.text

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
    """Кнопка «тела запросов»: тело пишется только когда включена."""

    def _chat_event(self, tmp_path, *, bodies: bool) -> dict:
        from service.eventlog import EventLog
        from storage.usertokens import UserTokens

        log = EventLog(tmp_path)
        log.bodies = bodies
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

    def test_bodies_off_means_no_bodies(self, tmp_path) -> None:
        event = self._chat_event(tmp_path, bodies=False)
        assert "req" not in event

    def test_bodies_on_records_a_fragment(self, tmp_path) -> None:
        event = self._chat_event(tmp_path, bodies=True)
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
