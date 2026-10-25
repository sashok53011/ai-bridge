"""Настройки уведомлений: токен бота на сервере, chat_id в профиле.

Токен — секрет: живёт в notify.json, на страницу не выводится; пустой
токен — уведомления выключены, а не «настроены вполсилы». Chat_id —
личный: есть — пуш приходит, нет — молча пропускаем.
"""

import json

from storage.notify import NotifyStore
from storage.users import UserStore


class TestToken:
    def test_an_empty_token_by_default(self, tmp_path) -> None:
        store = NotifyStore(tmp_path)

        assert store.token() == ""
        assert store.enabled() is False

    def test_token_is_saved_and_survives_reopen(self, tmp_path) -> None:
        NotifyStore(tmp_path).set_token("123:ABC-DEF")

        reopened = NotifyStore(tmp_path)

        assert reopened.token() == "123:ABC-DEF"
        assert reopened.enabled() is True

    def test_blanking_the_token_turns_notifications_off(self, tmp_path) -> None:
        store = NotifyStore(tmp_path)
        store.set_token("123:ABC")
        store.set_token("   ")

        assert store.token() == ""
        assert store.enabled() is False

    def test_a_broken_file_is_no_token(self, tmp_path) -> None:
        (tmp_path / "notify.json").write_text("{сломано", encoding="utf-8")

        assert NotifyStore(tmp_path).token() == ""

    def test_the_token_is_plain_json_the_page_never_sees(self, tmp_path) -> None:
        NotifyStore(tmp_path).set_token("123:ABC")

        data = json.loads((tmp_path / "notify.json").read_text(encoding="utf-8"))

        assert data == {"token": "123:ABC"}


class TestChatId:
    def test_a_chat_id_lives_in_the_profile(self, tmp_path) -> None:
        users = UserStore(tmp_path)
        users.remember("u1", "a@b.c")

        users.set_tg("u1", "424242")

        assert users.tg("u1") == "424242"
        assert UserStore(tmp_path).tg("u1") == "424242"  # переживает перезапуск

    def test_no_chat_id_means_no_notifications_not_an_error(self, tmp_path) -> None:
        users = UserStore(tmp_path)
        users.remember("u1", "a@b.c")

        assert users.tg("u1") == ""

    def test_blanking_removes_the_field(self, tmp_path) -> None:
        users = UserStore(tmp_path)
        users.remember("u1", "a@b.c")
        users.set_tg("u1", "424242")

        users.set_tg("u1", "")

        assert users.tg("u1") == ""
        assert "tg" not in (users.get("u1") or {})

    def test_an_unknown_person_has_no_chat(self, tmp_path) -> None:
        assert UserStore(tmp_path).tg("никого") == ""
