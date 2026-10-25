"""Маршрутизация: кому достаётся запрос и когда стоит переключаться."""

from core.routes import (
    advertised_models,
    enabled,
    is_local_url,
    request_needs,
    route,
    should_fallback,
)


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
        # Свой провайдер первым; его отказ — последний шанс дальше.
        assert route(entries, "gpt-4o-mini") == [
            {"providerId": "openai", "model": "gpt-4o-mini"},
            {"providerId": "ollama", "model": "llama3.2"},
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
        """«ollama/qwen3» — к ollama с моделью qwen3, хоть она и не в каталоге.
        Префикс решает порядок; его отказ — последний шанс дальше."""
        entries = [_entry(), _entry(id="openai", name="OpenAI", model="gpt-4o-mini", fallbackPriority=1)]
        assert route(entries, "ollama/qwen3") == [
            {"providerId": "ollama", "model": "qwen3"},
            {"providerId": "openai", "model": "gpt-4o-mini"},
        ]

    def test_an_unknown_model_falls_back_to_enabled_models(self) -> None:
        """Модели нет ни у кого — пустой ответ хуже чужого: пробуем
        включённые по приоритету."""
        assert route([_entry()], "gpt-9-turbo") == [
            {"providerId": "ollama", "model": "llama3.2"},
        ]

    def test_a_disabled_provider_gets_nothing(self) -> None:
        assert route([_entry(enabled=False)], "llama3.2") == []


class TestFallback:
    def test_overload_auth_faults_quota_and_server_errors_switch_the_provider(self) -> None:
        # 429 — лимит или исчерпанная квота (OpenAI так отвечает за оба),
        # 402 — пустой баланс у провайдеров вроде OpenRouter,
        # 404 — модель неизвестна этому провайдеру, смена безвредна.
        for status in (401, 402, 403, 404, 429, 500, 502, 503, 504):
            assert should_fallback(status) is True

    def test_a_bad_request_stays_where_it_is(self) -> None:
        for status in (400, 413, 422):
            assert should_fallback(status) is False


class TestIsLocalUrl:
    """Локальные адреса — ждём их дольше (холодная загрузка модели)."""

    def test_local_addresses(self) -> None:
        for url in (
            "http://localhost:11434/v1",
            "http://127.0.0.1:1234/v1",
            "http://192.168.0.133:11434/v1",
            "http://10.0.0.5:11434/v1",
            "http://172.20.0.3:11434/v1",
            "http://nas.local:11434/v1",
            "http://[::1]:11434/v1",
        ):
            assert is_local_url(url) is True, url

    def test_internet_addresses(self) -> None:
        for url in (
            "https://api.openai.com/v1",
            "https://8.8.8.8/v1",
            "http://example.com/v1",
        ):
            assert is_local_url(url) is False, url

    def test_garbage_is_not_local(self) -> None:
        assert is_local_url("") is False
        assert is_local_url("не адрес") is False


class TestRequestNeeds:
    """Что просил запрос: картинки, инструменты — ничего особого."""

    def test_images_mean_vision(self) -> None:
        body = {
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": "что на картинке?"},
                    {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                ]}
            ]
        }
        assert request_needs(body) == ("vision",)

    def test_tools_mean_tools(self) -> None:
        assert request_needs({"messages": [], "tools": [{"type": "function"}]}) == ("tools",)
        assert request_needs({"messages": [], "tool_choice": "auto"}) == ("tools",)

    def test_a_plain_request_needs_nothing(self) -> None:
        assert request_needs({"messages": [{"role": "user", "content": "привет"}]}) == ()
        assert request_needs({"messages": []}) == ()
        assert request_needs(None) == ()
        assert request_needs("мусор") == ()


class TestRouteKeepsRequirements:
    """Переключение не теряет требование: vision остаётся vision."""

    def _mixed(self) -> list[dict]:
        return [
            _entry(id="plain", name="Plain", model="m-plain", fallbackPriority=1),
            _entry(
                id="vis", name="Vis", model="m-vis", fallbackPriority=2,
                capabilities="vision",
            ),
            _entry(
                id="both", name="Both", model="m-both", fallbackPriority=3,
                capabilities="vision, tools",
            ),
        ]

    def test_a_vision_chain_puts_capable_first_plain_last(self, tmp_path) -> None:
        """С картинками способные идут первыми, немаркированные — в хвосте.

        Немаркированные не выбрасываются (иначе vision нельзя выучить:
        роутер давал бы картинку только уже помеченным, и ни одна модель
        никогда не доказала бы способность).
        """
        attempts = route(self._mixed(), "m-vis", needs=("vision",))
        assert [item["providerId"] for item in attempts] == ["vis", "both", "plain"]

    def test_an_unnamed_vision_request_ranks_capable_first(self, tmp_path) -> None:
        attempts = route(self._mixed(), "", needs=("vision",))
        assert [item["providerId"] for item in attempts] == ["vis", "both", "plain"]

    def test_an_unknown_named_model_with_images_finds_vision_first(self, tmp_path) -> None:
        attempts = route(self._mixed(), "нет-такой", needs=("vision",))
        assert [item["providerId"] for item in attempts] == ["vis", "both", "plain"]

    def test_tools_chain_prefers_models_with_tools(self, tmp_path) -> None:
        attempts = route(self._mixed(), "", needs=("tools",))
        assert [item["providerId"] for item in attempts] == ["both", "plain", "vis"]

    def test_without_requirements_behaviour_is_unchanged(self) -> None:
        # Названная модель — точные совпадения первыми, дальше последний
        # шанс (в порядке приоритета).
        assert [i["providerId"] for i in route(self._mixed(), "m-vis")] == ["vis", "plain", "both"]
        # Неназванная — все с настроенными моделями.
        assert [i["providerId"] for i in route(self._mixed(), "")] == ["plain", "vis", "both"]

    def test_an_explicit_provider_prefix_is_respected(self) -> None:
        # Явный выбор провайдера («провайдер/своя-модель») — идёт первым,
        # но его отказ продолжает цепочкой; запрос с картинками получает
        # только модели со способностями.
        attempts = route(self._mixed(), "plain/super-new", needs=("vision",))
        assert [i["providerId"] for i in attempts] == ["plain", "vis", "both"]
        assert attempts[0] == {"providerId": "plain", "model": "super-new"}

    def test_a_last_resort_never_feeds_pictures_to_plain_models(self) -> None:
        """Названная текстовая модель с картинкой уходит в хвост цепочки.

        Картинку текстовой не даём: первыми идут vision-модели, названная
        неподходящая — последним шансом (лучше её попытка, чем мёртвая
        ошибка, если vision-моделей не хватит).
        """
        attempts = route(self._mixed(), "m-plain", needs=("vision",))
        assert [i["providerId"] for i in attempts] == ["vis", "both", "plain"]
        assert attempts[-1]["providerId"] == "plain"  # неподходящая — в хвосте

    def test_a_refused_model_goes_last_but_stays_in_chain(self, tmp_path) -> None:
        """Доказавшая отказ от vision уходит в конец цепочки, но не умирает.

        «Просто не заявила vision» — пробуем (вдруг умеет); «доказала
        отказ» — ставим последней, чтобы шанс остался (отказ бывает
        временным), но заведомо неподходящая не шла первой.
        """
        entries = [
            _entry(id="noimg", name="NoImg", model="m-noimg", fallbackPriority=1,
                   _refused={"vision"}),
            _entry(id="vis", name="Vis", model="m-vis", fallbackPriority=2,
                   capabilities="vision"),
        ]

        attempts = route(entries, "noimg/m-noimg", needs=("vision",))

        assert [i["providerId"] for i in attempts] == ["vis", "noimg"]
        assert attempts[0]["model"] == "m-vis"  # подходящая первой
        assert attempts[1]["providerId"] == "noimg"  # отказавшая — в хвосте

    def test_a_refused_model_still_serves_text(self, tmp_path) -> None:
        """Отказ от vision не мешает ей отвечать на текст."""
        entries = [
            _entry(id="noimg", name="NoImg", model="m-noimg", fallbackPriority=1,
                   _refused={"vision"}),
        ]

        attempts = route(entries, "noimg/m-noimg", needs=())

        assert [i["providerId"] for i in attempts] == ["noimg"]
