"""Страница приставки: разметка из данных.

Главное свойство — чужие слова не становятся разметкой: имя провайдера
с прямыми скобками или адрес с кавычкой не должны ломать страницу.
"""

from core.page import catalogue_page, key_page


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

    def test_the_add_form_asks_for_name_url_and_model(self) -> None:
        page = catalogue_page([_entry()])
        assert 'action="/add"' in page
        assert 'name="name"' in page
        assert 'name="url"' in page
        assert 'name="model"' in page
        assert 'name="api_key"' in page

    def test_every_row_can_be_toggled_and_checked(self) -> None:
        page = catalogue_page([_entry()])
        assert 'action="/toggle/ollama"' in page
        assert 'action="/check/ollama"' in page

    def test_an_admin_sees_remove_and_a_user_does_not(self) -> None:
        assert 'action="/remove/ollama"' in catalogue_page([_entry()], can_edit=True)
        assert 'action="/remove/ollama"' not in catalogue_page([_entry()], can_edit=False)

    def test_a_user_sees_remove_for_own_proposal(self) -> None:
        page = catalogue_page([_entry(origin="user", mine=True)])
        assert 'action="/remove/ollama"' in page

    def test_the_key_column_says_only_what_is_true(self) -> None:
        assert ">задан<" in catalogue_page([_entry()], keyed_ids=frozenset({"ollama"}))
        assert ">задан<" not in catalogue_page([_entry()], keyed_ids=frozenset())

    def test_a_notice_is_escaped_text(self) -> None:
        page = catalogue_page([_entry()], notice='<img src=x onerror=evil()>')
        assert "<img src=x" not in page
        assert "&lt;img" in page

    def test_an_empty_catalogue_says_so(self) -> None:
        assert "каталог пуст" in catalogue_page([])


class TestKeyPage:
    def test_the_page_asks_for_the_key(self) -> None:
        page = key_page("OpenAI", has_key=False)
        assert 'name="api_key"' in page
        assert "OpenAI" in page

    def test_a_set_key_is_admitted_without_being_shown(self) -> None:
        page = key_page("OpenAI", has_key=True)
        assert "задан" in page
        assert "sk-" not in page  # сам ключ на страницу не попадает никогда

    def test_the_provider_name_is_escaped(self) -> None:
        page = key_page('<b>жирный</b>', has_key=False)
        assert "<b>жирный</b>" not in page
