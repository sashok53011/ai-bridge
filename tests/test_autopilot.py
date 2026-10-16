"""Фоновая проверка: кто проверяется, что печатается, как живёт поток.

Автопроверка живёт внутри `--serve` и поддерживает статистику сообщества
(§6 CONTEXT.md): служба на FreedomBox и так работает круглосуточно,
поэтому и отчитываться она может сама — человек только включает нужное.
"""

import httpx
import pytest

from core.autopilot import checkable, report_line
from providers.client import PocketBase, Provider
from service import autopilot
from storage.keys import KeyStore


def _entry(**kwargs) -> dict:
    base = {
        "id": "ollama",
        "name": "Ollama",
        "url": "http://ollama.test/v1",
        "model": "llama3.2",
        "fallbackPriority": 6,
        "status": "active",
        "origin": "admin",
        "enabled": True,
        "mine": False,
    }
    base.update(kwargs)
    return base


@pytest.fixture(autouse=True)
def _device(tmp_path, monkeypatch):
    """Каждый тест работает на своём временном устройстве."""
    monkeypatch.setenv("AI_BRIDGE_HOME", str(tmp_path))
    return tmp_path


class _Catalogue:
    """Клиент каталога с журналом запросов — как в test_ops."""

    def __init__(self):
        self.seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            path = request.url.path
            if path.endswith("/auth-with-password"):
                return httpx.Response(
                    200, json={"token": "t", "record": {"id": "u1", "email": "a@b.c"}}
                )
            body = _json.loads(request.content) if request.content else {}
            self.seen.append({"method": request.method, "path": path, "body": body})
            return httpx.Response(200, json={"canEdit": False, "updated": "", "providers": []})

        self.client = PocketBase("https://server.test", transport=httpx.MockTransport(handler))
        self.client.login("a@b.c", "secret")

    @property
    def reports(self) -> list[dict]:
        return [item for item in self.seen if item["path"] == "/api/ai-stats" and item["method"] == "POST"]


def _factory(handler):
    def factory(url, **kwargs) -> Provider:
        return Provider(url, transport=httpx.MockTransport(handler), **kwargs)

    return factory


def _ok_provider(url, **kwargs) -> Provider:
    return _factory(lambda request: httpx.Response(200, json={"data": [{"id": "llama3.2"}]}))(url, **kwargs)


def _keyless_provider(url, **kwargs) -> Provider:
    return _factory(lambda request: httpx.Response(401, json={"error": "key required"}))(url, **kwargs)


class TestCheckable:
    def test_only_enabled_and_alive(self) -> None:
        entries = [
            _entry(id="on", enabled=True),
            _entry(id="off", enabled=False),
            _entry(id="dead", enabled=True, status="dead"),
        ]

        assert [entry["id"] for entry in checkable(entries)] == ["on"]


class TestCheckRound:
    def test_a_working_provider_is_reported(self, _device) -> None:
        server = _Catalogue()

        results = autopilot.check_round(server.client, [_entry()], KeyStore(), make_provider=_ok_provider)

        assert [result["code"] for result in results] == [0]
        assert len(server.reports) == 1

    def test_a_keyless_provider_stays_silent(self, _device) -> None:
        """401 без ключа — не поломка: в статистику и в журнал не идёт."""
        server = _Catalogue()

        results = autopilot.check_round(server.client, [_entry()], KeyStore(), make_provider=_keyless_provider)

        assert [result["code"] for result in results] == [3]
        assert server.reports == []

    def test_a_dead_provider_is_never_in_the_round(self, _device) -> None:
        """checkable отсекает неработающие ещё до сети."""
        server = _Catalogue()

        results = autopilot.check_round(
            server.client, [_entry(enabled=True, status="dead")], KeyStore(), make_provider=_ok_provider
        )

        assert results == []
        assert server.reports == []

    def test_entries_are_read_at_the_moment_of_the_round(self, _device) -> None:
        """Провайдер, выключенный через --disable, в следующий раунд не попадает."""
        server = _Catalogue()
        entries = [_entry(enabled=True)]

        autopilot.check_round(server.client, entries, KeyStore(), make_provider=_ok_provider)
        entries[0]["enabled"] = False
        results = autopilot.check_round(server.client, entries, KeyStore(), make_provider=_ok_provider)

        assert results == []
        assert len(server.reports) == 1  # зафиксирован только первый раунд


class TestReportLine:
    def test_quiet_when_nothing_to_check(self) -> None:
        assert report_line([]) == ""

    def test_one_line_for_the_journal(self) -> None:
        results = [
            {"name": "Ollama", "code": 0, "error": ""},
            {"name": "LM Studio", "code": 1, "error": "нет связи"},
            {"name": "OpenAI", "code": 3, "error": ""},
        ]

        line = report_line(results)

        assert "проверено 3" in line
        assert "в порядке 1" in line
        assert "LM Studio (нет связи)" in line
        assert "без ключа: OpenAI" in line


class TestStart:
    def test_the_loop_checks_and_logs(self, _device) -> None:
        server = _Catalogue()
        lines: list[str] = []

        thread = autopilot.start(
            server.client,
            lambda: [_entry()],
            KeyStore(),
            interval=9999.0,
            log=lines.append,
            make_provider=_ok_provider,
            rounds=1,
            sleep=lambda seconds: None,
        )
        thread.join(timeout=5)

        assert not thread.is_alive()  # rounds=1 — поток отработал и вышел
        assert any("автопроверка" in line and "в порядке 1" in line for line in lines)
        assert len(server.reports) == 1
