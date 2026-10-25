"""Живое состояние локальных серверов: Ollama /api/ps, LM Studio health."""

from __future__ import annotations

import httpx

from providers.client import Provider, _format_local_status


def _provider(handler) -> Provider:
    return Provider("http://localhost:11434/v1", transport=httpx.MockTransport(handler))


class TestFormatLocalStatus:
    def test_ollama_with_models_shows_names_and_gpu(self) -> None:
        payload = {"models": [
            {"name": "qwen3:0.6b", "size": 1000, "size_vram": 1000},
            {"name": "llava", "size": 2000, "size_vram": 1000},
        ]}

        line = _format_local_status(payload, ollama=True)

        assert "qwen3:0.6b · GPU 100%" in line
        assert "llava · GPU 50%" in line

    def test_ollama_without_models_says_empty(self) -> None:
        assert _format_local_status({"models": []}, ollama=True) == "в памяти пусто"

    def test_lmstudio_loaded_model_is_found(self) -> None:
        line = _format_local_status({"loaded_model": "prism-ml/bonsai-27b"}, ollama=False)
        assert line == "загружено: prism-ml/bonsai-27b"

    def test_nested_data_is_understood(self) -> None:
        payload = {"data": {"model": "qwen3-0.6b"}}
        assert _format_local_status(payload, ollama=False) == "загружено: qwen3-0.6b"

    def test_nonsense_is_silence(self) -> None:
        assert _format_local_status({"status": "healthy"}, ollama=False) == ""
        assert _format_local_status("не json", ollama=True) == ""

    def test_gpu_is_skipped_when_sizes_absent(self) -> None:
        line = _format_local_status({"models": [{"name": "x"}]}, ollama=True)
        assert line == "загружено: x"  # без размеров — без GPU%, но не врём


class TestLocalStatusEndpoint:
    def test_ollama_ps_is_reported(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/ps":
                return httpx.Response(200, json={"models": [
                    {"name": "qwen3:0.6b", "size": 500, "size_vram": 500},
                ]})
            return httpx.Response(404)

        assert _provider(handler).local_status() == "загружено: qwen3:0.6b · GPU 100%"

    def test_a_dead_endpoint_is_an_empty_line(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        assert _provider(handler).local_status() == ""

    def test_lmstudio_health_fallback(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/ps":
                return httpx.Response(404)  # Ollama-путь не поддержан
            if request.url.path == "/api/v0/loaded":
                return httpx.Response(404)
            return httpx.Response(200, json={"loaded_model": "bonsai-27b"})

        assert _provider(handler).local_status() == "загружено: bonsai-27b"

    def test_a_hanging_server_does_not_block_forever(self) -> None:
        """Таймаут клиента (1.5с в веб-слое) кладёт конец опросу."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        assert _provider(handler).local_status() == ""
