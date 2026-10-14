"""Страница приставки: чистые функции «данные → HTML».

Здесь нет ни сети, ни файлов, ни часов — только разметка из данных.
Поэтому она целиком покрывается тестами: экранирование чужих слов,
наличие форм там, где должны быть кнопки.

Форматы без JavaScript: формы шлют обычные POST и получают redirect.
Страница работает даже если JS отключён, а чужие слова (имена,
адреса, ошибки) экранируются — витрина не должна быть дырой.
"""

from __future__ import annotations

from html import escape

from core.catalogue import can_remove
from core.stats import format_stats, for_line


def _badge(text: str, kind: str = "") -> str:
    cls = f"badge badge-{kind}" if kind else "badge"
    return f'<span class="{cls}">{escape(text)}</span>'


def _provider_row(
    entry: dict,
    *,
    stats: object,
    can_edit: bool,
    key_hint: str,
) -> str:
    """Одна строка каталога: имя, адрес, модель, статистика и действие."""
    name = escape(entry["name"])
    badges = []
    if entry.get("enabled"):
        badges.append(_badge("вкл", "on"))
    if entry.get("status") == "dead":
        badges.append(_badge("нераб.", "dead"))
    if entry.get("status") == "disabled_for_all":
        badges.append(_badge("выкл. админом", "off"))
    if entry.get("mine"):
        badges.append(_badge("ваш", "mine"))

    counter = for_line(stats or {}, entry["id"], entry.get("model", ""))
    stats_text = format_stats(counter) or "—"

    # Действие над строкой: тумблер всегда, ключ и удаление — по смыслу.
    toggle_label = "выключить" if entry.get("enabled") else "включить"
    actions = [
        f'<form method="post" action="/toggle/{escape(entry["id"], quote=True)}">'
        f'<button type="submit">{toggle_label}</button></form>',
        f'<form method="post" action="/check/{escape(entry["id"], quote=True)}">'
        f'<button type="submit" class="ghost">проверить</button></form>',
    ]
    if can_remove(entry, is_superuser=can_edit):
        actions.append(
            f'<form method="post" action="/remove/{escape(entry["id"], quote=True)}">'
            f'<button type="submit" class="ghost danger">убрать</button></form>'
        )

    return (
        f'<tr class="{"on" if entry.get("enabled") else ""}">'
        f'<td class="name">{name} {" ".join(badges)}</td>'
        f'<td class="url">{escape(entry["url"])}</td>'
        f'<td class="model">{escape(entry.get("model") or "—")}</td>'
        f'<td class="stats">{escape(stats_text)}</td>'
        f'<td class="key">{escape(key_hint)}</td>'
        f'<td class="actions">{" ".join(actions)}</td>'
        f"</tr>"
    )


def catalogue_page(
    providers: list[dict],
    *,
    stats: object = None,
    can_edit: bool = False,
    keyed_ids: frozenset[str] = frozenset(),
    notice: str = "",
    role: str = "",
) -> str:
    """Весь каталог одной страницей: тумблеры, проверка, ключи, добавление."""
    rows = "\n".join(
        _provider_row(
            entry,
            stats=stats,
            can_edit=can_edit,
            key_hint="задан" if entry["id"] in keyed_ids else "",
        )
        for entry in providers
    )
    if not rows:
        rows = '<tr><td colspan="6" class="empty">каталог пуст</td></tr>'

    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    role_html = f'<p class="role">{escape(role)}</p>' if role else ""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-приставка</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; }}
  h1 {{ font-size: 1.4rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8884; }}
  th {{ font-size: .85rem; text-transform: lowercase; opacity: .7; }}
  tr.on td {{ background: #2a80..; }}
  tr.on td {{ background: rgba(42, 128, 60, .10); }}
  .badge {{ font-size: .75rem; padding: .1rem .4rem; border-radius: .3rem; margin-left: .3rem; }}
  .badge-on {{ background: #2a803c33; }}
  .badge-dead {{ background: #a0303033; }}
  .badge-off {{ background: #806a2a33; }}
  .badge-mine {{ background: #2a5a8033; }}
  form {{ display: inline; }}
  button {{ padding: .25rem .7rem; border: 1px solid #8886; border-radius: .4rem; background: transparent; cursor: pointer; }}
  button.ghost {{ opacity: .8; }}
  button.danger {{ color: #c0392b; }}
  .actions {{ white-space: nowrap; }}
  .empty {{ opacity: .6; }}
  .notice {{ background: rgba(42, 128, 60, .15); padding: .5rem .8rem; border-radius: .4rem; }}
  .role {{ opacity: .6; font-size: .85rem; }}
  .add {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #8884; }}
  .add input {{ margin: .2rem; padding: .3rem; }}
  details {{ margin-top: 1rem; }}
  input[type=password] {{ font-family: monospace; }}
</style>
</head>
<body>
<h1>AI-приставка</h1>
{role_html}
{notice_html}
<table>
<tr><th>провайдер</th><th>адрес</th><th>модель</th><th>проверки</th><th>ключ</th><th></th></tr>
{rows}
</table>

<details>
<summary>Добавить провайдера</summary>
<form method="post" action="/add">
  <input name="name" placeholder="Имя" required>
  <input name="url" type="url" placeholder="https://…/v1" required>
  <input name="model" placeholder="модель" required>
  <input name="api_key" type="password" placeholder="ключ (останется на устройстве)" autocomplete="off">
  <button type="submit">добавить</button>
</form>
<p class="role">обычному пользователю — предложение (видят только вы и администратор),
администратору — запись в каталог для всех.</p>
</details>

<p class="role">Ключи хранятся только на этом устройстве и уходят напрямую провайдеру —
на сервер каталога они не попадают никогда.</p>
</body>
</html>"""


def login_page(notice: str = "") -> str:
    """Вход в приставку: форма с токеном.

    Показывается, когда страница открыта на не-loopback адресе и браузер
    ещё не ввёл токен. Токен один на приставку — тот же, что для /v1/*.
    """
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вход — AI-приставка</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 30rem; padding: 0 1rem; }}
  input {{ width: 100%; padding: .4rem; font-family: monospace; }}
  button {{ margin-top: .6rem; padding: .3rem .9rem; }}
  .role {{ opacity: .6; font-size: .85rem; }}
  .notice {{ background: rgba(160, 48, 48, .15); padding: .5rem .8rem; border-radius: .4rem; }}
</style>
</head>
<body>
<h1>AI-приставка</h1>
{notice_html}
<form method="post" action="/login">
  <input name="token" type="password" placeholder="токен приставки" autocomplete="current-password" autofocus>
  <button type="submit">войти</button>
</form>
<p class="role">Токен: <code>uv run python app.py --token</code> — тот же, что для /v1/*.</p>
</body>
</html>"""


def key_page(provider_name: str, *, has_key: bool, notice: str = "") -> str:
    """Ввод ключа одного провайдера: форма плюс честное предупреждение."""
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    value_hint = "сейчас задан — можно оставить пустым, чтобы удалить" if has_key else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ключ — {escape(provider_name)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 30rem; padding: 0 1rem; }}
  input {{ width: 100%; padding: .4rem; font-family: monospace; }}
  button {{ margin-top: .6rem; padding: .3rem .9rem; }}
  .role {{ opacity: .6; font-size: .85rem; }}
  .notice {{ background: rgba(42, 128, 60, .15); padding: .5rem .8rem; border-radius: .4rem; }}
</style>
</head>
<body>
<h1>Ключ: {escape(provider_name)}</h1>
{notice_html}
<form method="post" action="/key">
  <input type="hidden" name="id" value="{escape(provider_name, quote=True)}">
  <input name="api_key" type="password" placeholder="вставьте ключ" autocomplete="off" autofocus>
  <p class="role">{escape(value_hint)}</p>
  <button type="submit">сохранить</button>
  <a href="/">назад</a>
</form>
<p class="role">Ключ остаётся на устройстве: он уходит только к провайдеру в его запросе.</p>
</body>
</html>"""
