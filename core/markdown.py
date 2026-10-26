"""Крошечный markdown → HTML для страниц приставки.

Поддерживает то, что реально присылают модели: заголовки, списки,
жирный/курсив, встроенный код, блоки кода ```lang```, ссылки http/https
и картинки ``![alt](url)`` (ответы медиа-моделей).
Всё остальное остаётся текстом. HTML экранируется — чужой ответ не может
вставить тег или обработчик.

Чистая функция «текст → HTML»: ни сети, ни файлов, ни времени.
"""

from __future__ import annotations

import html
import re

_FENCE = re.compile(r"^```([\w+-]*)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
#: Картинки медиа-моделей: ![alt](url) — только http/https, как ссылки.
_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def render(text: object) -> str:
    """Markdown → безопасный HTML."""
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    out: list[str] = []
    para: list[str] = []
    code: list[str] = []
    lang = ""
    in_code = False
    list_tag = ""  # "ul" / "ol" / ""

    def flush_para() -> None:
        nonlocal para
        if para:
            out.append("<p>" + _inline("\n".join(para)) + "</p>")
            para = []

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = ""

    for line in lines:
        fence = _FENCE.match(line.strip())
        if fence:
            if in_code:
                out.append(_code_block("\n".join(code), lang))
                code, lang, in_code = [], "", False
            else:
                flush_para()
                close_list()
                in_code, lang = True, fence.group(1)
            continue
        if in_code:
            code.append(line)
            continue
        if not line.strip():
            flush_para()
            close_list()
            continue
        heading = _HEADING.match(line)
        if heading:
            flush_para()
            close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue
        bullet = _BULLET.match(line)
        numbered = _NUMBERED.match(line)
        if bullet or numbered:
            flush_para()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append("<li>" + _inline((bullet or numbered).group(1)) + "</li>")
            continue
        para.append(line)

    if in_code:  # блок не закрыт — показываем как есть, не теряем
        out.append(_code_block("\n".join(code), lang))
    flush_para()
    close_list()
    return "\n".join(out)


def _inline(text: str) -> str:
    """Строка без абзацев: экранирование, код, выделение, картинки, ссылки."""
    value = html.escape(text)
    value = _CODE_SPAN.sub(r"<code>\1</code>", value)
    value = _BOLD.sub(r"<strong>\1</strong>", value)
    value = _ITALIC.sub(r"<em>\1</em>", value)
    # Картинки — до ссылок: иначе _LINK разберёт внутренние [alt](url).
    value = _IMAGE.sub(r'<img src="\2" alt="\1" loading="lazy">', value)
    value = _LINK.sub(
        r'<a href="\2" target="_blank" rel="noopener">\1</a>', value
    )
    return value.replace("\n", "<br>")


def _code_block(code: str, lang: str) -> str:
    """Блок кода с кнопкой «копировать» (её оживляет скрипт страницы)."""
    language = html.escape(lang or "")
    return (
        '<div class="codeblock">'
        '<button type="button" class="copy-code">копировать</button>'
        f'<pre><code class="lang-{language}">{html.escape(code)}</code></pre>'
        "</div>"
    )