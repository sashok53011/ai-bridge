"""Адреса провайдеров: у списка моделей и у нативного API разные корни."""

import pytest

from providers.client import Provider, ServerError, _auth_headers, _root_url


class TestRootUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("http://localhost:11434/v1", "http://localhost:11434"),
            ("http://localhost:11434/v1/", "http://localhost:11434"),
            ("https://api.openai.com/v1", "https://api.openai.com"),
            # Без хвостового /v1 адрес не трогаем.
            ("https://api.anthropic.com", "https://api.anthropic.com"),
            ("http://10.0.2.2:11434/v1", "http://10.0.2.2:11434"),
        ],
    )
    def test_strips_only_the_trailing_v1(self, url: str, expected: str) -> None:
        assert _root_url(url) == expected


class TestBases:
    def test_a_catalogue_url_gives_both_addresses(self) -> None:
        provider = Provider("http://localhost:11434/v1")
        # Список моделей — OpenAI-совместимый путь.
        assert provider.openai_base == "http://localhost:11434/v1"
        # Загрузка модели — нативный API в корне.
        assert provider.root == "http://localhost:11434"


class TestAuthHeaders:
    def test_no_key_means_no_header(self) -> None:
        """Пустой Bearer путает провайдеров, поэтому его не шлём."""
        assert _auth_headers("") == {}

    def test_a_key_goes_into_a_bearer_header(self) -> None:
        assert _auth_headers("sk-123") == {"Authorization": "Bearer sk-123"}


def test_server_error_carries_the_reason() -> None:
    with pytest.raises(ServerError, match="401"):
        raise ServerError("без ключа провайдер отвечает 401")
