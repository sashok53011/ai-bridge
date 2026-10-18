"""Страница приставки: разметка из данных.

Главное свойство — чужие слова не становятся разметкой: имя провайдера
с прямыми скобками или адрес с кавычкой не должны ломать страницу.
"""

from core.page import (
    catalogue_page,
    edit_page,
    key_page,
    login_page,
    pick_model_page,
    signup_page,
    test_page as chat_test_page,  # имя «test_*» путает pytest с тестами
    token_created_page,
    tokens_page,
    users_page,
)


def _entry(**kwargs) -> dict:
    base = {
        "id": "ollama",
        "name": "Ollama",
        "url": "http://localhost:11434/v1",
        "model": "llama3.2",
        "status": "active",
        "origin": "admin",
        "enabled": False,
        "mine": False,
    }
    base.update(kwargs)
    return base


class TestCataloguePage:
    def test_every_provider_is_a_row_with_its_address(self) -> None:
        page = catalogue_page([_entry(), _entry(id="b", name="B", url="https://b/v1")])
        assert "Ollama" in page
        assert "http://localhost:11434/v1" in page
        assert "https://b/v1" in page

    def test_a_switched_on_provider_carries_its_badge(self) -> None:
        page = catalogue_page([_entry(enabled=True)])
        assert "вкл" in page

    def test_a_dead_provider_is_marked(self) -> None:
        page = catalogue_page([_entry(status="dead")])
        assert "нераб." in page

    def test_a_foreign_name_cannot_break_the_page(self) -> None:
        """Прямые скобки и кавычки в имени — не разметка, а текст."""
        page = catalogue_page([_entry(name='<script>alert("x")</script>')])
        assert "<script>alert" not in page
        assert "&lt;script&gt;" in page

    def test_a_foreign_url_is_escaped_too(self) -> None:
        page = catalogue_page([_entry(url='http://x/" onmouseover="evil()')])
        assert 'onmouseover="evil()' not in page

    def test_the_add_form_is_an_admin_affair(self) -> None:
        page = catalogue_page([_entry()], can_edit=True)
        assert 'action="/add"' in page
        assert 'name="name"' in page
        assert 'name="url"' in page
        assert 'name="model"' in page
        assert 'name="api_key"' in page
        # Человек управляет лишь своим пространством — каталог не его.
        assert 'action="/add"' not in catalogue_page([_entry()], can_edit=False)

    def test_every_row_can_be_toggled_and_checked(self) -> None:
        page = catalogue_page([_entry()])
        assert 'action="/toggle/ollama"' in page
        assert 'action="/check/ollama"' in page

    def test_every_row_offers_the_personal_tools(self) -> None:
        """Каждому доступны ключ, выбор своей модели и тест-чат."""
        page = catalogue_page([_entry()], can_edit=False)
        assert 'href="/key/ollama"' in page
        assert 'href="/pick/ollama"' in page
        assert 'action="/test/ollama"' in page

    def test_an_admin_sees_remove_and_a_user_does_not(self) -> None:
        assert 'action="/remove/ollama"' in catalogue_page([_entry()], can_edit=True)
        assert 'action="/remove/ollama"' not in catalogue_page([_entry()], can_edit=False)

    def test_a_user_does_not_get_editing_even_for_their_proposal(self) -> None:
        """Правка определений — привилегия админа; человек — своё пространство."""
        page = catalogue_page([_entry(origin="user", mine=True)], can_edit=False)
        assert 'action="/remove/ollama"' not in page
        assert 'href="/edit/ollama"' not in page

    def test_the_key_column_says_only_what_is_true(self) -> None:
        assert ">задан<" in catalogue_page([_entry()], keyed_ids=frozenset({"ollama"}))
        assert ">задан<" not in catalogue_page([_entry()], keyed_ids=frozenset())

    def test_a_notice_is_escaped_text(self) -> None:
        page = catalogue_page([_entry()], notice='<img src=x onerror=evil()>')
        assert "<img src=x" not in page
        assert "&lt;img" in page

    def test_an_empty_catalogue_says_so(self) -> None:
        assert "каталог пуст" in catalogue_page([])


class TestEditPage:
    def test_the_form_offers_every_editable_field(self) -> None:
        page = edit_page(_entry(pricing="$0.1/1M", context="128K", capabilities="vision"))
        for name in (
            "name", "url", "model", "fallbackPriority",
            "loginUrl", "getApiKeyUrl", "context", "pricing", "capabilities",
        ):
            assert f'name="{name}"' in page

    def test_current_values_are_prefilled(self) -> None:
        page = edit_page(_entry(name="Мой Ollama", pricing="$0.2/1M"))
        assert 'value="Мой Ollama"' in page
        assert 'value="$0.2/1M"' in page

    def test_foreign_values_are_escaped(self) -> None:
        page = edit_page(_entry(name='<script>alert("x")</script>'))
        assert "<script>alert" not in page

    def test_a_fresh_entry_asks_with_placeholders(self) -> None:
        page = edit_page(_entry())
        assert "128K" in page  # плейсхолдер контекста
        assert "vision" in page  # плейсхолдер способностей


class TestFacts:
    def test_pricing_context_and_capabilities_show_under_the_model(self) -> None:
        page = catalogue_page(
            [_entry(context="128K", pricing="$0.1/1M", capabilities="vision, tools")]
        )
        assert "128K" in page
        assert "$0.1/1M" in page
        assert "vision, tools" in page

    def test_an_empty_facts_block_is_absent(self) -> None:
        page = catalogue_page([_entry()])
        assert 'class="facts"' not in page

    def test_facts_are_escaped(self) -> None:
        page = catalogue_page([_entry(pricing="<img onerror=x>")])
        assert "<img onerror" not in page

    def test_every_row_offers_editing_to_the_admin(self) -> None:
        page = catalogue_page([_entry()], can_edit=True)
        assert 'href="/edit/ollama"' in page
        assert 'href="/edit/ollama"' not in catalogue_page([_entry()], can_edit=False)


class TestKeyPage:
    def test_the_form_carries_the_provider_id_not_its_name(self) -> None:
        """Регрессия 17.10: скрытое поле хранило имя — ключ не сохранялся."""
        page = key_page("Ollama", provider_id="ollama", has_key=False)
        assert 'value="ollama"' in page
        assert "Ollama" in page  # имя — в заголовке, для человека

    def test_every_secondary_page_keeps_the_navigation(self) -> None:
        """Со страниц ключа/модели/теста есть выход и «мои токены»."""
        for page in (
            key_page("Ollama", provider_id="ollama", has_key=False),
            pick_model_page("Ollama", "ollama", ["qwen3"]),
            chat_test_page("Ollama", "ollama", "привет", answer="ок"),
        ):
            assert 'href="/tokens"' in page
            assert 'action="/logout"' in page

    def test_the_key_page_links_to_the_providers_own_pages(self) -> None:
        page = key_page(
            "Ollama", provider_id="ollama", has_key=False,
            key_url="https://ollama.com/api", login_url="https://ollama.com/login",
        )
        assert 'href="https://ollama.com/api"' in page
        assert "получить ключ" in page
        assert 'href="https://ollama.com/login"' in page

    def test_a_dangerous_scheme_is_not_a_link(self) -> None:
        page = key_page(
            "X", provider_id="x", has_key=False,
            key_url="javascript:alert(1)", login_url="ftp://чужое",
        )
        assert "javascript:" not in page
        assert "ftp:" not in page


class TestProviderLinks:
    def test_the_row_links_to_login_and_key_pages_when_known(self) -> None:
        page = catalogue_page(
            [_entry(loginUrl="https://o.com/login", getApiKeyUrl="https://o.com/keys")]
        )
        assert 'href="https://o.com/login"' in page
        assert 'href="https://o.com/keys"' in page
        assert "получить ключ" in page

    def test_no_fields_no_links(self) -> None:
        page = catalogue_page([_entry()])
        assert "получить ключ" not in page
        assert "страница логина" not in page


class TestAppAddress:
    def test_the_catalogue_shows_a_copyable_endpoint_line(self) -> None:
        page = catalogue_page([_entry()], app_address="https://ai.devhorizon.online/v1")
        assert 'value="https://ai.devhorizon.online/v1"' in page
        assert "clipboard" in page  # клик копирует в буфер
        assert "клик — копировать" in page

    def test_no_address_no_line(self) -> None:
        assert "clipboard" not in catalogue_page([_entry()])

    def test_the_tokens_page_is_a_setup_guide_with_a_switch(self) -> None:
        page = tokens_page(
            [{"name": "телефон", "tail": "Ab12Cd", "created": "2026-10-16"}],
            app_address="https://ai.devhorizon.online/v1",
            api_on=True,
        )
        assert "Приложения и токены" in page
        assert 'value="https://ai.devhorizon.online/v1"' in page
        assert 'action="/tokens/api"' in page
        assert "выключить доступ" in page
        assert "OpenAI" in page  # тип endpoint для настройки

    def test_a_switched_off_page_says_it_loudly(self) -> None:
        page = tokens_page([], api_on=False)
        assert "ВЫКЛЮЧЕН" in page
        assert "включить доступ" in page

    def test_the_page_asks_for_the_key(self) -> None:
        page = key_page("OpenAI", provider_id="openai", has_key=False)
        assert 'name="api_key"' in page
        assert "OpenAI" in page

    def test_a_set_key_is_admitted_without_being_shown(self) -> None:
        page = key_page("OpenAI", provider_id="openai", has_key=True)
        assert "задан" in page
        assert "sk-" not in page  # сам ключ на страницу не попадает никогда

    def test_the_provider_name_is_escaped(self) -> None:
        page = key_page('<b>жирный</b>', provider_id="x", has_key=False)
        assert "<b>жирный</b>" not in page


class TestAuthPages:
    def test_the_login_form_asks_for_email_and_password(self) -> None:
        page = login_page()
        assert 'name="email"' in page
        assert 'name="password"' in page

    def test_the_token_stays_as_an_emergency_way_in(self) -> None:
        assert 'name="token"' in login_page()

    def test_registration_is_offered_when_the_server_allows_it(self) -> None:
        assert 'href="/signup"' in login_page()
        assert 'href="/signup"' not in login_page(signup_open=False)

    def test_the_signup_form_asks_to_repeat_the_password(self) -> None:
        page = signup_page()
        assert 'name="password"' in page
        assert 'name="password2"' in page

    def test_a_login_error_is_visible_and_escaped(self) -> None:
        page = login_page(notice="нет <такого> аккаунта")
        assert "нет &lt;такого&gt;" in page


class TestTokensPage:
    def test_a_token_is_shown_once_and_in_full(self) -> None:
        page = token_created_page("uib-abcdef", "телефон")
        assert "uib-abcdef" in page
        assert "телефон" in page

    def test_the_list_shows_tails_not_the_whole_token(self) -> None:
        page = tokens_page([{"name": "телефон", "tail": "Ab12Cd", "created": "2026-10-16"}])
        assert "Ab12Cd" in page
        assert "uib-" not in page  # полный токен в список не возвращается
        assert 'action="/tokens/revoke"' in page

    def test_an_empty_token_list_says_so(self) -> None:
        assert "токенов пока нет" in tokens_page([])


class TestUsersPage:
    def test_the_admin_sees_people_and_a_delete_button(self) -> None:
        page = users_page(
            [{"id": "u1", "email": "a@b.c", "role": "user", "lastLogin": "2026-10-16"}],
            current_id="u0",
        )
        assert "a@b.c" in page
        assert 'action="/users/delete"' in page

    def test_the_admin_cannot_delete_themselves(self) -> None:
        page = users_page([{"id": "u0", "email": "me@b.c", "role": "admin"}], current_id="u0")
        assert 'action="/users/delete"' not in page


class TestPickAndTestPages:
    def test_picking_offers_models_with_the_current_one_checked(self) -> None:
        page = pick_model_page("Ollama", "ollama", ["qwen3", "llama3"], current="llama3")
        assert 'value="qwen3"' in page
        assert "checked" in page
        assert 'action="/pick/ollama"' in page

    def test_picking_without_models_points_at_the_key(self) -> None:
        page = pick_model_page("OpenAI", "openai", [])
        assert "проверьте ключ" in page

    def test_a_test_page_shows_the_answer_or_the_error(self) -> None:
        ok = chat_test_page("Ollama", "qwen3", "привет", answer="ок")
        assert "ок" in ok
        bad = chat_test_page("Ollama", "qwen3", "привет", error="нет ключа")
        assert "нет ключа" in bad
