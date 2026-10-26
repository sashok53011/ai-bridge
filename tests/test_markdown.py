"""Markdown → HTML: то, что реально присылают модели."""

from core.markdown import render


class TestRender:
    def test_plain_text_becomes_a_paragraph(self) -> None:
        assert render("привет") == "<p>привет</p>"

    def test_headings(self) -> None:
        assert "<h2>Заголовок</h2>" in render("## Заголовок")

    def test_bold_and_italic(self) -> None:
        page = render("**жирный** и *курсив*")
        assert "<strong>жирный</strong>" in page
        assert "<em>курсив</em>" in page

    def test_inline_code(self) -> None:
        assert "<code>код</code>" in render("вот `код` тут")

    def test_fenced_code_block_has_copy_button(self) -> None:
        page = render("```python\nprint(1)\n```")
        assert '<div class="codeblock">' in page
        assert 'class="copy-code"' in page
        assert "print(1)" in page

    def test_a_bullet_list(self) -> None:
        page = render("- один\n- два")
        assert "<ul>" in page and page.count("<li>") == 2

    def test_a_numbered_list(self) -> None:
        page = render("1. раз\n2. два")
        assert "<ol>" in page and page.count("<li>") == 2

    def test_an_image_becomes_a_picture(self) -> None:
        """Ответы медиа-моделей: ![alt](url) — настоящая картинка."""
        page = render("![кот в космосе](https://x/pic.png)")
        assert '<img src="https://x/pic.png"' in page
        assert 'alt="кот в космосе"' in page
        assert 'loading="lazy"' in page

    def test_a_javascript_image_stays_text(self) -> None:
        page = render("![x](javascript:alert(1))")
        assert "<img" not in page

    def test_an_image_does_not_swallow_the_link_syntax(self) -> None:
        """После картинки обычные ссылки остаются ссылками."""
        page = render("![кот](https://x/p.png) и [ссылка](https://y/z)")
        assert '<img src="https://x/p.png"' in page
        assert '<a href="https://y/z"' in page

    def test_a_link_is_safe(self) -> None:
        page = render("[сайт](https://example.com/x)")
        assert 'href="https://example.com/x"' in page
        assert 'rel="noopener"' in page

    def test_a_javascript_link_is_not_a_link(self) -> None:
        page = render("[зло](javascript:alert(1))")
        assert "href=" not in page
        assert "<a " not in page

    def test_html_is_escaped(self) -> None:
        page = render("<script>alert(1)</script>")
        assert "&lt;script&gt;" in page
        assert "<script>" not in page

    def test_line_breaks_are_kept_inside_a_paragraph(self) -> None:
        assert "<br>" in render("строка один\nстрока два")

    def test_an_unclosed_code_block_does_not_get_lost(self) -> None:
        page = render("```\nне закрыт")
        assert "не закрыт" in page