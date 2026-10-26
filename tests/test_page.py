"""Страница приставки: разметка из данных.

Главное свойство — чужие слова не становятся разметкой: имя провайдера
с прямыми скобками или адрес с кавычкой не должны ломать страницу.
"""

from core.page import (
    add_page,
    autotest_page,
    autotest_results_html,
    bugs_page,
    catalogue_page,
    edit_page,
    key_page,
    login_page,
    logs_page,
    messages_page,
    oauth_page,
    pick_model_page,
    signup_page,
    stats_page,
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
    def test_every_provider_is_a_row_with_its_name(self) -> None:
        page = catalogue_page([_entry(), _entry(id="b", name="B", url="https://b/v1")])
        assert "Ollama" in page
        assert "B" in page

    def test_addresses_are_hidden_everywhere(self) -> None:
        """Адрес провайдера не показывается никому — только в формах админа."""
        page = catalogue_page([_entry(url="http://secret-endpoint.internal:9999/v1")])
        assert "secret-endpoint" not in page
        assert "http://" not in page

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

    def test_a_foreign_url_never_reaches_the_page(self) -> None:
        """URL не рендерится вовсе — даже с дурными схемами нет шанса."""
        page = catalogue_page([_entry(url='http://x/" onmouseover="evil()')])
        assert "onmouseover" not in page

    def test_everyone_gets_an_add_link_not_a_form(self) -> None:
        """Добавить может каждый: со страницы — ссылка на полную форму."""
        for can_edit in (True, False):
            page = catalogue_page([_entry()], can_edit=can_edit)
            assert 'href="/add"' in page
            assert 'action="/add"' not in page  # форма живёт на отдельной странице

    def test_every_row_can_be_toggled_and_tested(self) -> None:
        """Строка: тумблер вкл/выкл и кнопка «тест» (она же проверка)."""
        page = catalogue_page([_entry()])
        assert 'action="/toggle/ollama"' in page
        assert 'action="/test/ollama"' in page
        assert "/check/" not in page  # отдельной кнопки проверки больше нет

    def test_the_toggle_is_a_switch_with_a_state(self) -> None:
        """Тумблер — role=switch; включённый провайдер помечен on."""
        on = catalogue_page([_entry(enabled=True)])
        assert 'class="switch on"' in on
        assert 'aria-checked="true"' in on
        off = catalogue_page([_entry(enabled=False)])
        assert 'class="switch off"' in off
        assert 'aria-checked="false"' in off

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

    def test_the_catalogue_has_no_model_or_checks_columns(self) -> None:
        """Каталог — про провайдеров: имя, ключ, кнопки. Модель и цифры
        убраны (они на статистике), чтобы строка не росла по вертикали."""
        page = catalogue_page([_entry(model="llama3.2")])
        assert "<th>модель</th>" not in page
        assert "<th>проверки</th>" not in page
        assert "<th>ключ</th>" in page
        # Название провайдера на месте, кнопки не тронуты.
        assert "Ollama" in page
        assert 'action="/test/ollama"' in page
        assert 'href="/key/ollama"' in page

    def test_the_key_column_is_still_there(self) -> None:
        page = catalogue_page([_entry()], keyed_ids=frozenset({"ollama"}))
        assert "<th>ключ</th>" in page
        assert ">задан<" in page


class TestEditPage:
    def test_the_form_offers_only_the_definition(self) -> None:
        """Правка — короткая: имя, адрес, модель, приоритет, где взять ключ."""
        page = edit_page(_entry(pricing="$0.1/1M", context="128K", capabilities="vision"))
        for name in ("name", "url", "model", "fallbackPriority", "getApiKeyUrl"):
            assert f'name="{name}"' in page

    def test_the_reference_fields_are_gone(self) -> None:
        """Справка, логотипы и страницы провайдера убраны из правки."""
        page = edit_page(
            _entry(loginUrl="https://aistudio.google.com/",
                   infoUrl="https://o.com/p", pricing="$1", context="128K",
                   capabilities="vision", logoUrl="https://a/l.png")
        )
        for name in ("loginUrl", "infoUrl", "context", "pricing",
                     "capabilities", "logoUrl"):
            assert f'name="{name}"' not in page
        # Данные при этом не показываются и не стираются — просто не правятся.
        assert "$1" not in page
        assert "aistudio.google.com" not in page

    def test_current_values_are_prefilled(self) -> None:
        page = edit_page(_entry(name="Мой Ollama", model="qwen3"))
        assert 'value="Мой Ollama"' in page
        assert 'value="qwen3"' in page

    def test_foreign_values_are_escaped(self) -> None:
        page = edit_page(_entry(name='<script>alert("x")</script>'))
        assert "<script>alert" not in page

    def test_the_model_placeholder_is_offered(self) -> None:
        page = edit_page(_entry())
        assert "qwen3:0.6b" in page  # плейсхолдер модели

    def test_the_key_field_and_fetch_button_live_here(self) -> None:
        """Ключ и «показать модели» — прямо в правке: список доступен с места."""
        page = edit_page(_entry())
        assert 'name="api_key"' in page
        assert 'type="password"' in page  # ключ не читается на экране
        assert 'name="action" value="fetch"' in page
        assert "показать модели" in page

    def test_a_set_key_is_advertised_but_not_echoed(self) -> None:
        """Заданный ключ виден по названию, значение — никогда."""
        page = edit_page(_entry(), has_key=True)
        assert "сейчас задан" in page
        assert 'value=""' in page  # поле пустое: секрет не печатается

    def test_entered_values_survive_a_fetch(self) -> None:
        """После «показать модели» введённое не теряется."""
        page = edit_page(
            _entry(),
            values={"url": "https://api.githubcopilot.com", "api_key": "gho_x"},
            models=["gpt-5", "o4-mini"],
        )
        assert 'value="https://api.githubcopilot.com"' in page
        assert 'value="gho_x"' in page
        assert '<select name="model">' in page
        assert "<option" in page and "gpt-5" in page

    def test_a_fetch_failure_is_explained_in_place(self) -> None:
        page = edit_page(_entry(), models_error="без ключа список недоступен")
        assert "без ключа список недоступен" in page

    def test_the_current_model_stays_in_a_fetched_list(self) -> None:
        """Текущая модель попадает в список, даже если её там нет."""
        page = edit_page(_entry(model="старая"), models=["gpt-5"])
        assert "старая" in page
        assert 'selected' in page

    def test_the_github_login_button_only_for_copilot(self) -> None:
        page = edit_page(_entry(), oauth=True)
        assert 'action="/oauth/ollama"' in page
        assert "Войти через GitHub" in page
        plain = edit_page(_entry(), oauth=False)
        assert "/oauth/" not in plain


class TestOauthPage:
    def test_the_code_and_link_are_the_centerpiece(self) -> None:
        page = oauth_page(
            "GitHub Copilot", provider_id="github-copilot-acp",
            user_code="ABCD-1234", verify_url="https://github.com/login/device",
        )
        assert "ABCD-1234" in page
        assert 'href="https://github.com/login/device"' in page
        assert 'action="/oauth/github-copilot-acp"' in page
        assert 'name="action" value="check"' in page

    def test_a_foreign_verification_url_falls_back_to_github(self) -> None:
        """Адрес приходит от GitHub, но чужая схема не становится ссылкой."""
        page = oauth_page(
            "P", provider_id="x", user_code="A-1",
            verify_url="javascript:alert(1)",
        )
        assert "javascript:" not in page
        assert "github.com/login/device" in page

    def test_the_page_polls_itself_while_open(self) -> None:
        page = oauth_page("P", provider_id="x", user_code="A-1", verify_url="https://g/d")
        assert "setTimeout" in page  # скрипт сам жмёт «Проверить»
        assert "oauth-check" in page

    def test_the_poll_interval_comes_from_github(self) -> None:
        """Интервал GitHub (в секундах) попадает в скрипт, а не в магию."""
        page = oauth_page("P", provider_id="x", user_code="A-1",
                          verify_url="https://g/d", interval=10)
        assert "10000" in page
        fast = oauth_page("P", provider_id="x", user_code="A-1",
                          verify_url="https://g/d", interval=5)
        assert "5000" in fast

    def test_foreign_words_are_escaped(self) -> None:
        page = oauth_page(
            '<img src=x>', provider_id="x", user_code="<script>1</script>",
            verify_url="https://github.com/login/device",
        )
        assert "<img" not in page
        assert "<script>1" not in page


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

    def test_the_github_login_button_appears_only_when_it_works(self) -> None:
        """Кнопка OAuth — только там, где токен GitHub вообщегодится."""
        page = key_page("GitHub Copilot", provider_id="gh", has_key=False, oauth=True)
        assert 'action="/oauth/gh"' in page
        assert "Войти через GitHub" in page
        plain = key_page("Ollama", provider_id="ollama", has_key=False, oauth=False)
        assert "/oauth/" not in plain


class TestProviderLinks:
    def test_the_catalogue_row_shows_get_key_when_available(self) -> None:
        """Получение ключа — кнопка «get key» в строке каталога, если URL задан.

        Адрес самого провайдера (loginUrl) в строке не дублируется — он
        остаётся на странице ключа (кнопка «ключ»).
        """
        page = catalogue_page(
            [_entry(loginUrl="https://o.com/login", getApiKeyUrl="https://o.com/keys")]
        )
        assert "get key" in page  # кнопка получения ключа
        assert "https://o.com/keys" in page  # URL в ссылке
        # Вход у провайдера — не дублируется в строке каталога.
        assert "https://o.com/login" not in page
        assert "вход у провайдера" not in page

    def test_no_get_key_button_without_url(self) -> None:
        page = catalogue_page([_entry()])
        assert "get key" not in page

    def test_a_dangerous_scheme_is_not_a_link(self) -> None:
        page = key_page(
            "X", provider_id="x", has_key=False,
            key_url="javascript:alert(1)", login_url="ftp://чужое",
        )
        assert "javascript:" not in page
        assert "ftp:" not in page


class TestAppAddress:
    def test_the_catalogue_shows_a_copyable_endpoint_line(self) -> None:
        page = catalogue_page([_entry()], app_address="https://ai.devhorizon.online/v1")
        assert ">https://ai.devhorizon.online/v1<" in page  # текст, не поле ввода
        assert 'id="v1addr"' in page
        assert "копировать" in page  # отдельная кнопка
        assert "function cp(" in page  # и скрипт копирования

    def test_no_address_no_line(self) -> None:
        page = catalogue_page([_entry()])
        assert "function cp(" not in page
        assert "копировать</button>" not in page

    def test_the_tokens_page_is_a_setup_guide_with_a_switch(self) -> None:
        page = tokens_page(
            [{"name": "телефон", "tail": "Ab12Cd", "created": "2026-10-16"}],
            app_address="https://ai.devhorizon.online/v1",
            api_on=True,
        )
        assert "Приложения и токены" in page
        assert ">https://ai.devhorizon.online/v1<" in page
        assert 'id="v1big"' in page
        assert "function cp(" in page
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


class TestBugsPage:
    """Нашёл bug: отдельный раздел для всех, разговор живёт на сервере."""

    @staticmethod
    def _post(**kwargs) -> dict:
        base = {
            "id": "p1",
            "ts": "2026-10-25T09:00:00Z",
            "author": "a@b.c",
            "text": "страница падает",
            "history": [],
            "comments": [],
        }
        base.update(kwargs)
        return base

    def test_the_page_exists_for_everyone_with_the_board(self) -> None:
        page = bugs_page([self._post()])
        assert "Нашёл bug" in page
        assert 'id="bug-input"' in page
        assert 'id="bug-chat"' in page
        # Для всех вошедших: пункт виден и не спрятан в админский блок.
        assert 'href="/bugs"' in page
        assert "страница падает" in page  # сообщение с сервера

    def test_actions_go_through_the_server_routes(self) -> None:
        """Каждое действие — POST на сервер: общий разговор для всех."""
        page = bugs_page([self._post()], can_edit=True)
        for route in ("/bugs/post", "/bugs/comment", "/bugs/status", "/bugs/drop"):
            assert f'action="{route}"' in page, route

    def test_the_status_set_requires_a_reason(self) -> None:
        """Статус без пояснения не сохраняется — так сказано прямо на странице."""
        page = bugs_page([self._post()])
        assert "только с пояснением" in page
        assert 'placeholder="почему именно так' in page  # поле причины обязательно
        # required стоит у обоих полей: браузер не даст отправить пустыми.
        assert page.count("required") >= 3
        for status in ("ждёт доработки", "дорабатывается",
                       "был доработан", "не может быть доработан"):
            assert status in page

    def test_the_current_status_shows_its_reason(self) -> None:
        """Текущий статус: бейдж, причина, кто и когда поставил."""
        post = self._post(history=[
            {"key": "wait", "why": "принято", "by": "b@b.c", "ts": "2026-10-25T09:10:00Z"},
        ])
        page = bugs_page([post])
        assert "почему: принято" in page
        assert "b@b.c" in page
        assert "badge-off" in page

    def test_a_status_trail_is_kept_but_collapsed(self) -> None:
        """Смена статусов видна (с прошлыми пояснениями), но не мешает."""
        post = self._post(history=[
            {"key": "wait", "why": "принято", "by": "b@b.c", "ts": "2026-10-25T09:10:00Z"},
            {"key": "done", "why": "чиню сегодня", "by": "b@b.c", "ts": "2026-10-25T09:20:00Z"},
        ])
        page = bugs_page([post])
        assert "история статусов (1)" in page
        assert "принято" in page  # прошлое пояснение не потеряно

    def test_comments_are_shown_with_their_authors(self) -> None:
        post = self._post(comments=[
            {"ts": "2026-10-25T09:05:00Z", "by": "x@b.c", "text": "вижу тоже"},
        ])
        page = bugs_page([post])
        assert "вижу тоже" in page
        assert "x@b.c" in page

    def test_times_carry_the_utc_marker_for_local_display(self) -> None:
        """Время — UTC в data-ts, страница переведёт в время зрителя."""
        page = bugs_page([self._post()])
        assert 'data-ts="2026-10-25T09:00:00Z"' in page
        assert "window.localTimes" in page

    def test_the_drop_button_is_for_the_admin_only(self) -> None:
        assert 'action="/bugs/drop"' in bugs_page([self._post()], can_edit=True)
        assert 'action="/bugs/drop"' not in bugs_page([self._post()], can_edit=False)

    def test_the_tokens_page_keeps_its_board_out(self) -> None:
        """Страница токенов конфиденциальна — доски багов там нет."""
        page = tokens_page([])
        assert 'id="bug-chat"' not in page
        assert "Нашёл bug" not in page


class TestMessagesPage:
    """Почта: свой ящик, написать любому, админу — рассылка и чужие ящики."""

    @staticmethod
    def _people() -> list[dict]:
        return [
            {"id": "me", "email": "me@b.c"},
            {"id": "u2", "email": "u2@b.c"},
            {"id": "u3", "email": "u3@b.c"},
        ]

    def test_everyone_sees_an_inbox_and_a_compose_form(self) -> None:
        page = messages_page([], people=self._people(), viewer_id="me")
        assert 'action="/messages/send"' in page
        assert 'name="to"' in page
        assert 'name="text"' in page
        assert "ящик пуст" in page

    def test_a_person_can_write_to_anyone_but_not_to_self(self) -> None:
        page = messages_page([], people=self._people(), viewer_id="me")
        assert "u2@b.c" in page
        assert "u3@b.c" in page
        assert "me@b.c</option>" not in page  # самому себе писать незачем

    def test_the_broadcast_option_is_for_the_admin_only(self) -> None:
        assert "рассылка" in messages_page([], people=self._people(), viewer_id="me",
                                           can_edit=True)
        assert "рассылка" not in messages_page([], people=self._people(), viewer_id="me",
                                               can_edit=False)

    def test_a_letter_is_shown_with_sender_and_time(self) -> None:
        page = messages_page(
            [{"id": "m1", "ts": "2026-10-25T10:00:00Z", "from_email": "u2@b.c",
              "to": "me", "text": "привет"}],
            people=self._people(), viewer_id="me",
        )
        assert "u2@b.c" in page
        assert "привет" in page
        assert 'data-ts="2026-10-25T10:00:00Z"' in page
        assert "window.localTimes" in page

    def test_the_refusal_switch_offers_both_states(self) -> None:
        on = messages_page([], people=self._people(), viewer_id="me", mail_on=True)
        off = messages_page([], people=self._people(), viewer_id="me", mail_on=False)
        assert 'action="/messages/mail"' in on
        assert "не получать сообщения" in on
        assert "снова получать сообщения" in off
        assert "не закрывается" in off  # обещание: ящик остаётся

    def test_rerouted_mail_is_marked_for_the_admin(self) -> None:
        """Письмо «за адресата» видно: полная власть — знать, за кого."""
        page = messages_page(
            [{"id": "m1", "ts": "2026-10-25T10:00:00Z", "from_email": "u2@b.c",
              "to": "admin", "orig_to": "u3@b.c", "text": "не туда"}],
            people=self._people(), viewer_id="me", can_edit=True,
        )
        assert "за адресата" in page
        assert "u3@b.c" in page

    def test_deleting_letters_is_the_admins_alone(self) -> None:
        letters = [{"id": "m1", "ts": "2026-10-25T10:00:00Z", "from_email": "u2@b.c",
                    "to": "me", "text": "x"}]
        assert 'action="/messages/drop"' in messages_page(
            letters, people=self._people(), viewer_id="me", can_edit=True
        )
        assert 'action="/messages/drop"' not in messages_page(
            letters, people=self._people(), viewer_id="me", can_edit=False
        )

    def test_the_admin_can_open_anyones_inbox(self) -> None:
        page = messages_page([], people=self._people(), viewer_id="me", can_edit=True)
        assert 'name="box"' in page  # выбор чужого ящика
        assert 'action="/messages"' in page

    def test_viewing_a_foreign_inbox_says_so_and_links_back(self) -> None:
        page = messages_page(
            [], people=self._people(), viewer_id="me", can_edit=True,
            box_owner={"id": "u2", "email": "u2@b.c"},
        )
        assert "u2@b.c" in page.split("<h1>", 1)[1].split("</h1>", 1)[0]  # в заголовке
        assert "← мой ящик" in page
        assert "только чтение" in page

    def test_the_tokens_page_stays_mail_free(self) -> None:
        page = tokens_page([])
        assert 'action="/messages/send"' not in page
        assert "Сообщения" not in page

    def test_the_notify_section_is_shown(self) -> None:
        page = messages_page([], people=self._people(), viewer_id="me",
                             can_edit=True, notify_token="123:ABC")
        assert "Уведомления в Telegram" in page
        assert 'action="/notify/token"' in page
        assert 'action="/notify/chat"' in page
        assert "узнать chat_id" in page

    def test_the_token_is_never_rendered(self) -> None:
        """Токен — секрет: на страницу не попадает даже админу."""
        page = messages_page([], people=self._people(), viewer_id="me",
                             can_edit=True, notify_token="123:SECRET-ABC")
        assert "123:SECRET-ABC" not in page
        assert "сохранён" in page  # но видно, что он сохранён

    def test_a_person_without_a_token_gets_a_hint_not_a_form(self) -> None:
        """Обычному человеку: подсказка, что токен задаст админ."""
        page = messages_page([], people=self._people(), viewer_id="me",
                             can_edit=False, notify_token="")
        assert "настроит администратор" in page
        assert 'action="/notify/token"' not in page  # токен — не его дело

    def test_a_chat_id_is_shown_and_testable(self) -> None:
        page = messages_page([], people=self._people(), viewer_id="me",
                             notify_token="123:ABC", tg_chat="424242")
        assert "424242" in page
        assert "/notify/test" in page  # кнопка проверки цепочки

    def test_the_notify_section_hides_for_a_foreign_inbox(self) -> None:
        page = messages_page([], people=self._people(), viewer_id="me",
                             can_edit=True, notify_token="123:ABC",
                             box_owner={"id": "u2", "email": "u2@b.c"})
        assert "Уведомления в Telegram" not in page  # это чужие настройки


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

    def test_a_blocked_person_is_marked_and_can_be_unblocked(self) -> None:
        page = users_page(
            [{"id": "u1", "email": "x@y.z", "role": "user", "blocked": True}],
            current_id="u0",
        )
        assert "заблокирован" in page
        assert "разблокировать" in page

    def test_an_active_person_can_be_blocked(self) -> None:
        page = users_page(
            [{"id": "u1", "email": "x@y.z", "role": "user"}],
            current_id="u0",
        )
        assert 'action="/users/block"' in page


class TestPickAndTestPages:
    def test_picking_offers_models_with_the_current_one_checked(self) -> None:
        page = pick_model_page("Ollama", "ollama", ["qwen3", "llama3"], current="llama3")
        assert 'value="qwen3"' in page
        assert "checked" in page
        assert 'action="/pick/ollama"' in page

    def test_picking_shows_the_current_facts(self) -> None:
        page = pick_model_page(
            "Ollama", "ollama", ["qwen3"], facts="128K · $0.1/1M · vision"
        )
        assert "Справка о текущей модели" in page
        assert "$0.1/1M" in page

    def test_picking_without_models_points_at_the_key(self) -> None:
        page = pick_model_page("OpenAI", "openai", [])
        assert "проверьте ключ" in page

    def test_a_test_page_shows_the_answer_or_the_error(self) -> None:
        ok = chat_test_page("Ollama", "qwen3", "привет", answer="ок")
        assert "ок" in ok
        bad = chat_test_page("Ollama", "qwen3", "привет", error="нет ключа")
        assert "нет ключа" in bad


class TestAddPage:
    def test_the_form_is_full_and_preserves_values(self) -> None:
        page = add_page(
            values={
                "name": "Мой LMS",
                "url": "http://x/v1",
                "model": "qwen3",
                "logoUrl": "https://a/logo.png\nhttps://b/logo2.png",
            }
        )
        for name in (
            "name", "url", "api_key", "model", "loginUrl", "getApiKeyUrl",
            "infoUrl", "pricing", "context", "capabilities", "logoUrl",
        ):
            assert f'name="{name}"' in page
        assert 'value="Мой LMS"' in page
        assert "показать модели" in page
        assert "https://a/logo.png" in page

    def test_models_become_a_select_and_keep_an_unknown_value(self) -> None:
        page = add_page(values={"model": "custom"}, models=["qwen3", "llama3"])
        assert '<select name="model">' in page
        assert ">qwen3</option>" in page
        assert 'value="custom"' in page  # введённая вручную модель не теряется

    def test_a_keyless_provider_gets_a_help_line(self) -> None:
        page = add_page(models_error="без ключа список недоступен")
        assert "без ключа список недоступен" in page

    def test_the_destination_note_matches_the_role(self) -> None:
        assert "видят только вы" in add_page(is_admin=False)
        assert "видят все" in add_page(is_admin=True)


class TestLogsPage:
    def test_events_render_newest_first(self) -> None:
        # Список приходит отсортированным (новые сверху) — как из журнала.
        page = logs_page(
            [
                {"ts": "2026-10-18T12:01:00Z", "kind": "чат", "user": "u1", "text": "qwen3 через Ollama"},
                {"ts": "2026-10-18T12:00:00Z", "kind": "вход", "user": "a@b.c", "text": "вышел"},
            ]
        )
        assert page.index("qwen3 через Ollama") < page.index("вышел")  # новые сверху
        assert "2026-10-18 12:01:00" in page  # дата и время каждого запроса

    def test_request_and_response_bodies_are_shown(self) -> None:
        page = logs_page(
            [
                {
                    "ts": "2026-10-19T10:00:00Z", "kind": "чат", "user": "u1",
                    "text": "модель через Ollama",
                    "req": '{"messages": [{"content": "привет"}]}',
                    "resp": "и ответ модели",
                }
            ]
        )
        assert "привет" in page
        assert "и ответ модели" in page
        assert "Тело запроса:" in page
        assert "Тело ответа:" in page

    def test_bodies_are_shown_in_full_and_in_a_details_block(self) -> None:
        """Тело — целиком, без обрезки, и сворачивается по клику."""
        long_body = "х" * 2000  # длиннее старого лимита в 300 символов
        page = logs_page(
            [{"ts": "2026-10-19T10:00:00Z", "kind": "чат", "user": "u",
              "text": "длинное тело", "req": long_body}]
        )
        assert long_body in page  # показано целиком
        assert '<details class="payload"' in page  # сворачивается без JS

    def test_bodies_are_collapsed_by_default(self) -> None:
        """По умолчанию свёрнуто: журнал остаётся журналом, не стеной."""
        page = logs_page(
            [{"ts": "2026-10-19T10:00:00Z", "kind": "чат", "user": "u",
              "text": "тело", "req": '{"a":1}', "resp": '{"b":2}'}]
        )
        assert '<details class="payload" open>' not in page
        assert '<details class="payload"><summary>' in page  # но раскрывается по клику
        # содержимое при этом на месте (HTML-экранированное)
        assert "&quot;a&quot;:1" in page

    def test_a_status_badge_is_shown_for_chat_and_decision_rows(self) -> None:
        """Статус рядом с типом: у строки решения видно основание."""
        page = logs_page(
            [
                {"ts": "2026-10-20T22:28:03Z", "kind": "решение", "user": "u1",
                 "text": "Groq: HTTP 200 — подтвердила себя", "status": 200,
                 "provider": "Groq"},
                {"ts": "2026-10-20T22:29:00Z", "kind": "чат", "user": "u1",
                 "text": "модель упала", "status": 402},
            ]
        )
        assert "200" in page
        assert "402" in page
        assert "Groq" in page

    def test_a_row_without_status_has_no_badge(self) -> None:
        page = logs_page(
            [{"ts": "2026-10-20T22:28:03Z", "kind": "вход", "user": "u",
              "text": "вышел"}]
        )
        assert "badge" in page  # бейдж типа на месте
        assert ">200<" not in page  # но пустого статуса нет

    def test_events_without_bodies_stay_clean(self) -> None:
        page = logs_page(
            [{"ts": "2026-10-19T10:00:00Z", "kind": "вход", "user": "u", "text": "вышел"}]
        )
        assert "Тело запроса:" not in page
        assert '<details class="payload">' not in page  # без тел нет и сворачивалок

    def test_the_page_refreshes_itself_but_not_while_a_body_is_open(self) -> None:
        page = logs_page([])
        assert "http-equiv" not in page  # meta-refresh сворачивал бы открытые тела
        assert "location.reload" in page  # автообновление осталось
        assert "details.payload[open]" in page  # пауза при открытом теле

    def test_the_refresh_waits_while_the_reader_is_busy(self) -> None:
        """При свёрнутых телах прыжок каждые 10 секунд мешал бы читать —
        автообновление ждёт, пока человек не трогал страницу."""
        page = logs_page([])
        assert '"mousemove"' in page  # занятость отслеживается
        assert "busy" in page

    def test_no_autoscroll_button_anymore(self) -> None:
        """Новые события сверху — кнопка автоскролла бесполезна, убрана."""
        page = logs_page([])
        assert "автоскролл" not in page
        assert "/logs/autoscroll" not in page

    def test_the_summary_counts_events_and_people(self) -> None:
        """Сводка сверху: сколько каких событий и с кем — за один взгляд."""
        page = logs_page([
            {"ts": "2026-10-25T10:00:00Z", "kind": "письмо", "user": "a@b.c",
             "text": "письмо для c@d.e: «привет»"},
            {"ts": "2026-10-25T10:01:00Z", "kind": "письмо", "user": "a@b.c", "text": "второе"},
            {"ts": "2026-10-25T10:02:00Z", "kind": "баг", "user": "x@y.z", "text": "новый баг"},
        ])
        assert "Показано событий: <b>3</b>" in page
        assert "письмо 2" in page  # вид события со счётчиком
        assert "баг 1" in page
        assert "С кем:" in page
        assert "a@b.c 2" in page  # кто чаще
        assert "x@y.z 1" in page

    def test_the_summary_is_absent_on_an_empty_journal(self) -> None:
        page = logs_page([])
        assert "Показано событий" not in page

    def test_extra_event_fields_show_under_the_text(self) -> None:
        """Путь страницы, адрес провайдера и прочее — подписями в строке."""
        page = logs_page([
            {"ts": "2026-10-25T10:00:00Z", "kind": "страница", "user": "a@b.c",
             "text": "Сообщения", "path": "/messages"},
        ])
        assert "Сообщения" in page
        assert "path: /messages" in page  # сырой путь доступен, но не главное

    def test_service_fields_are_not_repeated_as_meta(self) -> None:
        """req/resp/status — своё место в строке, дублей в подписях не будет."""
        page = logs_page([
            {"ts": "2026-10-25T10:00:00Z", "kind": "чат", "user": "a@b.c",
             "text": "ответ", "status": 200, "provider": "Ollama",
             "req": "{}", "resp": "{}"},
        ])
        meta = page.split('class="meta-line"')
        assert len(meta) == 1 or "status:" not in meta[1]  # служебных нет
        assert "provider: Ollama" not in page  # провайдер — своим местом

    def test_the_bodies_button_is_gone_bodies_are_always_on(self) -> None:
        """Кнопки тел больше нет: тела пишутся и показываются всегда."""
        page = logs_page([])
        assert "bodies-btn" not in page
        assert "/logs/bodies" not in page
        assert "preventDefault" not in page  # и скрипта переключателя тоже нет
        assert "тела запросов: вкл" not in page
        assert "тела запросов: выкл" not in page
        assert "чужая переписка" not in page  # предупреждений больше нет

    def test_foreign_words_cannot_break_the_table(self) -> None:
        page = logs_page(
            [{"ts": "2026-10-18T12:00:00Z", "kind": "x", "user": "<img src=x>", "text": "<b>жирный</b>"}]
        )
        assert "<img" not in page
        assert "&lt;img" in page

    def test_an_empty_journal_says_so(self) -> None:
        assert "событий пока нет" in logs_page([])


class TestProviderVitrine:
    def test_the_first_logo_becomes_an_image(self) -> None:
        page = catalogue_page(
            [_entry(logoUrl="https://cdn.example/logo.png\nhttps://x/2.png")]
        )
        assert '<img class="logo" src="https://cdn.example/logo.png"' in page

    def test_a_dangerous_logo_scheme_is_not_an_image(self) -> None:
        page = catalogue_page([_entry(logoUrl="javascript:alert(1)")])
        assert "<img" not in page

    def test_info_url_is_a_link(self) -> None:
        page = catalogue_page([_entry(infoUrl="https://o.com/pricing")])
        assert "описание и цены" in page
        assert 'href="https://o.com/pricing"' in page

    def test_edit_form_carries_neither_logo_nor_info(self) -> None:
        """Логотипы и «описание и цены» — не поля правки; данные не тронуты."""
        page = edit_page(_entry(logoUrl="https://a/l.png", infoUrl="https://o.com/p"))
        assert 'name="logoUrl"' not in page
        assert 'name="infoUrl"' not in page


class TestNavigation:
    """Шапка одна на все страницы: логотип-ссылка домой, «выйти» far right."""

    @staticmethod
    def _pages() -> dict[str, str]:
        return {
            "каталог": catalogue_page([_entry()], can_edit=True),
            "правка": edit_page(_entry()),
            "добавить": add_page(is_admin=True),
            "ключ": key_page("Ollama", provider_id="ollama", has_key=False),
            "модель": pick_model_page("Ollama", "ollama", ["qwen3"]),
            "тест": chat_test_page("Ollama", "ollama", "привет", answer="ок"),
            "токены": tokens_page([]),
            "новый токен": token_created_page("uib-x", "phone"),
            "статистика": stats_page([]),
            "журнал": logs_page([]),
            "пользователи": users_page([]),
            "баги": bugs_page([]),
            "почта": messages_page([], people=[]),
        }

    def test_every_page_links_home_via_the_logo(self) -> None:
        for name, page in self._pages().items():
            assert 'class="brand" href="/"' in page, name

    def test_every_page_keeps_the_sections_and_logout(self) -> None:
        for name, page in self._pages().items():
            assert 'href="/stats"' in page, name
            assert 'href="/tokens"' in page, name
            assert 'action="/logout"' in page, name

    def test_logout_is_last_and_visually_set_apart(self) -> None:
        """«Выйти» — последним и приглушённым: кликнуть по ошибке трудно."""
        page = self._pages()["каталог"]
        assert page.rindex('action="/logout"') > page.rindex('href="/stats"')
        assert 'class="out"' in page

    def test_admin_sections_only_for_the_admin(self) -> None:
        assert 'href="/logs"' in catalogue_page([_entry()], can_edit=True)
        assert 'href="/logs"' not in catalogue_page([_entry()], can_edit=False)
        assert 'href="/users"' not in catalogue_page([_entry()], can_edit=False)

    def test_login_and_signup_have_the_brand_but_not_the_nav(self) -> None:
        for page in (login_page(), signup_page()):
            assert 'class="brand"' in page  # лицо приставки на месте
            assert 'href="/stats"' not in page  # вход ещё не состоялся
            assert 'action="/logout"' not in page

    def test_token_created_page_hides_admin_sections_from_a_user(self) -> None:
        assert 'href="/logs"' not in token_created_page("uib-x", "phone")
        assert 'href="/logs"' in token_created_page("uib-x", "phone", can_edit=True)


class TestNavLook:
    """Вид шапки: разделы — пилюли, свои цвета живут в браузере.

    Шапка одна на все страницы, поэтому проверяется на каталоге.
    """
    @staticmethod
    def _page(**kwargs) -> str:
        return catalogue_page([_entry()], can_edit=True, **kwargs)

    def test_sections_are_pills_with_a_visible_border(self) -> None:
        """Разделы (и «выйти») — овальные пилюли, а не голые ссылки."""
        page = self._page()
        assert (
            "border: 1px solid color-mix(in srgb, var(--head) 40%, transparent); "
            "border-radius: 999px"
        ) in page
        assert ".topnav nav a:hover" in page
        assert ".topnav button.out" in page

    def test_two_colors_are_independent_but_default_to_one(self) -> None:
        """Кнопки и полоса шапки — две разные переменные; по умолчанию равны."""
        page = self._page()
        assert "--nav-a: var(--accent)" in page  # полоса = акцент, пока не задана своя
        assert "linear-gradient(135deg, var(--nav-a), var(--nav-b))" in page
        assert "html.custom-accent" in page
        assert "html.custom-nav" in page

    def test_the_header_shadow_follows_the_picked_color(self) -> None:
        """Тень полосы считается от её цвета — не остаётся зелёной при синем."""
        page = self._page()
        assert "box-shadow: 0 2px 6px color-mix(in srgb, var(--nav-a)" in page

    def test_four_independent_colors_with_four_inputs(self) -> None:
        """Четыре цвета: полоса верх/низ и кнопки верх/низ — у каждого своя пипетка."""
        page = self._page()
        for node in ("color-nav", "color-soft", "color-head", "color-accent", "color-reset"):
            assert f'id="{node}"' in page, node
        assert 'type="color"' in page
        for label in ("полоса верх", "полоса низ", "кнопки верх", "кнопки низ"):
            assert label in page, label

    def test_the_soft_background_is_its_own_variable(self) -> None:
        """Нежный фон (подсветки, уведомления) — отдельная переменная --soft."""
        page = self._page()
        assert "--soft: var(--accent-soft)" in page  # следует за кнопками, пока своя не задана
        assert "background: var(--soft)" in page  # подложки читают именно её
        assert "background: var(--accent-soft)" not in page  # старых прямых чтений больше нет

    def test_header_buttons_follow_their_own_variable(self) -> None:
        """Кнопки шапки (выйти, тема, пипетка) — свой цвет --head."""
        page = self._page()
        assert "--head: #ffffff" in page
        assert "color: color-mix(in srgb, var(--head) 85%, transparent)" in page
        assert "border: 1px solid color-mix(in srgb, var(--head) 40%, transparent)" in page

    def test_the_panel_offers_two_color_inputs_and_a_reset(self) -> None:
        page = self._page()
        assert 'id="color-accent"' in page
        assert 'id="color-nav"' in page
        assert 'id="color-reset"' in page
        assert 'type="color"' in page

    def test_saved_colors_apply_before_the_header_is_drawn(self) -> None:
        """Свои цвета применяются ранним скриптом — без мигания белым."""
        page = self._page()
        for key in ("ai-bridge-accent", "ai-bridge-nav", "ai-bridge-soft", "ai-bridge-head"):
            assert page.index(f'localStorage.getItem("{key}")') < page.index(
                '<header class="topnav">'
            ), key

    def test_choosing_a_preset_clears_custom_colors(self) -> None:
        """Пресет темы сбрасывает свои цвета — иначе непонятно, что действует."""
        page = self._page()
        assert (
            'var AK="ai-bridge-accent", NK="ai-bridge-nav", '
            'SK="ai-bridge-soft", HK="ai-bridge-head"'
        ) in page
        assert (
            'function clearAll(){ setAccent(""); setNav(""); setSoft(""); setHead(""); }'
            in page
        )

    def test_remove_is_a_trash_icon_with_an_explanation(self) -> None:
        """«Убрать» — корзина (место для телефона), смысл — в подсказке."""
        page = self._page()
        assert 'action="/remove/ollama"' in page
        assert 'aria-label="убрать"' in page
        assert ">убрать</button>" not in page  # слова в кнопке больше нет

    def test_test_and_remove_carry_the_pill_class(self) -> None:
        """Обе кнопки строки каталога получают рамку-пилюлю."""
        page = self._page()
        assert 'class="ghost pill"' in page  # тест
        assert 'class="ghost danger pill"' in page  # убрать
        assert "button.ghost.pill" in page  # и правило в CSS


class TestAutotestAnswers:
    """Результаты тестов: форматированный, raw и реакция роутера."""

    @staticmethod
    def _page(response: str, *, resp: str = '{"choices": []}', reaction: str = "сработала бы: белый список") -> str:
        return autotest_page(
            [
                {"id": "ollama", "name": "Ollama", "enabled": True},
                {"id": "off", "name": "Off", "enabled": False},
            ],
            last_run={
                "provider": "ollama",
                "timestamp": "2026-10-23T02:00:00Z",
                "results": [
                    {
                        "ts": "2026-10-23T02:00:01Z",
                        "model": "m-1",
                        "test": "text",
                        "status": "ok",
                        "http_status": 200,
                        "duration": 1.0,
                        "response": response,
                        "reaction": reaction,
                        "req": "{}",
                        "resp": resp,
                    }
                ],
            },
            can_edit=True,
        )

    def test_two_columns_and_no_bodies_column(self) -> None:
        page = self._page("ответ")
        assert "<th>форматированный</th>" in page
        assert "<th>raw</th>" in page
        assert "<th>реакция роутера</th>" in page
        assert "<th>тела</th>" not in page

    def test_formatted_and_raw_show_the_whole_body(self) -> None:
        page = self._page("ответ", resp='{"a": 1, "b": 2}')
        assert 'class="answer-json"' in page
        assert 'class="answer-raw"' in page
        # Форматированный: JSON с отступами; raw: как пришло.
        assert "&quot;a&quot;: 1" in page
        assert "&quot;a&quot;: 1,\n" in page  # pretty-print с отступами

    def test_markdown_body_is_rendered(self) -> None:
        # Тело не JSON → показывается как markdown.
        page = self._page("ответ", resp="# Заголовок\n\n**жирный** текст")
        assert 'class="answer-md"' in page
        assert "<h1>Заголовок</h1>" in page
        assert "<strong>жирный</strong>" in page

    def test_copy_buttons_are_present(self) -> None:
        page = self._page("ответ")
        assert 'class="copy-all"' in page
        assert 'class="copy-code"' in self._page("ответ", resp="```\nкод\n```")

    def test_text_wraps_in_both_columns(self) -> None:
        page = self._page("длинный текст", resp="очень длинное тело")
        assert "white-space: pre-wrap" in page
        assert "overflow-wrap: anywhere" in page

    def test_reaction_is_shown(self) -> None:
        page = self._page("ответ", reaction="сработала бы: серый список на сутки")
        assert "сработала бы: серый список на сутки" in page

    def test_all_providers_are_listed_disabled_marked(self) -> None:
        page = self._page("ответ")
        assert 'value="ollama"' in page
        assert 'value="off"' in page
        assert "Off · выключен" in page

    def test_form_has_one_test_dropdown_and_required_prompt(self) -> None:
        page = self._page("ответ")
        assert '<select name="test">' in page  # один тип теста — выпадающий список
        assert 'name="test_vision"' not in page  # чекбоксов больше нет
        assert 'name="prompt" required' in page  # prompt обязателен

    def test_the_used_prompt_is_shown(self) -> None:
        page = autotest_page(
            [{"id": "ollama", "name": "Ollama", "enabled": True}],
            last_run={
                "provider": "ollama",
                "timestamp": "2026-10-23T02:00:00Z",
                "prompt": "Мой собственный промпт",
                "results": [{
                    "ts": "2026-10-23T02:00:01Z", "model": "m", "test": "text",
                    "status": "ok", "http_status": 200, "duration": 0.1,
                    "response": "ок", "reaction": "сработала бы: белый список",
                    "resp": '{"choices": []}',
                }],
            },
            can_edit=True,
        )
        assert "Мой собственный промпт" in page


class TestGreyTimer:
    """Серый список показывает таймер: провайдер спит, видно сколько осталось."""

    def test_a_sleeping_provider_shows_the_remaining_time(self) -> None:
        page = stats_page(
            [], can_edit=True,
            grey_rows=[{"id": "ollama", "minutes": 125, "reason": "лимит", "cycle": 2}],
        )
        assert "Серый список — таймер пробуждения" in page
        assert "ollama" in page
        assert "2 ч 5 мин" in page  # 125 минут
        assert "лимит" in page
        assert "2" in page  # цикл виден

    def test_an_awake_provider_is_marked_as_awake(self) -> None:
        page = stats_page(
            [], can_edit=True,
            grey_rows=[{"id": "groq", "minutes": 0, "reason": "—", "cycle": 1}],
        )
        assert "уже проснулся" in page

    def test_no_timer_table_when_nothing_sleeps(self) -> None:
        page = stats_page([], can_edit=True, grey_rows=[])
        assert "таймер пробуждения" not in page

    def test_a_user_never_sees_the_lists_section(self) -> None:
        """Обычному человеку списки и таймер не показываются вовсе."""
        page = stats_page(
            [], can_edit=False,
            grey_rows=[{"id": "ollama", "minutes": 30, "reason": "x", "cycle": 1}],
        )
        assert "таймер пробуждения" not in page


class TestAutotestMediaForm:
    """Форма автотеста: типы генерации и поля под медиа."""

    @staticmethod
    def _page() -> str:
        return autotest_page([_entry()])

    def test_every_test_type_is_selectable(self) -> None:
        page = self._page()
        for value in ("text", "vision", "tools", "free",
                      "image", "tts", "stt", "video", "3d", "embedding"):
            assert f'<option value="{value}">' in page, value

    def test_media_fields_are_present(self) -> None:
        page = self._page()
        assert 'name="only_model"' in page  # своя модель вместо всего каталога
        assert 'name="voice"' in page  # голос для озвучки
        assert 'name="audio"' in page  # файл для распознавания
        assert 'accept="audio/*"' in page


class TestAutotestMediaResults:
    """Медиа в результатах: картинка — картинкой, звук — плеером."""

    @staticmethod
    def _html(test: str, response: str) -> str:
        return autotest_results_html([
            {
                "model": "m-1", "test": test, "ts": "2026-10-26T10:00:00Z",
                "status": "ok", "http_status": 200, "duration": 1.2,
                "response": response, "req": "{}", "resp": response,
                "reaction": "", "used_tool": None,
            },
        ])

    def test_a_generated_picture_is_embedded(self) -> None:
        html = self._html("image", "https://media.test/pic.png")
        assert "<img" in html
        assert "https://media.test/pic.png" in html

    def test_a_speech_result_becomes_a_player(self) -> None:
        html = self._html("tts", "https://media.test/voice.mp3")
        assert "<audio" in html
        assert "controls" in html

    def test_a_video_result_gets_a_player(self) -> None:
        html = self._html("video", "https://media.test/clip.mp4")
        assert "<video" in html
        assert "controls" in html

    def test_plain_results_stay_plain(self) -> None:
        html = self._html("stt", "распознано: привет")
        assert "<img" not in html
        assert "<audio" not in html

    def test_a_foreign_scheme_never_becomes_markup(self) -> None:
        """Ответ провайдера — чужие данные: javascript: не становится ссылкой."""
        html = self._html("image", "javascript:alert(1)")
        assert "<img" not in html


class TestTestPageExtras:
    """Страница «Тест»: свой промпт и секции генерации по типам моделей."""

    @staticmethod
    def _page(**kwargs) -> str:
        media = kwargs.pop("media", None) or {
            "image": [{"name": "flux.1-schnell", "paid": False}],
            "audio": [{"name": "openai/tts-1", "paid": False}],
            "three": [{"name": "microsoft/trellis-2", "paid": False}],
            "embedding": [{"name": "openai/text-embedding-3-small", "paid": False}],
            "stt": [{"name": "openai/whisper-large-v3", "paid": False}],
        }
        return chat_test_page("Pollinations.ai", "flux.1-schnell", "привет",
                              answer="Да", provider_id="pollinations",
                              media=media, **kwargs)

    def test_your_own_prompt_form_is_always_here(self) -> None:
        page = self._page()
        assert 'action="/test/pollinations"' in page
        assert 'name="question"' in page

    def test_image_section_appears_only_with_models(self) -> None:
        page = self._page(media={"image": [
            {"name": "flux.1-schnell", "paid": False},
            {"name": "flux-2-pro", "paid": True},
        ]})
        assert 'action="/imgtest/pollinations"' in page
        assert 'name="prompt"' in page
        assert 'value="flux.1-schnell"' in page
        assert "платная" in page  # дорогая модель помечена

        plain = chat_test_page("Ollama", "qwen3", "привет", answer="ок",
                               provider_id="ollama")
        assert "/imgtest/" not in plain  # у провайдера без меты секции нет

    def test_the_result_box_waits_for_a_picture(self) -> None:
        page = self._page()
        assert "genbox" in page
        assert "<img" not in page

    def test_a_generated_picture_is_shown_in_the_box(self) -> None:
        page = self._page(
            image_result={"model": "flux.1-schnell", "prompt": "кот",
                          "url": "https://media.test/pic.png", "b64": "", "error": ""},
        )
        assert "<img" in page
        assert "https://media.test/pic.png" in page
        assert "кот" in page  # подпись: что и какой моделью

    def test_base64_becomes_a_data_uri(self) -> None:
        page = self._page(
            image_result={"model": "flux.1-schnell", "prompt": "кот",
                          "url": "", "b64": "QUJD", "error": ""},
        )
        assert "data:image/png;base64,QUJD" in page

    def test_an_image_error_is_a_notice(self) -> None:
        page = self._page(
            image_result={"model": "", "prompt": "", "url": "", "b64": "",
                          "error": "HTTP 400: нет кредитов"},
        )
        assert "HTTP 400: нет кредитов" in page
        assert "notice bad" in page

    def test_a_foreign_scheme_never_becomes_an_image(self) -> None:
        page = self._page(
            image_result={"model": "m", "prompt": "x",
                          "url": "javascript:alert(1)", "b64": "", "error": ""},
        )
        assert "<img" not in page

    def test_the_answer_block_hides_without_a_question(self) -> None:
        """После генерации страница не показывает чужой чат-вопрос."""
        page = chat_test_page("Pollinations.ai", "", "", provider_id="pollinations")
        assert "крошечный запрос" not in page

    # ------------------------------------------------------- новые секции

    def test_audio_section_offers_models_voice_and_text(self) -> None:
        page = self._page(media={"audio": [
            {"name": "openai/tts-1", "paid": False},
            {"name": "elevenlabs/eleven-v4", "paid": True},
        ]})
        assert 'action="/audiotest/pollinations"' in page
        assert 'name="voice"' in page
        assert 'name="text"' in page
        assert "elevenlabs/eleven-v4 · платная" in page

    def test_audio_result_becomes_a_player(self) -> None:
        page = self._page(
            audio_result={"model": "openai/tts-1", "text": "привет",
                          "url": "https://media.test/voice.mp3", "error": ""},
        )
        assert "<audio" in page
        assert "https://media.test/voice.mp3" in page

    def test_three_d_section_and_its_download_link(self) -> None:
        page = self._page(media={"three": [{"name": "microsoft/trellis-2",
                                            "paid": False}]})
        assert 'action="/3dtest/pollinations"' in page
        assert 'name="image"' in page  # картинка-источник обязана быть ссылкой
        assert 'name="resolution"' in page

        ready = self._page(
            three_result={"model": "microsoft/trellis-2",
                          "url": "https://media.test/model.glb", "error": ""},
        )
        assert "https://media.test/model.glb" in ready
        assert "скачать" in ready

    def test_embedding_section_and_its_vector(self) -> None:
        page = self._page(media={"embedding": [{"name": "openai/text-embedding-3-small",
                                                "paid": False}]})
        assert 'action="/embedtest/pollinations"' in page
        assert 'name="text"' in page

        ready = self._page(
            embed_result={"model": "openai/text-embedding-3-small", "text": "привет",
                          "count": 3, "sample": "0.25, -0.5, 0.125", "error": ""},
        )
        assert "вектор из 3 чисел" in ready
        assert "0.25" in ready

    def test_no_models_no_sections(self) -> None:
        """Без метаданных — ни секции, ни лишних полей."""
        page = chat_test_page("Ollama", "qwen3", "привет", answer="ок",
                              provider_id="ollama",
                              media={"image": [], "audio": [], "three": [],
                                     "embedding": [], "stt": []})
        for marker in ("/imgtest/", "/audiotest/", "/3dtest/", "/embedtest/",
                       "/stttest/"):
            assert marker not in page

    def test_transcriber_section_takes_an_audio_file(self) -> None:
        page = self._page(media={"stt": [
            {"name": "openai/whisper-large-v3", "paid": False},
        ]})
        assert 'action="/stttest/pollinations"' in page
        assert 'name="audio"' in page
        assert 'accept="audio/*"' in page
        assert "тестовый тон" in page  # пустое поле — тон подставится сам

    def test_a_transcript_is_shown_as_plain_text(self) -> None:
        page = self._page(
            transcript_result={"model": "openai/whisper-large-v3",
                               "text": "распознано: привет от приставки",
                               "error": ""},
        )
        assert "распознано: привет от приставки" in page
        assert "openai/whisper-large-v3" in page

    # --------------------------------------------------- музыка и обработка

    def test_music_section_sings_with_its_own_models(self) -> None:
        page = self._page(media={"music": [{"name": "google/lyria-3.5",
                                            "paid": True}]})
        assert "<h2>Музыка</h2>" in page
        assert 'action="/audiotest/pollinations"' in page  # тот же маршрут озвучки
        assert "google/lyria-3.5 · платная" in page
        # секции без моделей не появляются
        plain = self._page(media={"music": []})
        assert "<h2>Музыка</h2>" not in plain

    def test_music_result_is_a_player_too(self) -> None:
        page = self._page(
            media={"music": [{"name": "google/lyria-3.5", "paid": True}]},
            music_result={"model": "google/lyria-3.5", "text": "песня про кота",
                          "url": "https://media.test/song.mp3", "error": ""},
        )
        assert "<audio" in page
        assert "https://media.test/song.mp3" in page

    def test_voice_section_labels_each_action(self) -> None:
        page = self._page(media={"voice": [
            {"name": "elevenlabs/voice-isolator", "paid": True, "action": "isolator"},
        ]})
        assert "<h2>Обработка звука</h2>" in page
        assert 'action="/voicetest/pollinations"' in page
        assert 'value="isolator|elevenlabs/voice-isolator"' in page
        assert "очистка от шума" in page  # действие подписано по-русски

    def test_voice_result_becomes_a_player(self) -> None:
        page = self._page(
            media={"voice": [{"name": "elevenlabs/voice-isolator", "paid": True,
                              "action": "isolator"}]},
            voice_result={"model": "elevenlabs/voice-isolator", "action": "isolator",
                          "url": "https://media.test/clean.mp3", "b64": "",
                          "error": ""},
        )
        assert "<audio" in page
        assert "https://media.test/clean.mp3" in page

    def test_voices_are_offered_as_suggestions(self) -> None:
        """Голоса каталога — подсказки в поле голоса (свободный ввод остаётся)."""
        page = self._page(voices=["nova", "alloy"])
        assert 'list="voices"' in page
        assert '<datalist id="voices">' in page
        assert 'value="nova"' in page
        assert 'value="alloy"' in page
