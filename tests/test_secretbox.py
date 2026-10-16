"""Шифрование ключей на устройстве.

Ключ провайдера — настоящий секрет (§7 CONTEXT.md): на сервер он не
уходит, но и на диске открытым текстом лежать не должен. Каждое
значение запаковано в AES-256-GCM, мастер-ключ живёт рядом
(`master.key`). Скопированный на другую машину или случайно
закоммиченный `keys.json` без мастер-ключа бесполезен.
"""

import json

import pytest

from storage.keys import KeyStore
from storage.secretbox import SecretBox, SecretBoxError


class TestSecretBox:
    def test_a_sealed_value_opens_back(self, tmp_path) -> None:
        box = SecretBox(tmp_path)

        blob = box.seal("sk-secret")

        assert blob != "sk-secret"
        assert SecretBox(tmp_path).open(blob) == "sk-secret"

    def test_the_same_directory_shares_the_master_key(self, tmp_path) -> None:
        first = SecretBox(tmp_path)
        second = SecretBox(tmp_path)

        assert second.open(first.seal("sk")) == "sk"

    def test_a_replaced_master_key_does_not_open_old_boxes(self, tmp_path) -> None:
        blob = SecretBox(tmp_path).seal("sk")
        (tmp_path / "master.key").unlink()  # новый мастер-ключ на то же место

        with pytest.raises(SecretBoxError):
            SecretBox(tmp_path).open(blob)

    def test_a_broken_master_key_is_a_clear_error(self, tmp_path) -> None:
        (tmp_path / "master.key").write_text("сломанный файл", encoding="utf-8")

        with pytest.raises(SecretBoxError):
            SecretBox(tmp_path)


class TestKeyStoreEncryption:
    def test_keys_are_not_on_the_disk_in_clear(self, tmp_path) -> None:
        keys = KeyStore(tmp_path)
        keys.set("ollama", "sk-plain-9999")

        raw = (tmp_path / "keys.json").read_text(encoding="utf-8")
        assert "sk-plain-9999" not in raw
        assert keys.get("ollama") == "sk-plain-9999"
        assert KeyStore(tmp_path).get("ollama") == "sk-plain-9999"

    def test_a_legacy_plaintext_file_migrates_on_first_read(self, tmp_path) -> None:
        (tmp_path / "keys.json").write_text(json.dumps({"ollama": "sk-old"}), encoding="utf-8")

        keys = KeyStore(tmp_path)

        assert keys.get("ollama") == "sk-old"
        raw = (tmp_path / "keys.json").read_text(encoding="utf-8")
        assert "sk-old" not in raw
        assert json.loads(raw)["version"] == 2

    def test_a_lost_master_key_is_a_loud_error_not_silence(self, tmp_path) -> None:
        """Тишина выглядела бы как «провайдер без ключа» — это хуже падения."""
        keys = KeyStore(tmp_path)
        keys.set("ollama", "sk-123")
        (tmp_path / "master.key").unlink()

        with pytest.raises(SecretBoxError):
            KeyStore(tmp_path)

    def test_delete_and_ids_survive_encryption(self, tmp_path) -> None:
        keys = KeyStore(tmp_path)
        keys.set("ollama", "sk-1")
        keys.set("openai", "sk-2")

        keys.delete("ollama")

        assert keys.ids() == ["openai"]
        assert KeyStore(tmp_path).get("openai") == "sk-2"
