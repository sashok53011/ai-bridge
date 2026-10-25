"""Личная почта: ящик у каждого, отказ от приёма уводит письма админу.

Главное здесь — обещания, данные на которых стоит разговор: письмо
доходит до адресата, рассылка видна всем, отказавшийся получает
письма не теряет (их читает админ), а переписка переживает
переоткрытие хранилища.
"""

import json

from storage.mail import EVERYONE, MailStore


def _mail(tmp_path) -> MailStore:
    return MailStore(tmp_path)


def _send(store: MailStore, to: str, text: str = "привет", **kw) -> str:
    return store.send(author_id="me", author="me@b.c", to=to, text=text, **kw)


class TestSending:
    def test_a_letter_reaches_the_recipients_inbox(self, tmp_path) -> None:
        store = _mail(tmp_path)

        assert _send(store, "u2") == ""

        (letter,) = store.inbox("u2")
        assert letter["from_email"] == "me@b.c"
        assert letter["text"] == "привет"
        assert letter["ts"].endswith("Z")
        assert store.inbox("u3") == []  # чужому ящику не попало

    def test_a_broadcast_reaches_every_inbox(self, tmp_path) -> None:
        store = _mail(tmp_path)

        assert _send(store, EVERYONE, text="всем привет") == ""

        assert store.inbox("u1")[0]["text"] == "всем привет"
        assert store.inbox("u9")[0]["text"] == "всем привет"

    def test_an_empty_letter_is_refused_with_a_reason(self, tmp_path) -> None:
        store = _mail(tmp_path)

        error = _send(store, "u2", text="   ")

        assert error
        assert store.all() == []

    def test_a_letter_without_a_recipient_is_refused(self, tmp_path) -> None:
        error = _send(_mail(tmp_path), "", text="куда?")

        assert "адресат" in error

    def test_rerouted_mail_keeps_the_original_addressee(self, tmp_path) -> None:
        """Отказавшийся не теряет письмо: админ видит, за кого оно."""
        store = _mail(tmp_path)

        store.send(author_id="me", author="me@b.c", to="admin-id",
                   text="для тебя", orig_to="u2@b.c")

        (letter,) = store.inbox("admin-id")
        assert letter["to"] == "admin-id"
        assert letter["orig_to"] == "u2@b.c"
        assert letter["text"] == "для тебя"


class TestInboxOrder:
    def test_new_letters_come_first(self, tmp_path) -> None:
        store = _mail(tmp_path)
        _send(store, "u2", text="первое")
        _send(store, "u2", text="второе")

        texts = [m["text"] for m in store.inbox("u2")]

        assert texts == ["второе", "первое"]

    def test_a_broadcast_and_a_personal_letter_mix_in_one_inbox(self, tmp_path) -> None:
        store = _mail(tmp_path)
        _send(store, "u2", text="лично")
        _send(store, EVERYONE, text="всем")

        texts = [m["text"] for m in store.inbox("u2")]

        assert texts == ["всем", "лично"]


class TestAdminPower:
    def test_the_admin_can_read_any_mail(self, tmp_path) -> None:
        """Полная власть: всё в одном файле, админ видит каждое письмо."""
        store = _mail(tmp_path)
        _send(store, "u2", text="тайное")
        _send(store, "u3", text="тоже тайное")

        assert {m["text"] for m in store.all()} == {"тайное", "тоже тайное"}

    def test_the_admin_drops_any_letter(self, tmp_path) -> None:
        store = _mail(tmp_path)
        _send(store, "u2", text="мусор")
        _send(store, "u2", text="норм")
        victim = store.inbox("u2")[0]["id"]

        assert store.drop(victim) is True

        assert [m["text"] for m in store.inbox("u2")] == ["мусор"]
        assert store.drop("нет-такого") is False


class TestDurability:
    def test_letters_survive_a_reopen(self, tmp_path) -> None:
        _mail(tmp_path).send(author_id="a", author="a@b.c", to="u2", text="живу")

        assert _mail(tmp_path).inbox("u2")[0]["text"] == "живу"

    def test_the_file_is_plain_readable_json(self, tmp_path) -> None:
        _send(_mail(tmp_path), "u2", text="просто текст")

        data = json.loads((tmp_path / "mail.json").read_text(encoding="utf-8"))

        assert data["messages"][0]["text"] == "просто текст"

    def test_a_broken_file_is_empty_mail_not_a_crash(self, tmp_path) -> None:
        (tmp_path / "mail.json").write_text("{сломано", encoding="utf-8")

        assert _mail(tmp_path).all() == []
        assert _mail(tmp_path).inbox("u2") == []

    def test_no_temporary_files_are_left_behind(self, tmp_path) -> None:
        store = _mail(tmp_path)
        _send(store, "u2")
        store.drop(store.inbox("u2")[0]["id"])

        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "mail.json"]

        assert leftovers == []
