"""GitHub Copilot: device-flow вход и обмен токена — всё офлайн.

Сеть подменяется на httpx.MockTransport: код проверяет, что именно
пристаставка шлёт GitHub (client_id, заголовки обмена) и как честно
переводит отказы на человеческий язык.
"""

import httpx
import pytest

from providers import copilot
from providers.client import Provider, ServerError


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class TestDeviceFlow:
    def test_start_posts_client_id_and_returns_the_code(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["form"] = dict(urllib_parse_qs(request.content))
            return httpx.Response(200, json={
                "device_code": "dc-1",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            })

        flow = copilot.start_device_flow(transport=_transport(handler))

        assert seen["path"] == "/login/device/code"
        assert seen["form"]["client_id"] == [copilot.CLIENT_ID]
        assert flow["user_code"] == "ABCD-1234"
        assert flow["url"] == "https://github.com/login/device"
        assert flow["expires_in"] == 900

    def test_start_translates_a_refusal(self) -> None:
        flow = copilot.start_device_flow(
            transport=_transport(lambda request: httpx.Response(403, json={"message": "нет"}))
        )
        assert "отказал" in flow["error"]
        assert "403" in flow["error"]

    def test_poll_pending_is_not_an_error(self) -> None:
        kind, value = copilot.poll_device_flow(
            "dc-1",
            transport=_transport(
                lambda request: httpx.Response(200, json={"error": "authorization_pending"})
            ),
        )
        assert kind == "pending"
        assert value == ""

    def test_poll_returns_the_token(self) -> None:
        kind, value = copilot.poll_device_flow(
            "dc-1",
            transport=_transport(
                lambda request: httpx.Response(200, json={"access_token": "gho_secret"})
            ),
        )
        assert kind == "done"
        assert value == "gho_secret"

    @pytest.mark.parametrize("error,phrase", [
        ("expired_token", "истёк"),
        ("access_denied", "отклонили"),
    ])
    def test_poll_explains_terminal_errors(self, error: str, phrase: str) -> None:
        kind, value = copilot.poll_device_flow(
            "dc-1",
            transport=_transport(lambda request: httpx.Response(200, json={"error": error})),
        )
        assert kind == "error"
        assert phrase in value

    def test_slow_down_keeps_the_flow_alive_and_asks_for_a_longer_pause(self) -> None:
        """GitHub просит опрашивать реже — это не отказ, а просьба подождать."""
        kind, value = copilot.poll_device_flow(
            "dc-1",
            transport=_transport(
                lambda request: httpx.Response(200, json={"error": "slow_down", "interval": 10})
            ),
        )
        assert kind == "pending"
        assert value == "slow_down"

    def test_poll_without_a_started_flow_says_so(self) -> None:
        assert copilot.poll_device_flow("") == ("error", "вход не начат")


class TestExchange:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        copilot._exchange_cache.clear()
        yield
        copilot._exchange_cache.clear()

    def test_gho_token_is_exchanged_with_vscode_headers(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["editor"] = request.headers.get("editor-version")
            seen["agent"] = request.headers.get("user-agent")
            return httpx.Response(200, json={"token": "short-lived;proxy-ep=x",
                                             "expires_at": 2_000_000_000})

        token = copilot.exchange(
            "gho_abc",
            transport=_transport(handler),
        )

        assert token == "short-lived;proxy-ep=x"
        assert seen["url"].startswith("https://api.github.com/copilot_internal/v2/token")
        assert seen["auth"] == "token gho_abc"
        assert seen["editor"] == copilot.EDITOR_VERSION
        assert seen["agent"] == copilot.EXCHANGE_USER_AGENT

    def test_a_second_use_comes_from_cache(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(200, json={"token": "t1", "expires_at": 2_000_000_000})

        first = copilot.exchange("gho_abc", transport=_transport(handler))
        second = copilot.exchange("gho_abc", transport=_transport(handler))
        assert first == second == "t1"
        assert len(calls) == 1  # второй запрос не ушёл в сеть

    def test_classic_pat_is_refused_with_advice(self) -> None:
        with pytest.raises(copilot.CopilotError) as exc:
            copilot.exchange(
                "ghp_xxx",
                transport=_transport(lambda request: httpx.Response(200, json={})),
            )
        assert "ghp_" in str(exc.value)
        assert "github.com/login/device" in str(exc.value) or "войте" in str(exc.value)

    def test_a_refusal_quotes_github_message(self) -> None:
        with pytest.raises(copilot.CopilotError) as exc:
            copilot.exchange(
                "gho_abc",
                transport=_transport(
                    lambda request: httpx.Response(
                        404, json={"message": "Not Found", "status": 404}
                    )
                ),
            )
        assert "Not Found" in str(exc.value)
        assert "404" in str(exc.value)

    def test_foreign_token_passes_untouched(self) -> None:
        """Не наш формат (уже обменённый токен) — не трогаем и не зовём сеть."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("сеть не должна вызываться")

        assert copilot.exchange("abc;def", transport=_transport(handler)) == "abc;def"

    def test_empty_key_says_what_is_wrong(self) -> None:
        with pytest.raises(copilot.CopilotError):
            copilot.exchange("", transport=_transport(lambda r: httpx.Response(200, json={})))

    def test_auth_headers_are_openai_style(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"token": "t1", "expires_at": 2_000_000_000})

        headers = copilot.auth_headers("gho_abc", transport=_transport(handler))
        assert headers["Authorization"] == "Bearer t1"
        assert headers["Copilot-Integration-Id"] == copilot.INTEGRATION_ID

    def test_auth_headers_without_key_are_empty(self) -> None:
        assert copilot.auth_headers("") == {}


class TestProviderCopilot:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        copilot._exchange_cache.clear()
        yield
        copilot._exchange_cache.clear()

    def test_models_request_uses_the_exchanged_token(self) -> None:
        """Список моделей Copilot: сырой gho_ обменивается, Bearer — новый."""
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.github.com":
                return httpx.Response(200, json={"token": "short", "expires_at": 2_000_000_000})
            seen.append({
                "path": request.url.path,
                "auth": request.headers.get("authorization"),
                "integration": request.headers.get("copilot-integration-id"),
            })
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": "gpt-5"}, {"id": "o4-mini"},
            ]})

        provider = Provider("https://api.githubcopilot.com",
                            transport=_transport(handler))
        models = provider.fetch_models("gho_raw")

        assert [m["name"] for m in models] == ["gpt-5", "o4-mini"]
        assert seen[0]["path"] == "/models"
        assert seen[0]["auth"] == "Bearer short"  # не сырой gho_
        assert seen[0]["integration"] == copilot.INTEGRATION_ID

    def test_chat_forwarding_exchanges_too(self) -> None:
        auths = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.github.com":
                return httpx.Response(200, json={"token": "short", "expires_at": 2_000_000_000})
            auths.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

        provider = Provider("https://api.githubcopilot.com",
                            transport=_transport(handler))
        response = provider.forward_chat(
            b'{"model": "gpt-5", "messages": []}', api_key="gho_raw", stream=False,
        )
        assert response.status_code == 200
        assert auths == ["Bearer short"]

    def test_broken_key_is_a_clear_server_error(self) -> None:
        """Отказ обмена — понятная причина, а не голый 401 от Copilot."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.github.com":
                return httpx.Response(401, json={"message": "Bad credentials"})
            raise AssertionError("запрос к Copilot уходить не должен")

        provider = Provider("https://api.githubcopilot.com",
                            transport=_transport(handler))
        with pytest.raises(ServerError) as exc:
            provider.fetch_models("gho_bad")
        assert "Bad credentials" in str(exc.value)

    def test_ordinary_providers_keep_plain_bearer(self) -> None:
        """Остальные провайдеры не замечают Copilot-логики."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("authorization") == "Bearer sk-1"
            return httpx.Response(200, json={"data": [{"id": "llama3"}]})

        provider = Provider("http://localhost:11434/v1", transport=_transport(handler))
        models = provider.fetch_models("sk-1")
        assert [m["name"] for m in models] == ["llama3"]

    def test_local_server_without_key_still_works(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"data": [{"id": "qwen3"}]})

        provider = Provider("http://localhost:11434/v1", transport=_transport(handler))
        assert provider.fetch_models()[0]["name"] == "qwen3"


def _item(name: str, *, type_: str = "chat", family: str = "", endpoints=None) -> dict:
    """Предмет /models Copilot в минимальной нужной форме."""
    caps: dict = {"type": type_}
    if family:
        caps["family"] = family
    item: dict = {"id": name, "capabilities": caps}
    if endpoints is not None:
        item["supported_endpoints"] = endpoints
    return item


class TestUsableModels:
    """Каталог /models шире тарифа — фильтр показывает только пригодное."""

    @pytest.fixture(autouse=True)
    def _sku(self):
        saved = copilot._last_sku
        copilot._last_sku = ""
        yield
        copilot._last_sku = saved

    def test_the_full_catalog_is_cut_to_chat_usable_models(
        self, monkeypatch,
    ) -> None:
        payload = {"data": [
            _item("gpt-4o", family="gpt-4o"),
            _item("text-embedding-3-small", type_="embeddings"),
            _item("gpt-5.6-luna", family="gpt-5.6-luna", endpoints=["/responses"]),
            _item("claude-haiku-4.5", family="claude-haiku-4.5",
                  endpoints=["/chat/completions", "/v1/messages"]),
        ]}
        monkeypatch.setattr(copilot, "_last_sku", "pro")
        # Без бесплатного тарифа — резаем только эмбеддинги и не-чатовые endpoint'ы.
        assert copilot.usable_models(payload) == [
            "claude-haiku-4.5", "gpt-4o",
        ]

    def test_free_tariff_shows_only_utility_families(self, monkeypatch) -> None:
        monkeypatch.setattr(copilot, "_last_sku", "free_limited_copilot")
        payload = {"data": [
            _item("gpt-4o-mini", family="gpt-4o-mini"),
            _item("gpt-4o-mini-2024-07-18", family="gpt-4o-mini"),
            _item("gpt-4.1", family="gpt-4.1"),
            _item("claude-haiku-4.5", family="claude-haiku-4.5",
                  endpoints=["/chat/completions", "/v1/messages"]),
            _item("mai-code-1-flash-4th", family="vscModelF",
                  endpoints=["/chat/completions", "/responses"]),
            _item("gpt-4", family="gpt-4"),
            _item("text-embedding-ada-002", type_="embeddings"),
        ]}
        assert copilot.usable_models(payload) == ["gpt-4.1", "gpt-4o-mini",
                                                  "gpt-4o-mini-2024-07-18"]

    def test_unknown_tariff_does_not_hide_premium_models(self) -> None:
        """sku неизвестен (обмен не был) — не гадаем, показываем всё чатовое."""
        payload = {"data": [_item("claude-haiku-4.5", family="claude-haiku-4.5",
                                  endpoints=["/chat/completions", "/v1/messages"])]}
        assert copilot.usable_models(payload) == ["claude-haiku-4.5"]

    def test_broken_payload_is_an_empty_list(self) -> None:
        assert copilot.usable_models(None) == []
        assert copilot.usable_models({"data": "мусор"}) == []
        assert copilot.usable_models({"data": ["строка", None, 5]}) == []

    def test_models_without_capabilities_are_kept(self) -> None:
        """Провайдер без capabilities (старый формат) — не потеряем молча."""
        assert copilot.usable_models({"data": [{"id": "llama3"}]}) == ["llama3"]


class TestExplainError:
    """Отказы Copilot — человеческой причиной, а не сырым JSON."""

    @pytest.fixture(autouse=True)
    def _sku(self):
        saved = copilot._last_sku
        copilot._last_sku = ""
        yield
        copilot._last_sku = saved

    _MODEL_400 = ('{"error":{"message":"The requested model is not supported.",'
                  '"code":"model_not_supported","param":"model",'
                  '"type":"invalid_request_error"}}')
    _RATE_429 = "Sorry, you've exceeded your rate limit for utility models."

    def test_model_not_supported_on_free_tariff_names_working_models(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setattr(copilot, "_last_sku", "free_limited_copilot")
        human = copilot.explain_error(400, self._MODEL_400)
        assert "бесплатн" in human
        assert "gpt-4o-mini" in human
        assert "model_not_supported" not in human  # не сыпем кодами в лицо

    def test_model_not_supported_on_paid_tariff_suggests_a_refresh(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setattr(copilot, "_last_sku", "pro")
        human = copilot.explain_error(400, self._MODEL_400)
        assert "вывели из списка" in human or "устарело" in human

    def test_rate_limit_translates_with_retry_after_minutes(self) -> None:
        human = copilot.explain_error(429, self._RATE_429, retry_after="1183")
        assert "лимит" in human.lower()
        assert "20 мин" in human  # 1183 с ≈ 20 мин, с округлением вверх

    def test_unknown_error_stays_empty_so_the_raw_body_shows(self) -> None:
        assert copilot.explain_error(502, "<html>bad gateway</html>") == ""
        assert copilot.explain_error(400, '{"error":{"code":"other"}}') == ""


class TestProviderCopilotFilter:
    """Список моделей уходит из /models уже отфильтрованным по тарифу."""

    @pytest.fixture(autouse=True)
    def _state(self):
        copilot._exchange_cache.clear()
        copilot._last_sku = ""
        yield
        copilot._exchange_cache.clear()
        copilot._last_sku = ""

    def test_fetch_models_offers_only_what_the_tariff_accepts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.github.com":
                # Обмен сообщает тариф: бесплатный каталог шире API.
                return httpx.Response(200, json={"token": "short", "expires_at": 2_000_000_000,
                                                 "sku": "free_limited_copilot"})
            return httpx.Response(200, json={"data": [
                _item("gpt-4o-mini", family="gpt-4o-mini"),
                _item("gpt-4.1", family="gpt-4.1"),
                _item("claude-haiku-4.5", family="claude-haiku-4.5",
                      endpoints=["/chat/completions", "/v1/messages"]),
                _item("mai-code-1-flash-4th", family="vscModelF",
                      endpoints=["/chat/completions", "/responses"]),
                _item("text-embedding-3-small", type_="embeddings"),
            ]})

        provider = Provider("https://api.githubcopilot.com", transport=_transport(handler))
        models = provider.fetch_models("gho_raw")

        assert [m["name"] for m in models] == ["gpt-4.1", "gpt-4o-mini"]

    def test_a_paid_tariff_keeps_the_whole_chat_catalog(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.github.com":
                return httpx.Response(200, json={"token": "short", "expires_at": 2_000_000_000,
                                                 "sku": "pro"})
            return httpx.Response(200, json={"data": [
                _item("gpt-4o-mini", family="gpt-4o-mini"),
                _item("claude-haiku-4.5", family="claude-haiku-4.5",
                      endpoints=["/chat/completions", "/v1/messages"]),
                _item("text-embedding-3-small", type_="embeddings"),
            ]})

        provider = Provider("https://api.githubcopilot.com", transport=_transport(handler))
        models = provider.fetch_models("gho_raw")

        assert [m["name"] for m in models] == ["claude-haiku-4.5", "gpt-4o-mini"]


def urllib_parse_qs(body: bytes) -> dict:
    """Форма x-www-form-urlencoded из тела запроса — для проверки полей."""
    import urllib.parse

    return urllib.parse.parse_qs(body.decode("utf-8"))
