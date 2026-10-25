"""Хранилище правил «строка → действие» (страница «Правила»)."""

from storage.rules import (
    REQUEST_ACTIONS,
    RESPONSE_ACTIONS,
    RuleStore,
)


class TestReading:
    def test_an_empty_store_has_no_rules(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        data = store.all()

        assert data == {"response": [], "request": []}

    def test_a_broken_file_is_read_as_no_rules(self, tmp_path) -> None:
        tmp_path.joinpath("rules.json").write_text("{сломано", encoding="utf-8")

        assert RuleStore(tmp_path).all() == {"response": [], "request": []}

    def test_response_rules_are_filtered_by_provider(self, tmp_path) -> None:
        store = RuleStore(tmp_path)
        store.add("response", text="кредиты", action="grey", provider="ollama")
        store.add("response", text="лимит", action="black", provider="groq")

        assert [r["text"] for r in store.response_rules("ollama")] == ["кредиты"]
        assert [r["text"] for r in store.response_rules("GROQ")] == ["лимит"]  # регистр
        assert store.response_rules("нет-такого") == []


class TestWriting:
    def test_a_response_rule_keeps_the_provider(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        assert store.add("response", text="кредиты", action="grey", provider="ollama")

        assert store.all()["response"] == [
            {"text": "кредиты", "action": "grey", "provider": "ollama"}
        ]

    def test_a_request_rule_has_no_provider(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        store.add("request", text="image_url", action="vision")

        assert store.all()["request"] == [{"text": "image_url", "action": "vision"}]

    def test_an_unknown_action_is_refused(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        assert not store.add("response", text="x", action="всё сжечь", provider="a")
        assert not store.add("request", text="x", action="nope")
        assert store.all() == {"response": [], "request": []}

    def test_an_empty_text_is_refused(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        assert not store.add("response", text="   ", action="grey", provider="a")
        assert store.all()["response"] == []

    def test_drop_removes_by_index(self, tmp_path) -> None:
        store = RuleStore(tmp_path)
        store.add("response", text="первое", action="grey", provider="a")
        store.add("response", text="второе", action="black", provider="a")

        assert store.drop("response", 0)

        assert [r["text"] for r in store.all()["response"]] == ["второе"]

    def test_a_bad_index_changes_nothing(self, tmp_path) -> None:
        store = RuleStore(tmp_path)
        store.add("request", text="x", action="vision")

        assert not store.drop("request", 5)
        assert not store.drop("request", -1)
        assert len(store.all()["request"]) == 1

    def test_the_rules_survive_a_restart(self, tmp_path) -> None:
        store = RuleStore(tmp_path)
        store.add("response", text="кредиты", action="grey", provider="ollama")

        again = RuleStore(tmp_path)  # перезапуск службы

        assert again.all()["response"][0]["text"] == "кредиты"

    def test_every_declared_action_is_known(self) -> None:
        """Подписи на странице есть для каждого кода действия."""
        assert set(RESPONSE_ACTIONS) == {
            "grey", "black", "white", "switch", "untouched",
            "can-vision", "can-tools", "cant-vision", "cant-tools",
        }
        assert set(REQUEST_ACTIONS) == {"vision", "tools"}


class TestCompositeConditions:
    """Условие ответа составное: строка в запросе + строка в ответе + статус.

    Все указанные части обязаны совпасть разом; хотя бы «строка в ответе»
    или «статус» обязаны быть — иначе правило ловило бы вообще всё.
    """

    def test_a_rule_needs_at_least_a_response_part(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        assert not store.add("response", provider="a", action="grey")  # совсем без условий
        assert not store.add("response", provider="a", action="grey",
                             req="image_url")  # только запрос — мало
        assert store.add("response", provider="a", action="grey", status=200)
        assert store.add("response", provider="a", action="grey", text="x")

    def test_a_rule_needs_a_provider(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        assert not store.add("response", text="x", action="grey")

    def test_status_is_stored_as_an_int(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        store.add("response", provider="a", action="white", status="200")

        assert store.all()["response"][0]["status"] == 200

    def test_a_junk_status_is_dropped(self, tmp_path) -> None:
        store = RuleStore(tmp_path)

        # Мусор вместо статуса: если есть строка — правило живёт, но без
        # условия статуса. Если и строки нет — условия нет, отклоняется.
        assert store.add("response", provider="a", action="white",
                         text="ok", status="двадцать")
        assert "status" not in store.all()["response"][0]

        assert not store.add("response", provider="a", action="white",
                             status="двадцать")

    def test_edit_changes_a_rule_in_place(self, tmp_path) -> None:
        store = RuleStore(tmp_path)
        store.add("response", text="старое", action="grey", provider="a")

        assert store.edit("response", 0, text="новое", action="black",
                          provider="a", status=429)

        rule = store.all()["response"][0]
        assert rule == {"provider": "a", "action": "black",
                        "text": "новое", "status": 429}

    def test_edit_of_a_bad_index_fails(self, tmp_path) -> None:
        store = RuleStore(tmp_path)
        store.add("request", text="x", action="vision")

        assert not store.edit("request", 7, text="y", action="vision")
        assert store.all()["request"][0]["text"] == "x"

    def test_edit_refuses_incomplete_conditions(self, tmp_path) -> None:
        store = RuleStore(tmp_path)
        store.add("response", text="x", action="grey", provider="a")

        assert not store.edit("response", 0, text="", action="grey", provider="a")

        assert store.all()["response"][0]["text"] == "x"  # не изменилось

    def test_describe_lists_all_parts_for_the_journal(self) -> None:
        from storage.rules import describe

        rule = {"provider": "a", "action": "grey",
                "req": "image_url", "text": "credits", "status": 200}
        assert describe(rule) == (
            "в запросе «image_url» и в ответе «credits» и статус 200"
        )

    def test_match_requires_every_declared_part(self) -> None:
        from storage.rules import match_response

        rule = {"req": "image_url", "text": "accepted", "status": 200}
        assert match_response(rule, req_text="...image_url...",
                              resp_text="images accepted", status=200)
        assert not match_response(rule, req_text="обычный текст",
                                  resp_text="images accepted", status=200)
        assert not match_response(rule, req_text="image_url",
                                  resp_text="отказ", status=200)
        assert not match_response(rule, req_text="image_url",
                                  resp_text="images accepted", status=400)

    def test_empty_parts_are_not_checked(self) -> None:
        from storage.rules import match_response

        rule = {"text": "accepted"}
        assert match_response(rule, req_text="", resp_text="accepted", status=500)
