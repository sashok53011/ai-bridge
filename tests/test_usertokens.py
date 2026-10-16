"""Токены приложений: выдать, найти владельца, отозвать по хвосту."""

from storage.usertokens import UserTokens


class TestUserTokens:
    def test_a_new_token_belongs_to_its_owner(self, tmp_path) -> None:
        tokens = UserTokens(tmp_path)

        token = tokens.create("u1", "телефон")

        assert token.startswith("uib-")
        assert tokens.owner(token) == "u1"
        assert tokens.owner("uib-чужой") == ""

    def test_a_person_sees_only_their_tokens_with_tails(self, tmp_path) -> None:
        tokens = UserTokens(tmp_path)
        mine = tokens.create("u1", "телефон")
        tokens.create("u2", "чужой")

        listed = tokens.list_for("u1")

        assert len(listed) == 1
        assert listed[0]["name"] == "телефон"
        assert listed[0]["tail"] == mine[-6:]

    def test_revoke_by_tail_removes_exactly_that_token(self, tmp_path) -> None:
        tokens = UserTokens(tmp_path)
        first = tokens.create("u1", "телефон")
        second = tokens.create("u1", "ноутбук")

        assert tokens.revoke("u1", first[-6:]) is True

        assert tokens.owner(first) == ""
        assert tokens.owner(second) == "u1"

    def test_one_cannot_revoke_anotherPersons_token(self, tmp_path) -> None:
        tokens = UserTokens(tmp_path)
        alien = tokens.create("u2", "чужой")

        assert tokens.revoke("u1", alien[-6:]) is False
        assert tokens.owner(alien) == "u2"
