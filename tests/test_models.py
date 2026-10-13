"""Список моделей: два формата ответа и сравнение с настройкой."""

from core.models import extract_models, offers_model, parse_ndjson


class TestExtractModels:
    def test_openai_style_payload(self) -> None:
        payload = {"object": "list", "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}
        assert extract_models(payload) == ["gpt-4o", "gpt-4o-mini"]

    def test_ollama_style_payload(self) -> None:
        payload = {"models": [{"name": "devstral-small-2:24b"}, {"name": "qwen3.8:27b"}]}
        assert extract_models(payload) == ["devstral-small-2:24b", "qwen3.8:27b"]

    def test_deduplicates_and_sorts(self) -> None:
        payload = {"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}]}
        assert extract_models(payload) == ["a", "b"]

    def test_garbage_becomes_an_empty_list(self) -> None:
        assert extract_models(None) == []
        assert extract_models("строка") == []
        assert extract_models({"data": "не список"}) == []


class TestOffersModel:
    def test_exact_match(self) -> None:
        assert offers_model("gpt-4o", ["gpt-4o", "gpt-4o-mini"]) is True
        assert offers_model("gpt-4o-mini", ["gpt-4o"]) is False

    def test_case_insensitive(self) -> None:
        assert offers_model("GPT-4O", ["gpt-4o"]) is True

    def test_namespaced_model_still_matches(self) -> None:
        """Провайдеры часто добавляют префикс: openai/gpt-4o против gpt-4o."""
        assert offers_model("gpt-4o", ["openai/gpt-4o"]) is True
        assert offers_model("llama3", ["ollama:llama3"]) is True

    def test_empty_settings_never_match(self) -> None:
        assert offers_model("", ["gpt-4o"]) is False
        assert offers_model("gpt-4o", []) is False


class TestParseNdjson:
    def test_one_event_per_line(self) -> None:
        text = '{"status":"pulling manifest"}\n{"status":"success"}\n'
        events = parse_ndjson(text)
        assert [event["status"] for event in events] == ["pulling manifest", "success"]

    def test_broken_lines_are_skipped(self) -> None:
        text = '{"status":"a"}\nэто не json\n\n{"status":"b"}\n'
        assert [event["status"] for event in parse_ndjson(text)] == ["a", "b"]
