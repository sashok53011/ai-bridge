"""Медиа-запросы провайдера: метаданные каталога, генерация, GET media.

Pollinations и подобные отдают в каталоге типы моделей (category,
supported_endpoints) — без них автотест не знает, какими моделями
проверять картинки и звук. Здесь проверяется только транспорт:
URL, заголовки, multipart; сеть замокана.
"""

from __future__ import annotations

import httpx
import pytest

from providers.client import NeedsKeyError, Provider, ServerError


def _provider(handler) -> Provider:
    return Provider("https://gen.test/v1", transport=httpx.MockTransport(handler))


class TestFetchModelMeta:
    def test_metadata_is_kept_for_typed_models_only(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/models"
            return httpx.Response(200, json={"data": [
                {"id": "img-1", "category": "image",
                 "supported_endpoints": ["/v1/images/generations"], "paid_only": True},
                {"id": "plain", "object": "model"},  # без метаданных — не мусорим
                {"id": "tts-1", "category": "audio",
                 "supported_endpoints": ["/v1/audio/speech"], "paid_only": False},
            ]})

        meta = _provider(handler).fetch_model_meta("sk-test")

        assert set(meta) == {"img-1", "tts-1"}
        assert meta["img-1"] == {
            "category": "image",
            "endpoints": ["/v1/images/generations"],
            "paid": True,
        }
        assert meta["tts-1"]["paid"] is False

    def test_no_metadata_in_the_catalog_is_not_an_error(self) -> None:
        """Обычный OpenAI-список без типов — пустой словарь, не поломка."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "gpt-x"}]})

        assert _provider(handler).fetch_model_meta("sk") == {}

    def test_a_server_error_is_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        with pytest.raises(ServerError):
            _provider(handler).fetch_model_meta("")

    def test_a_key_is_required_like_for_models(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "no"})

        with pytest.raises(NeedsKeyError):
            _provider(handler).fetch_model_meta("")


class TestForwardPath:
    def test_post_goes_to_the_given_path_under_the_base(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["auth"] = request.headers.get("Authorization", "")
            seen["body"] = request.content
            return httpx.Response(200, json={"ok": True})

        client = _provider(handler)
        response = client.forward_path(
            "/images/generations",
            '{"prompt": "кот"}'.encode("utf-8"),
            api_key="sk-1", stream=False,
        )

        assert seen["path"] == "/v1/images/generations"
        assert seen["auth"] == "Bearer sk-1"
        assert b"prompt" in seen["body"]
        assert response.status_code == 200

    def test_a_down_provider_is_a_server_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("нет сети")

        with pytest.raises(ServerError):
            _provider(handler).forward_path("/audio/speech", b"{}", stream=False)


class TestForwardForm:
    def test_multipart_carries_fields_and_the_file(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["type"] = request.headers.get("content-type", "")
            seen["body"] = request.content
            return httpx.Response(200, json={"text": "привет"})

        client = _provider(handler)
        response = client.forward_form(
            "/audio/transcriptions",
            {"model": "whisper-1"},
            {"file": ("tone.wav", b"RIFF....", "audio/wav")},
            api_key="sk-1",
        )

        assert seen["path"] == "/v1/audio/transcriptions"
        assert "multipart/form-data" in seen["type"]
        assert b'name="model"' in seen["body"]
        assert b'name="file"' in seen["body"]
        assert b"tone.wav" in seen["body"]
        assert response.json() == {"text": "привет"}


class TestMediaGet:
    def test_video_is_fetched_from_the_server_root(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["model"] = request.url.params.get("model", "")
            seen["auth"] = request.headers.get("Authorization", "")
            return httpx.Response(
                200,
                content=b"\x00\x00\x00\x18ftypmp42",
                headers={"content-type": "video/mp4",
                         "Link": '<https://media.test/abc>; rel="enclosure"'},
            )

        client = _provider(handler)
        response = client.media_get("video", "закат таймлапс", "veo-3", api_key="sk-1")

        # Корень сервера (/v1 снят), промпт закодирован, модель — query-параметром.
        assert seen["path"].startswith("/video/")
        assert "закат" in seen["path"] or "%" in seen["path"]
        assert seen["model"] == "veo-3"
        assert seen["auth"] == "Bearer sk-1"
        assert 'https://media.test/' in response.headers["Link"]
        assert response.status_code == 200
