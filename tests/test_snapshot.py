"""Снапшот на устройстве: полный список с флагами, без секретов."""

import json

from storage.keys import KeyStore
from storage.snapshot import Snapshot


def _entry(**kwargs) -> dict:
    base = {"id": "ollama", "name": "Ollama", "url": "http://localhost:11434/v1", "model": "llama3.2"}
    base.update(kwargs)
    return base


class TestSnapshot:
    def test_a_saved_list_comes_back_with_its_flags(self, tmp_path) -> None:
        snap = Snapshot(tmp_path)
        snap.save([_entry(enabled=True), _entry(id="openai", name="OpenAI", enabled=False)])

        loaded = snap.load()
        assert [entry["id"] for entry in loaded] == ["ollama", "openai"]
        assert loaded[0]["enabled"] is True
        assert loaded[1]["enabled"] is False

    def test_no_file_means_nothing_seen_yet(self, tmp_path) -> None:
        assert Snapshot(tmp_path).load() == []

    def test_a_broken_file_is_not_a_crash(self, tmp_path) -> None:
        (tmp_path / "snapshot.json").write_text("это не json", encoding="utf-8")
        assert Snapshot(tmp_path).load() == []

    def test_garbage_entries_fall_out(self, tmp_path) -> None:
        snap = Snapshot(tmp_path)
        snap.save([_entry(), {"id": "", "name": "без адреса"}, "строка", 42])
        assert [entry["id"] for entry in snap.load()] == ["ollama"]

    def test_an_api_key_never_lands_in_the_file(self, tmp_path) -> None:
        """Правило продукта: ключ не покидает keys.json."""
        snap = Snapshot(tmp_path)
        snap.save([_entry(api_key="sk-secret", apiKey="sk-secret2", enabled=True)])

        raw = (tmp_path / "snapshot.json").read_text(encoding="utf-8")
        assert "sk-secret" not in raw
        for entry in snap.load():
            assert "api_key" not in entry and "apiKey" not in entry

    def test_clear_forgets_everything(self, tmp_path) -> None:
        snap = Snapshot(tmp_path)
        snap.save([_entry(enabled=True)])
        snap.clear()
        assert snap.load() == []

    def test_keys_and_snapshot_live_side_by_side(self, tmp_path) -> None:
        """Разные файлы: снапшот не трогает ключи и наоборот."""
        keys = KeyStore(tmp_path)
        keys.set("ollama", "sk-123")
        snap = Snapshot(tmp_path)
        snap.save([_entry(enabled=True)])
        snap.clear()

        assert keys.get("ollama") == "sk-123"
        assert json.loads((tmp_path / "keys.json").read_text(encoding="utf-8")) == {"ollama": "sk-123"}
