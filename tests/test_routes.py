"""Маршрутизация: кому достаётся запрос и когда стоит переключаться."""

from core.routes import advertised_models, enabled, route, should_fallback


def _entry(**kwargs) -> dict:
    base = {
        "id": "ollama",
        "name": "Ollama",
        "url": "http://localhost:11434/v1",
        "model": "llama3.2",
        "fallbackPriority": 6,
        "enabled": True,
    }
    base.update(kwargs)
    return base


class TestEnabled:
    def test_only_switched_on_go_to_work(self) -> None:
        entries = [_entry(), _entry(id="openai", name="OpenAI", enabled=False)]
        assert [entry["id"] for entry in enabled(entries)] == ["ollama"]

    def test_priority_decides_the_order(self) -> None:
        entries = [
            _entry(id="slow", url="https://s.example/v1", fallbackPriority=5),
            _entry(id="fast", url="https://f.example/v1", fallbackPriority=1),
        ]
        assert [entry["id"] for entry in enabled(entries)] == ["fast", "slow"]


class TestAdvertisedModels:
    def test_configured_models_of_enabled_providers(self) -> None:
        models = advertised_models([_entry(), _entry(id="openai", name="OpenAI", model="gpt-4o-mini", enabled=False)])
        assert models == [{"id": "llama3.2", "owned_by": "ollama"}]

    def test_a_duplicated_model_belongs_to_the_better_priority(self) -> None:
        entries = [
            _entry(id="b", name="B", model="shared", fallbackPriority=9),
            _entry(id="a", name="A", model="shared", fallbackPriority=1),
        ]
        assert advertised_models(entries) == [{"id": "shared", "owned_by": "a"}]

    def test_an_unconfigured_model_is_not_advertised(self) -> None:
        assert advertised_models([_entry(model="")]) == []


class TestRoute:
    def test_nothing_enabled_means_nothing_to_try(self) -> None:
        assert route([_entry(enabled=False)], "llama3.2") == []
        assert route([], "") == []

    def test_a_named_model_goes_to_its_provider(self) -> None:
        entries = [
            _entry(),
            _entry(id="openai", name="OpenAI", model="gpt-4o-mini", fallbackPriority=1),
        ]
        assert route(entries, "gpt-4o-mini") == [
            {"providerId": "openai", "model": "gpt-4o-mini"},
        ]

    def test_an_unnamed_model_goes_to_the_best_provider(self) -> None:
        entries = [
            _entry(id="slow", url="https://s.example/v1", model="m-slow", fallbackPriority=5),
            _entry(id="fast", url="https://f.example/v1", model="m-fast", fallbackPriority=1),
        ]
        assert route(entries) == [
            {"providerId": "fast", "model": "m-fast"},
            {"providerId": "slow", "model": "m-slow"},
        ]

    def test_a_model_with_a_provider_prefix_still_matches_by_name(self) -> None:
        """У openrouter модель «anthropic/claude-…» — это имя модели.
        Но суффиксное сравнение делает совпадением и провайдера anthropic
        с моделью claude-sonnet-4 — оба законные кандидаты, и приоритет
        решает, кто первый."""
        entries = [
            _entry(id="anthropic", name="Anthropic", model="claude-sonnet-4"),
            _entry(id="openrouter", name="OpenRouter", model="anthropic/claude-sonnet-4", fallbackPriority=9),
        ]
        assert route(entries, "anthropic/claude-sonnet-4") == [
            {"providerId": "anthropic", "model": "claude-sonnet-4"},
            {"providerId": "openrouter", "model": "anthropic/claude-sonnet-4"},
        ]

    def test_a_prefix_can_pin_a_provider_for_any_model(self) -> None:
        """«ollama/qwen3» — к ollama с моделью qwen3, хоть она и не в каталоге."""
        entries = [_entry(), _entry(id="openai", name="OpenAI", model="gpt-4o-mini", fallbackPriority=1)]
        assert route(entries, "ollama/qwen3") == [{"providerId": "ollama", "model": "qwen3"}]

    def test_an_unknown_model_has_no_route(self) -> None:
        assert route([_entry()], "gpt-9-turbo") == []

    def test_a_disabled_provider_gets_nothing(self) -> None:
        assert route([_entry(enabled=False)], "llama3.2") == []


class TestFallback:
    def test_overload_and_server_faults_switch_the_provider(self) -> None:
        for status in (429, 500, 502, 503, 504):
            assert should_fallback(status) is True

    def test_a_bad_request_stays_where_it_is(self) -> None:
        for status in (400, 401, 403, 404, 413, 422):
            assert should_fallback(status) is False
