"""Проверка провайдера: три исхода — успех, поломка, «нужен ключ»."""

from core.check import judge


class TestJudge:
    def test_a_working_provider_reports_success(self) -> None:
        outcome = judge(["llama3.2", "qwen3"], configured="llama3.2")
        assert outcome["ok"] is True
        assert outcome["report"] is True
        assert outcome["model"] == "llama3.2"
        assert outcome["error"] == ""

    def test_without_a_configured_model_the_first_ones_is_reported(self) -> None:
        outcome = judge(["llama3.2", "qwen3"])
        assert outcome["ok"] is True
        assert outcome["model"] == "llama3.2"

    def test_a_provider_prefix_does_not_break_the_match(self) -> None:
        """Провайдеры часто добавляют префикс: openai/gpt-4o против gpt-4o."""
        outcome = judge(["openai/gpt-4o"], configured="gpt-4o")
        assert outcome["ok"] is True
        assert outcome["model"] == "gpt-4o"

    def test_a_missing_configured_model_is_a_failure(self) -> None:
        outcome = judge(["llama3.2"], configured="gpt-4o")
        assert outcome["ok"] is False
        assert outcome["report"] is True
        assert outcome["model"] == "gpt-4o"
        assert "gpt-4o" in outcome["error"]

    def test_an_empty_model_list_is_a_failure(self) -> None:
        outcome = judge([])
        assert outcome["ok"] is False
        assert outcome["report"] is True
        assert outcome["error"]

    def test_an_unreachable_provider_is_reported(self) -> None:
        outcome = judge(None, configured="llama3.2", error="провайдер недоступен: timeout")
        assert outcome["ok"] is False
        assert outcome["report"] is True
        assert outcome["model"] == "llama3.2"
        assert "недоступен" in outcome["error"]

    def test_a_missing_key_is_not_reported_to_the_server(self) -> None:
        """401 без ключа — просьба ввести ключ, а не поломка провайдера.

        Счётчик сообщества должен расти только от тех, кто действительно
        проверил провайдер, а не от тех, кто ещё не ввёл ключ.
        """
        outcome = judge(None, configured="gpt-4o", needs_key=True)
        assert outcome["ok"] is False
        assert outcome["report"] is False
        assert "ключ" in outcome["error"]

    def test_a_rejected_key_is_a_failure_even_with_models_absent(self) -> None:
        """Ключ введён, но 401 — это уже поломка, и она уходит в отчёт."""
        outcome = judge(None, configured="gpt-4o", error="ключ не принят: HTTP 401")
        assert outcome["ok"] is False
        assert outcome["report"] is True
