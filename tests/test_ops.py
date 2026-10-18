"""Служебные действия: тумблер, проверка, добавление, удаление.

CLI и веб-страница пользуются одним кодом, поэтому здесь проверяется
та же политика, что важна в консоли: флаги админа не уходят на сервер,
каталог админа не тащит локальные выжившие записи, ключ не покидает
устройство.
"""

import httpx
import pytest

from providers.client import PocketBase, Provider
from service import ops
from storage.keys import KeyStore
from storage.snapshot import Snapshot


def _entry(**kwargs) -> dict:
    base = {
        "id": "ollama",
        "name": "Ollama",
        "url": "http://ollama.test/v1",
        "model": "llama3.2",
        "fallbackPriority": 6,
        "status": "active",
        "origin": "admin",
        "enabled": False,
        "mine": False,
    }
    base.update(kwargs)
    return base


@pytest.fixture(autouse=True)
def _device(tmp_path, monkeypatch):
    """Каждый тест работает на своём временном устройстве."""
    monkeypatch.setenv("AI_BRIDGE_HOME", str(tmp_path))
    return tmp_path


class _Server:
    """Клиент каталога с журналом запросов и настраиваемым canEdit.

    ``providers`` — что отдаёт GET: админские действия теперь берут
    свежий серверный список, иначе тест проверяет не тот путь.
    """

    def __init__(self, *, can_edit: bool, user_id: str = "u1", providers: list[dict] | None = None):
        self.seen: list[dict] = []
        self._can_edit = can_edit

        def handler(request: httpx.Request) -> httpx.Response:
            import json as _json

            path = request.url.path
            if path.endswith("/auth-with-password"):
                return httpx.Response(
                    200, json={"token": "t", "record": {"id": user_id, "email": "a@b.c"}}
                )
            if path == "/api/ai-config" and request.method == "GET":
                return httpx.Response(
                    200, json={"canEdit": can_edit, "updated": "", "providers": list(providers or [])}
                )
            if path == "/api/ai-stats" and request.method == "GET":
                return httpx.Response(200, json=self.stats)
            body = _json.loads(request.content) if request.content else {}
            self.seen.append({"method": request.method, "path": path, "body": body})
            return httpx.Response(200, json={"canEdit": can_edit, "updated": "", "providers": []})

        self.stats: dict = {}
        self.client = PocketBase(
            "https://server.test", transport=httpx.MockTransport(handler)
        )
        self.client.login("a@b.c", "secret")
        self.client.fetch_catalogue()


class TestSetEnabled:
    def test_a_superuser_keeps_flags_on_the_device(self, _device) -> None:
        server = _Server(can_edit=True)
        result = ops.set_enabled(server.client, [_entry()], ["ollama"], enabled=True, can_edit=True)

        assert result["code"] == 0
        assert server.seen == []  # ни одного запроса с флагами
        assert Snapshot().load()[0]["enabled"] is True

    def test_a_regular_user_pushes_flags(self, _device) -> None:
        server = _Server(can_edit=False)
        result = ops.set_enabled(server.client, [_entry()], ["ollama"], enabled=True, can_edit=False)

        assert result["code"] == 0
        put = server.seen[-1]
        assert put["method"] == "PUT"
        assert put["body"]["providers"][0]["enabled"] is True

    def test_an_unknown_id_is_honest_about_being_hidden(self, _device) -> None:
        server = _Server(can_edit=False)
        result = ops.set_enabled(server.client, [_entry()], ["nope"], enabled=True, can_edit=False)

        assert result["code"] == 2
        assert "не найдено" in result["error"]


class TestCheck:
    def test_a_working_provider_reports_and_counts(self, _device) -> None:
        server = _Server(can_edit=False)
        server.stats = {"ollama": {"models": {"llama3.2": {"usersOk": 3, "usersFail": 1, "last": "2026-10-13T10:00:00Z"}}}}

        def factory(url, **kwargs) -> Provider:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"data": [{"id": "llama3.2"}]})

            return Provider(url, transport=httpx.MockTransport(handler), **kwargs)

        result = ops.check(server.client, _entry(), "", make_provider=factory)

        assert result["code"] == 0
        assert result["outcome"]["ok"] is True
        assert result["counter"] is not None
        assert any(item["path"] == "/api/ai-stats" and item["method"] == "POST" for item in server.seen)

    def test_a_missing_key_stays_out_of_statistics(self, _device) -> None:
        """401 без ключа — просьба ввести ключ, а не поломка провайдера."""
        server = _Server(can_edit=False)

        def factory(url, **kwargs) -> Provider:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(401, json={"error": "key required"})

            return Provider(url, transport=httpx.MockTransport(handler), **kwargs)

        result = ops.check(server.client, _entry(), "", make_provider=factory)

        assert result["code"] == 3
        assert result["outcome"]["report"] is False
        assert not any(item["method"] == "POST" for item in server.seen)

    def test_a_dead_provider_is_not_checked_at_all(self, _device) -> None:
        server = _Server(can_edit=False)
        result = ops.check(server.client, _entry(status="dead"), "", make_provider=None)

        assert result["code"] == 2
        assert "неработающей" in result["dead"]


class TestAdd:
    def test_a_superuser_pushes_server_list_plus_new_entry(self, _device) -> None:
        """Локальные выжившие записи в каталог не попадают."""
        server = _Server(can_edit=True, providers=[_entry()])
        server.client.fetch_catalogue()  # canEdit запомнен
        Snapshot().save([_entry(id="stray", name="Выжившая", url="http://s/v1", enabled=True)])

        result = ops.add(
            server.client,
            [_entry(), _entry(id="stray", name="Выжившая", url="http://s/v1", enabled=True)],
            [_entry()],  # серверный список — без выжившей
            name="Новый",
            url="http://new.test/v1",
            model="m",
            can_edit=True,
        )

        assert result["code"] == 0
        sent = server.seen[-1]["body"]["providers"]
        # «Новый» по транслитерации даёт novyy — это ожидаемо.
        assert [entry["id"] for entry in sent] == ["ollama", "novyy"]
        assert "stray" not in [entry["id"] for entry in sent]

    def test_a_user_proposal_goes_through_flags(self, _device) -> None:
        server = _Server(can_edit=False)
        result = ops.add(
            server.client,
            [_entry()],
            [_entry()],
            name="Мой LMS",
            url="http://127.0.0.1:1234/v1",
            model="qwen3",
            key="sk-local",
            can_edit=False,
        )

        assert result["code"] == 0
        assert result["entry"]["origin"] == "user"
        assert result["entry"]["mine"] is True
        # Ключ — только на устройство.
        assert KeyStore().get(result["entry"]["id"]) == "sk-local"
        sent = server.seen[-1]["body"]["providers"]
        assert all("api_key" not in entry and "apiKey" not in entry for entry in sent)

    def test_a_taken_id_is_refused(self, _device) -> None:
        server = _Server(can_edit=False)
        result = ops.add(
            server.client, [_entry()], [_entry()],
            name="X", url="http://x/v1", model="m", provider_id="ollama", can_edit=False,
        )
        assert result["code"] == 2
        assert "занят" in result["error"]

    def test_a_person_adds_locally_without_touching_the_server(self, _device) -> None:
        """sync=False — запись живёт в личном пространстве, сервер не трогается."""
        server = _Server(can_edit=False)
        before = len(server.seen)

        result = ops.add(
            server.client, [], [],
            name="Мой LMS", url="http://127.0.0.1:1234/v1", model="qwen3",
            can_edit=False, sync=False, author_id="u7",
            details={"infoUrl": "https://o.com/pricing", "pricing": "$0.1/1M"},
        )

        assert result["code"] == 0
        assert "видят только вы" in result["note"]
        assert len(server.seen) == before  # ни одного запроса к серверу
        saved = Snapshot().load()[0]
        assert saved["authorId"] == "u7"
        assert saved["infoUrl"] == "https://o.com/pricing"
        assert saved["pricing"] == "$0.1/1M"
        assert KeyStore().get("moy-lms") == ""  # ключ не передавался — и не нужен был


class TestSetModel:
    def test_a_superuser_changes_the_catalogue_model(self, _device) -> None:
        server = _Server(can_edit=True, providers=[_entry()])
        result = ops.set_model(
            server.client, [_entry()], [_entry()], "ollama", "qwen3:0.6b", can_edit=True
        )
        assert result["code"] == 0
        assert result["entry"]["model"] == "qwen3:0.6b"
        # Каталог ушёл целиком с заменой.
        sent = server.seen[-1]["body"]["providers"]
        assert sent[0]["model"] == "qwen3:0.6b"

    def test_a_user_changes_only_own_proposal(self, _device) -> None:
        server = _Server(can_edit=False)
        mine = _entry(id="mine", name="Моё", url="http://m/v1", origin="user", mine=True)
        result = ops.set_model(
            server.client, [mine], [mine], "mine", "qwen3", can_edit=False
        )
        assert result["code"] == 0

    def test_a_user_cannot_change_a_catalogue_model(self, _device) -> None:
        """Сервер бы проигнорировал — команда отказывается заранее."""
        server = _Server(can_edit=False)
        result = ops.set_model(
            server.client, [_entry()], [_entry()], "ollama", "qwen3", can_edit=False
        )
        assert result["code"] == 2
        assert "администратор" in result["error"]
        assert server.seen == []


class TestEdit:
    def test_fields_change_on_the_server(self, _device) -> None:
        server = _Server(can_edit=True, providers=[_entry()])
        result = ops.edit(
            server.client, [_entry()], [_entry()], "ollama",
            {"name": "Мой Ollama", "pricing": "$0.1/1M", "context": "128K"},
            can_edit=True,
        )
        assert result["code"] == 0
        sent = server.seen[-1]["body"]["providers"]
        assert sent[0]["name"] == "Мой Ollama"
        assert sent[0]["pricing"] == "$0.1/1M"
        assert sent[0]["context"] == "128K"

    def test_empty_form_fields_do_not_erase(self, _device) -> None:
        server = _Server(can_edit=True)
        ops.edit(
            server.client, [_entry()], [_entry()], "ollama",
            {"name": "Мой Ollama"}, can_edit=True,
        )
        result = ops.edit(
            server.client, [_entry(name="Мой Ollama")], [_entry(name="Мой Ollama")],
            "ollama", {"name": "  ", "model": "  "}, can_edit=True,
        )
        assert result["code"] == 2  # всё пустое → отклонено, ничего не стёрто

    def test_a_user_cannot_edit_a_catalogue_entry(self, _device) -> None:
        server = _Server(can_edit=False)
        result = ops.edit(
            server.client, [_entry()], [_entry()], "ollama", {"name": "X"}, can_edit=False
        )
        assert result["code"] == 2
        assert server.seen == []


class TestRemove:
    def test_a_superuser_sends_catalogue_without_the_entry(self, _device) -> None:
        server = _Server(
            can_edit=True,
            providers=[_entry(), _entry(id="b", name="B", url="http://b/v1")],
        )
        result = ops.remove(
            server.client, [_entry(), _entry(id="b", name="B", url="http://b/v1")],
            [_entry(), _entry(id="b", name="B", url="http://b/v1")],
            "ollama", can_edit=True,
        )

        assert result["code"] == 0
        sent = server.seen[-1]["body"]["providers"]
        assert [entry["id"] for entry in sent] == ["b"]

    def test_a_user_cannot_remove_a_catalogue_entry(self, _device) -> None:
        server = _Server(can_edit=False)
        result = ops.remove(server.client, [_entry()], [_entry()], "ollama", can_edit=False)

        assert result["code"] == 2
        assert "только" in result["error"]
        assert server.seen == []

    def test_a_user_removes_own_proposal(self, _device) -> None:
        server = _Server(can_edit=False)
        mine = _entry(id="mine", name="Моё", url="http://m/v1", origin="user", mine=True)
        result = ops.remove(server.client, [mine], [mine], "mine", can_edit=False)

        assert result["code"] == 0
        assert Snapshot().load() == []


class TestPersonalModel:
    """Модель — личный выбор: переживает пересборку каталога.

    Регрессия 19.10.2026: человек выбрал модель на странице, а merge
    затирал выбор серверной — проверка и /v1 продолжали стучаться к
    старой, несуществующей модели.
    """

    def test_a_chosen_model_survives_a_catalogue_refresh(self, _device) -> None:
        server = _Server(can_edit=False)
        server.client.fetch_catalogue()
        snapshot = Snapshot()
        ops.merge_view(
            server.client, [_entry()], can_edit=False, server_flags={}, snapshot=snapshot
        )
        snapshot.save(
            [{**entry, "model": "qwen3:0.6b", "picked": True} for entry in snapshot.load()]
        )

        wanted = ops.merge_view(
            server.client, [_entry(model="llama3.2")], can_edit=False,
            server_flags={}, snapshot=snapshot,
        )

        assert wanted[0]["model"] == "qwen3:0.6b"

    def test_an_admins_catalogue_change_does_not_clobber_the_choice(self, _device) -> None:
        server = _Server(can_edit=False)
        server.client.fetch_catalogue()
        snapshot = Snapshot()
        ops.merge_view(
            server.client, [_entry()], can_edit=False, server_flags={}, snapshot=snapshot
        )
        snapshot.save(
            [{**entry, "model": "qwen3:0.6b", "picked": True} for entry in snapshot.load()]
        )

        wanted = ops.merge_view(
            server.client, [_entry(model="выбранная-админом")], can_edit=False,
            server_flags={}, snapshot=snapshot,
        )

        assert wanted[0]["model"] == "qwen3:0.6b"

    def test_a_different_model_without_the_mark_is_not_a_choice(self, _device) -> None:
        """Регрессия: разные данные без отметки picked — это не выбор, а мусор."""
        server = _Server(can_edit=False)
        server.client.fetch_catalogue()
        snapshot = Snapshot()
        snapshot.save([_entry(model="кто-что-попало")])

        wanted = ops.merge_view(
            server.client, [_entry(model="server-model")], can_edit=False,
            server_flags={}, snapshot=snapshot,
        )

        assert wanted[0]["model"] == "server-model"

    def test_without_a_choice_the_server_model_wins(self, _device) -> None:
        server = _Server(can_edit=False)
        server.client.fetch_catalogue()
        snapshot = Snapshot()
        ops.merge_view(
            server.client, [_entry(model="server-model")], can_edit=False,
            server_flags={}, snapshot=snapshot,
        )

        wanted = ops.merge_view(
            server.client, [_entry(model="server-model-2")], can_edit=False,
            server_flags={}, snapshot=snapshot,
        )

        assert wanted[0]["model"] == "server-model-2"

    def test_an_empty_snapshot_model_is_not_an_override(self, _device) -> None:
        server = _Server(can_edit=False)
        server.client.fetch_catalogue()
        snapshot = Snapshot()
        snapshot.save([_entry(model="")])

        wanted = ops.merge_view(
            server.client, [_entry(model="server-model")], can_edit=False,
            server_flags={}, snapshot=snapshot,
        )

        assert wanted[0]["model"] == "server-model"
