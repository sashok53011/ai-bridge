"""Правила каталога: нормализация, кому что показывать, синхронизация."""

from core.catalogue import (
    STATUS_ACTIVE,
    STATUS_DEAD,
    STATUS_DISABLED,
    can_edit_model,
    edit_entry,
    next_priority,
    normalize,
    propose,
    reconcile,
    set_model,
    view,
    with_local_enabled,
)


def _entry(**kwargs) -> dict:
    base = {
        "id": "ollama",
        "name": "Ollama",
        "url": "http://localhost:11434/v1",
        "model": "llama3.2",
        "status": STATUS_ACTIVE,
        "origin": "admin",
    }
    base.update(kwargs)
    return base


class TestNormalize:
    def test_keeps_what_matters(self) -> None:
        entry = normalize(_entry(fallbackPriority="7", name="  Ollama "))
        assert entry is not None
        assert entry["id"] == "ollama"
        assert entry["name"] == "Ollama"
        assert entry["fallbackPriority"] == 7

    def test_drops_entries_without_id_or_url(self) -> None:
        assert normalize({"name": "без адреса"}) is None
        assert normalize({"id": "x", "url": "   "}) is None
        assert normalize("мусор") is None
        assert normalize(None) is None

    def test_unknown_status_becomes_active(self) -> None:
        entry = normalize(_entry(status="какой-то статус"))
        assert entry is not None
        assert entry["status"] == STATUS_ACTIVE


class TestView:
    def test_dead_is_hidden_until_switched_on(self) -> None:
        catalogue = [_entry(status=STATUS_DEAD)]
        assert view(catalogue, flags={}) == []
        # Включённое показывается с пометкой, чтобы человек мог выключить.
        shown = view(catalogue, flags={"ollama": True})
        assert shown[0]["enabled"] is True

    def test_disabled_for_all_is_hidden_until_switched_on(self) -> None:
        catalogue = [_entry(status=STATUS_DISABLED)]
        assert view(catalogue, flags={}) == []
        assert view(catalogue, flags={"ollama": True})[0]["enabled"] is True

    def test_superuser_sees_everything(self) -> None:
        catalogue = [
            _entry(status=STATUS_DEAD),
            _entry(id="other", url="https://x.example/v1"),
        ]
        assert len(view(catalogue, flags={}, is_superuser=True)) == 2

    def test_a_proposal_is_only_visible_to_its_author(self) -> None:
        catalogue = [_entry(id="mine", origin="user", authorId="user-1")]
        assert view(catalogue, flags={}, viewer_id="user-1")[0]["mine"] is True
        assert view(catalogue, flags={}, viewer_id="user-2") == []
        assert view(catalogue, flags={}, is_superuser=True)[0]["mine"] is False


class TestReconcile:
    def test_switched_on_entries_survive_any_catalogue(self) -> None:
        """Главное правило: включённое человеком не удаляется никогда."""
        local = [_entry(enabled=True)]
        result = reconcile(local, remote=[])
        assert [entry["id"] for entry in result] == ["ollama"]
        assert result[0]["enabled"] is True

    def test_switched_off_follow_the_catalogue(self) -> None:
        local = [_entry(enabled=False)]
        assert reconcile(local, remote=[]) == []

        remote = [_entry(url="http://10.0.0.2:11434/v1")]
        result = reconcile(local, remote=remote)
        assert result[0]["url"] == "http://10.0.0.2:11434/v1"
        assert result[0]["enabled"] is False

    def test_marked_entries_go_away_for_users_who_left_them_off(self) -> None:
        local = [_entry(enabled=False)]
        remote = [_entry(status=STATUS_DEAD)]
        assert reconcile(local, remote=remote) == []

    def test_a_marked_entry_stays_for_the_one_who_enabled_it(self) -> None:
        local = [_entry(enabled=True)]
        remote = [_entry(status=STATUS_DISABLED)]
        result = reconcile(local, remote=remote)
        assert [entry["id"] for entry in result] == ["ollama"]

    def test_priority_decides_the_order(self) -> None:
        local: list[dict] = []
        remote = [
            _entry(id="slow", url="https://s.example/v1", fallbackPriority=5),
            _entry(id="fast", url="https://f.example/v1", fallbackPriority=1),
        ]
        assert [entry["id"] for entry in reconcile(local, remote=remote)] == [
            "fast",
            "slow",
        ]


class TestWithLocalEnabled:
    def test_an_enabled_entry_outlives_the_catalogue(self) -> None:
        """Правило продукта: человек включил сам — выключает сам."""
        listed = [_entry(id="openai", name="OpenAI", url="https://api.openai.com/v1")]
        local = [_entry(enabled=True), _entry(id="openai", name="OpenAI", url="https://api.openai.com/v1")]
        result = with_local_enabled(listed, local)
        assert [entry["id"] for entry in result] == ["ollama", "openai"]

    def test_a_disabled_vanished_entry_stays_gone(self) -> None:
        listed = [_entry(id="openai", name="OpenAI", url="https://api.openai.com/v1")]
        local = [_entry(enabled=False)]
        assert [entry["id"] for entry in with_local_enabled(listed, local)] == ["openai"]

    def test_the_catalogue_copy_wins_over_the_local_one(self) -> None:
        """Запись на месте: живые данные каталога новее снапшота."""
        listed = [_entry(url="http://new-host:11434/v1", enabled=True)]
        local = [_entry(url="http://old-host:11434/v1", enabled=True)]
        result = with_local_enabled(listed, local)
        assert [entry["url"] for entry in result] == ["http://new-host:11434/v1"]

    def test_an_enabled_entry_keeps_its_mark(self) -> None:
        listed = [_entry()]
        local = [_entry(enabled=True)]
        result = with_local_enabled(listed, local)
        assert result[0]["enabled"] is True

    def test_empty_local_makes_every_flag_explicit(self) -> None:
        listed = [_entry()]
        result = with_local_enabled(listed, [])
        assert [entry["id"] for entry in result] == ["ollama"]
        assert result[0]["enabled"] is False


class TestPropose:
    def test_a_full_entry_comes_back_ready(self) -> None:
        entry, error = propose(
            {
                "name": "Мой LM Studio",
                "url": "http://192.168.0.5:1234/v1",
                "model": "qwen3-8b",
                "loginUrl": "http://192.168.0.5:1234",
            }
        )
        assert error == ""
        assert entry is not None
        assert entry["name"] == "Мой LM Studio"
        assert entry["url"] == "http://192.168.0.5:1234/v1"
        assert entry["model"] == "qwen3-8b"
        # Предложение — всегда от автора и всегда рабочее по умолчанию.
        assert entry["origin"] == "user"
        assert entry["status"] == STATUS_ACTIVE

    def test_the_id_is_made_from_the_name(self) -> None:
        entry, _ = propose({"name": "My LM Studio", "url": "http://x/v1", "model": "m"})
        assert entry is not None and entry["id"] == "my-lm-studio"

    def test_a_russian_name_gets_a_readable_id(self) -> None:
        entry, _ = propose({"name": "Мой Ollama", "url": "http://x/v1", "model": "m"})
        assert entry is not None and entry["id"] == "moy-ollama"

    def test_an_explicit_id_wins(self) -> None:
        entry, _ = propose({"id": "home.llm", "name": "Дом", "url": "http://x/v1", "model": "m"})
        assert entry is not None and entry["id"] == "home.llm"

    def test_a_missing_name_is_refused(self) -> None:
        entry, error = propose({"url": "http://x/v1", "model": "m"})
        assert entry is None and "имя" in error

    def test_a_missing_url_is_refused_because_the_server_would_drop_it(self) -> None:
        entry, error = propose({"name": "X", "model": "m"})
        assert entry is None and "адрес" in error

    def test_a_url_without_a_scheme_is_refused(self) -> None:
        entry, error = propose({"name": "X", "url": "localhost:11434/v1", "model": "m"})
        assert entry is None and "http" in error

    def test_a_missing_model_is_refused_because_the_server_would_drop_it(self) -> None:
        entry, error = propose({"name": "X", "url": "http://x/v1"})
        assert entry is None and "модель" in error

    def test_an_unusable_id_is_refused(self) -> None:
        entry, error = propose({"id": "плохой id!", "name": "X", "url": "http://x/v1", "model": "m"})
        assert entry is None and "id" in error

    def test_non_dict_input_is_not_a_crash(self) -> None:
        entry, error = propose("просто строка")
        assert entry is None and error


class TestSlug:
    def test_punctuation_collapses_to_one_dash(self) -> None:
        from core.catalogue import _slug

        assert _slug("Qwen 3:8B (local)") == "qwen-3-8b-local"

    def test_an_unusable_name_leaves_nothing(self) -> None:
        from core.catalogue import _slug

        assert _slug("!!!") == ""


class TestCanRemove:
    def test_the_superuser_removes_anything(self) -> None:
        from core.catalogue import can_remove

        assert can_remove(_entry(origin="admin"), is_superuser=True) is True

    def test_a_user_removes_only_their_own_proposal(self) -> None:
        from core.catalogue import can_remove

        assert can_remove(_entry(origin="user", mine=True), is_superuser=False) is True

    def test_a_user_cannot_remove_a_catalogue_entry(self) -> None:
        """Сервер бы его сохранил — команда не должна солгать «удалено»."""
        from core.catalogue import can_remove

        assert can_remove(_entry(origin="admin"), is_superuser=False) is False
        assert can_remove(_entry(origin="user", mine=False), is_superuser=False) is False


class TestNextPriority:
    def test_the_first_provider_goes_after_the_last(self) -> None:
        assert next_priority([]) == 1
        assert next_priority([_entry(fallbackPriority=6)]) == 7

    def test_a_garbage_priority_does_not_crash(self) -> None:
        assert next_priority([_entry(fallbackPriority="много")]) == 1


class TestSetModel:
    def test_the_model_changes_in_place(self) -> None:
        updated, error = set_model([_entry()], "ollama", "qwen3:0.6b")
        assert error == ""
        assert updated[0]["model"] == "qwen3:0.6b"

    def test_other_entries_stay_untouched(self) -> None:
        other = _entry(id="openai", name="OpenAI", url="https://o/v1", model="gpt-4o")
        updated, _ = set_model([_entry(), other], "ollama", "qwen3")
        assert updated[1]["model"] == "gpt-4o"

    def test_an_unknown_provider_is_refused(self) -> None:
        updated, error = set_model([_entry()], "нет", "m")
        assert updated is None and "не найден" in error

    def test_an_empty_model_is_refused(self) -> None:
        updated, error = set_model([_entry()], "ollama", "   ")
        assert updated is None and "пустой" in error


class TestCanEditModel:
    def test_the_superuser_edits_anything(self) -> None:
        assert can_edit_model(_entry(origin="admin"), is_superuser=True) is True

    def test_a_user_edits_only_own_proposal(self) -> None:
        assert can_edit_model(_entry(origin="user", mine=True), is_superuser=False) is True
        assert can_edit_model(_entry(origin="admin"), is_superuser=False) is False
        assert can_edit_model(_entry(origin="user", mine=False), is_superuser=False) is False


class TestEditEntry:
    def test_fields_change_and_the_rest_survives(self) -> None:
        updated, error = edit_entry(
            [_entry()], "ollama", {"name": "Мой Ollama", "model": "qwen3"}
        )
        assert error == ""
        assert updated[0]["name"] == "Мой Ollama"
        assert updated[0]["model"] == "qwen3"
        assert updated[0]["url"] == "http://localhost:11434/v1"  # не тронуто

    def test_empty_fields_do_not_erase(self) -> None:
        """Пустой input в форме не должен стирать значение.

        Пустые поля отбрасываются до правки; если пусто всё — правка
        отклоняется, а не молча стирает запись.
        """
        updated, _ = edit_entry(
            [_entry()], "ollama", {"model": "  ", "name": "  ", "url": ""}
        )
        assert updated is None  # всё пустое → отклонено, ничего не стёрто

        updated, _ = edit_entry([_entry()], "ollama", {"model": "qwen3", "name": "  "})
        assert updated[0]["name"] == "Ollama"  # пустое имя не тронуто
        assert updated[0]["model"] == "qwen3"

    def test_a_bad_priority_is_refused(self) -> None:
        updated, error = edit_entry([_entry()], "ollama", {"fallbackPriority": "дорого"})
        assert updated is None and "числом" in error

    def test_a_negative_priority_clamps_to_zero(self) -> None:
        updated, _ = edit_entry([_entry()], "ollama", {"fallbackPriority": "-5"})
        assert updated[0]["fallbackPriority"] == 0

    def test_erasing_the_name_is_refused(self) -> None:
        """Без имени сервер отбросил бы запись — правка не должна это делать."""
        updated, error = edit_entry([_entry()], "ollama", {"name": " x ", "url": "http://x/v1"})
        assert error == ""  # name запасной, нормализация берёт id
        assert updated[0]["name"] in ("x", "ollama")

    def test_an_unknown_provider_is_refused(self) -> None:
        updated, error = edit_entry([_entry()], "нет", {"model": "m"})
        assert updated is None and "не найден" in error

    def test_no_fields_is_refused(self) -> None:
        updated, error = edit_entry([_entry()], "ollama", {})
        assert updated is None and "нечего" in error
