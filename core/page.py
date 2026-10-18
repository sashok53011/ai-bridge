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


def _style() -> str:
    """Общий вид страниц: одна тёмная/светлая тема на всех формах."""
    return """  :root { color-scheme: light dark; }
  body { font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; }
  h1 { font-size: 1.4rem; }
  input { padding: .35rem; }
  button { padding: .25rem .7rem; border: 1px solid #8886; border-radius: .4rem; background: transparent; cursor: pointer; }
  button.ghost { opacity: .8; }
  button.danger { color: #c0392b; }
  form { display: inline; }
  .role { opacity: .6; font-size: .85rem; }
  .notice { background: rgba(42, 128, 60, .15); padding: .5rem .8rem; border-radius: .4rem; }
  .notice.bad { background: rgba(160, 48, 48, .15); }
  .nav { font-size: .9rem; }
  .nav button.link { border: none; padding: 0; font-size: .9rem; text-decoration: underline; color: inherit; }
  label { display: block; margin: .5rem 0; }"""


def _copyable(value: str) -> str:
    """Поле с адресом: клик выделяет и копирует в буфер.

    Без JavaScript страница работает: поле просто выделяется, дальше
    Ctrl+C — как обычно. Копирование по клику — прогрессивное улучшение.
    """
    return (
        f'<input readonly class="copy" value="{escape(value, quote=True)}" '
        f'onclick="this.select(); navigator.clipboard && '
        f'navigator.clipboard.writeText(this.value)" '
        f'title="клик — копировать в буфер">'
    )


def _nav(*, can_edit: bool) -> str:
    """Верхняя строка: личные разделы и выход."""
    links = ['<a href="/tokens">приложения и токены</a>']
    if can_edit:
        links.append('<a href="/users">пользователи</a>')
    links.append(
        '<form method="post" action="/logout" style="display:inline">'
        '<button class="link" type="submit">выйти</button></form>'
    )
    return f'<p class="nav">{" · ".join(links)}</p>'


def _badge(text: str, kind: str = "") -> str:
    cls = f"badge badge-{kind}" if kind else "badge"
    return f'<span class="{cls}">{escape(text)}</span>'


def _safe_url(value: object) -> str:
    """Адрес для ссылки: только http/https, иначе пусто (чужая схема — не ссылка)."""
    text = str(value or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    return ""


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

    # Справка о модели: контекст, цена, способности — если админ заполнил.
    facts = [
        escape(entry[key])
        for key in ("context", "pricing", "capabilities")
        if entry.get(key)
    ]
    facts_html = f'<span class="facts">{" · ".join(facts)}</span>' if facts else ""

    # Ссылки на самого провайдера: где войти и где взять ключ — из полей каталога.
    provider_links = []
    if _safe_url(entry.get("loginUrl")):
        provider_links.append(
            f'<a href="{escape(_safe_url(entry["loginUrl"]), quote=True)}" '
            f'target="_blank" rel="noopener">страница логина</a>'
        )
    if _safe_url(entry.get("getApiKeyUrl")):
        provider_links.append(
            f'<a href="{escape(_safe_url(entry["getApiKeyUrl"]), quote=True)}" '
            f'target="_blank" rel="noopener">получить ключ</a>'
        )
    links_html = (
        f'<span class="facts">{" · ".join(provider_links)}</span>' if provider_links else ""
    )

    # Действие над строкой. Админ правит определение (править/убрать),
    # человек ведёт своё пространство: ключ, тест, своя модель.
    toggle_label = "выключить" if entry.get("enabled") else "включить"
    entry_id = escape(entry["id"], quote=True)
    actions = [
        f'<form method="post" action="/toggle/{entry_id}">'
        f'<button type="submit">{toggle_label}</button></form>',
        f'<form method="post" action="/check/{entry_id}">'
        f'<button type="submit" class="ghost">проверить</button></form>',
        f'<a class="btn-ghost" href="/key/{entry_id}">ключ</a>',
        f'<a class="btn-ghost" href="/pick/{entry_id}">модель</a>',
        f'<form method="post" action="/test/{entry_id}">'
        f'<button type="submit" class="ghost">тест</button></form>',
    ]
    if can_edit:
        actions.append(f'<a class="btn-ghost" href="/edit/{entry_id}">править</a>')
        if can_remove(entry, is_superuser=can_edit):
            actions.append(
                f'<form method="post" action="/remove/{entry_id}">'
                f'<button type="submit" class="ghost danger">убрать</button></form>'
            )

    return (
        f'<tr class="{"on" if entry.get("enabled") else ""}">'
        f'<td class="name">{name} {" ".join(badges)}</td>'
        f'<td class="url">{escape(entry["url"])}{links_html}</td>'
        f'<td class="model">{escape(entry.get("model") or "—")}{facts_html}</td>'
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
    notice_bad: bool = False,
    role: str = "",
    app_address: str = "",
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

    notice_html = (
        f'<p class="notice{" bad" if notice_bad else ""}">{escape(notice)}</p>'
        if notice
        else ""
    )
    role_html = f'<p class="role">{escape(role)}</p>' if role else ""

    # Добавление провайдеров — привилегия админа: человек управляет лишь
    # своим пространством (ключ, модель, вкл).
    address_html = (
        '<p class="role">Для приложений (тип endpoint — OpenAI): '
        f"{_copyable(app_address)}</p>"
        if app_address
        else ""
    )
    add_html = ""
    if can_edit:
        add_html = """<details>
<summary>Добавить провайдера</summary>
<form method="post" action="/add">
  <input name="name" placeholder="Имя" required>
  <input name="url" type="url" placeholder="https://…/v1" required>
  <input name="model" placeholder="модель" required>
  <input name="api_key" type="password" placeholder="ключ (останется на устройстве)" autocomplete="off">
  <button type="submit">добавить</button>
</form>
<p class="role">администратору — запись в каталог для всех.</p>
</details>"""

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
  button.link {{ border: none; padding: 0; font-size: .9rem; text-decoration: underline; color: inherit; }}
  .actions {{ white-space: nowrap; }}
  .facts {{ display: block; font-size: .8em; opacity: .7; }}
  a.btn-ghost {{ display: inline-block; padding: .25rem .7rem; border: 1px solid #8886; border-radius: .4rem; text-decoration: none; color: inherit; }}
  .empty {{ opacity: .6; }}
  .notice {{ background: rgba(42, 128, 60, .15); padding: .5rem .8rem; border-radius: .4rem; }}
  .notice.bad {{ background: rgba(160, 48, 48, .15); }}
  .role {{ opacity: .6; font-size: .85rem; }}
  .nav {{ font-size: .9rem; }}
  input.copy {{ width: 24rem; max-width: 100%; font-family: monospace; }}
  .add {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #8884; }}
  .add input {{ margin: .2rem; padding: .3rem; }}
  details {{ margin-top: 1rem; }}
  input[type=password] {{ font-family: monospace; }}
</style>
</head>
<body>
<h1>AI-приставка</h1>
{_nav(can_edit=can_edit)}
{role_html}
{notice_html}
{address_html}
<table>
<tr><th>провайдер</th><th>адрес</th><th>модель</th><th>проверки</th><th>ключ</th><th></th></tr>
{rows}
</table>

{add_html}

<p class="role">Ключи хранятся только на этом устройстве и уходят напрямую провайдеру —
на сервер каталога они не попадают никогда.</p>
</body>
</html>"""


def edit_page(entry: dict, *, notice: str = "") -> str:
    """Форма правки одного провайдера: определение плюс справка о модели.

    Пустое поле ничего не стирает — сервер отбрасывает пустые значения
    до правки, так что можно менять одну строку, не трогая остальные.
    """
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""

    def field(label: str, name: str, value: str, *, placeholder: str = "") -> str:
        return (
            f'<label>{escape(label)}'
            f'<input name="{name}" value="{escape(value, quote=True)}" '
            f'placeholder="{escape(placeholder, quote=True)}"></label>'
        )

    fields = "\n  ".join(
        [
            field("Название", "name", entry.get("name", "")),
            field("Адрес", "url", entry.get("url", "")),
            field("Модель", "model", entry.get("model", ""), placeholder="qwen3:0.6b"),
            field("Приоритет", "fallbackPriority", str(entry.get("fallbackPriority", 0))),
            field("Страница логина", "loginUrl", entry.get("loginUrl", "")),
            field("Где взять ключ", "getApiKeyUrl", entry.get("getApiKeyUrl", "")),
            field("Контекст", "context", entry.get("context", ""), placeholder="128K"),
            field("Цена", "pricing", entry.get("pricing", ""), placeholder="$0.1/1M вход, $0.4/1M выход"),
            field(
                "Способности", "capabilities", entry.get("capabilities", ""),
                placeholder="vision, thinking, tools",
            ),
        ]
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Правка — {escape(entry.get("name", ""))}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 34rem; padding: 0 1rem; }}
  label {{ display: block; margin: .5rem 0; }}
  input {{ width: 100%; padding: .35rem; }}
  button {{ margin-top: .8rem; padding: .3rem .9rem; }}
  .role {{ opacity: .6; font-size: .85rem; }}
  .notice {{ background: rgba(42, 128, 60, .15); padding: .5rem .8rem; border-radius: .4rem; }}
</style>
</head>
<body>
<h1>Правка: {escape(entry.get("name", ""))}</h1>
{_nav(can_edit=True)}
{notice_html}
<form method="post" action="/edit/{escape(entry.get("id", ""), quote=True)}">
  {fields}
  <button type="submit">сохранить</button>
  <a href="/">назад</a>
</form>
<p class="role">Цена и контекст — справка для выбора модели, её видят все.
Способности — через запятую: vision, thinking, tools. Пустое поле не стирает
значение.</p>
</body>
</html>"""


def login_page(notice: str = "", *, signup_open: bool = True) -> str:
    """Вход в приставку: логин/пароль аккаунта сервера каталога.

    Токен остаётся аварийным входом: если сервер каталога ляжет,
    админ всё равно попадёт на страницу. Регистрация — открытая,
    но ссылка показывается только когда сервер её вообще принимает.
    """
    notice_html = (
        f'<p class="notice bad">{escape(notice)}</p>' if notice else ""
    )
    signup_html = (
        '<p class="role">нет аккаунта? <a href="/signup">зарегистрироваться</a></p>'
        if signup_open
        else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вход — AI-приставка</title>
<style>
{_style()}
  body {{ max-width: 30rem; }}
  input {{ width: 100%; margin: .2rem 0; font-family: monospace; }}
</style>
</head>
<body>
<h1>AI-приставка</h1>
{notice_html}
<form method="post" action="/login">
  <input name="email" type="email" placeholder="почта" autocomplete="username" autofocus>
  <input name="password" type="password" placeholder="пароль" autocomplete="current-password">
  <button type="submit">войти</button>
</form>
{signup_html}
<details>
<summary class="role">вход по токену (аварийный)</summary>
<form method="post" action="/login">
  <input name="token" type="password" placeholder="токен приставки" autocomplete="off">
  <button type="submit">войти по токену</button>
</form>
<p class="role">Токен: <code>uv run python app.py --token</code>.</p>
</details>
</body>
</html>"""


def signup_page(notice: str = "") -> str:
    """Регистрация: открыта, без подтверждения почты — защита это модерация."""
    notice_html = (
        f'<p class="notice bad">{escape(notice)}</p>' if notice else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Регистрация — AI-приставка</title>
<style>
{_style()}
  body {{ max-width: 30rem; }}
  input {{ width: 100%; margin: .2rem 0; font-family: monospace; }}
</style>
</head>
<body>
<h1>Регистрация</h1>
{notice_html}
<form method="post" action="/signup">
  <input name="email" type="email" placeholder="почта" autocomplete="username" autofocus required>
  <input name="password" type="password" placeholder="пароль" autocomplete="new-password" required>
  <input name="password2" type="password" placeholder="ещё раз пароль" autocomplete="new-password" required>
  <button type="submit">создать аккаунт</button>
  <a href="/login">назад ко входу</a>
</form>
<p class="role">Аккаунт открывает ваше пространство приставки: свои ключи,
свои переключатели, свои токены для приложений.</p>
</body>
</html>"""


def tokens_page(
    tokens: list[dict],
    *,
    app_address: str = "",
    api_on: bool = True,
    notice: str = "",
    can_edit: bool = False,
) -> str:
    """Приложения и токены: адрес для настройки, тумблер доступа, список."""
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    if tokens:
        rows = "\n".join(
            f'<tr><td>{escape(str(item.get("name") or ""))}</td>'
            f'<td><code>…{escape(str(item.get("tail") or ""))}</code></td>'
            f'<td>{escape(str(item.get("created") or "")[:10])}</td>'
            f'<td><form method="post" action="/tokens/revoke">'
            f'<input type="hidden" name="tail" value="{escape(str(item.get("tail") or ""), quote=True)}">'
            f'<button type="submit" class="ghost danger">отозвать</button></form></td></tr>'
            for item in tokens
        )
    else:
        rows = '<tr><td colspan="4" class="empty">токенов пока нет</td></tr>'

    api_button = "включить доступ" if not api_on else "выключить доступ"
    api_status = (
        "доступ приложений включён — токены работают"
        if api_on
        else "доступ приложений ВЫКЛЮЧЕН — ни один токен не пройдёт"
    )
    address_html = ""
    if app_address:
        address_html = f"""<h2>Настройка в приложении</h2>
<p class="role">Тип endpoint — <b>OpenAI</b>. Адрес вставьте в поле
«custom base URL» (клик по полю копирует в буфер), а ключом будет ваш токен ниже.</p>
<p>{_copyable(app_address)}</p>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Приложения и токены — AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: collapse; width: 100%; max-width: 40rem; }}
  th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8884; }}
  input {{ margin: .2rem; }}
  input.copy {{ width: 100%; max-width: 40rem; font-family: monospace; }}
  h2 {{ font-size: 1.1rem; margin-top: 1.6rem; }}
  .api {{ background: rgba(42, 128, 60, .10); padding: .6rem .8rem; border-radius: .4rem; }}
  .api.off {{ background: rgba(160, 48, 48, .15); }}
</style>
</head>
<body>
<h1>Приложения и токены</h1>
{_nav(can_edit=can_edit)}
{notice_html}
{address_html}
<p class="api{" off" if not api_on else ""}">{escape(api_status)}
<form method="post" action="/tokens/api" style="float:right">
  <button type="submit" class="ghost">{api_button}</button>
</form></p>
<h2>Мои токены</h2>
<table>
<tr><th>название</th><th>хвост</th><th>создан</th><th></th></tr>
{rows}
</table>
<form method="post" action="/tokens/create">
  <input name="name" placeholder="название: телефон, ноутбук…" required>
  <button type="submit">создать токен</button>
</form>
<p class="role">Токен показывается один раз при создании — скопируйте.
Отозванный токен перестаёт работать мгновенно.</p>
<p><a href="/">← назад в каталог</a></p>
</body>
</html>"""


def token_created_page(token: str, name: str) -> str:
    """Один раз показать новый токен: скопируй и уходи."""
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Новый токен — AI-приставка</title>
<style>
{_style()}
  body {{ max-width: 40rem; }}
  input {{ width: 100%; font-family: monospace; }}
</style>
</head>
<h1>Новый токен: {escape(name)}</h1>
<input readonly value="{escape(token, quote=True)}" onclick="this.select()">
<p class="role">Скопируйте сейчас: показывается один раз. В приложении укажите
адрес приставки <code>/v1</code> и этот токен в Authorization.</p>
<p><a href="/tokens">← к токенам</a></p>
</body>
</html>"""


def users_page(users: list[dict], *, current_id: str = "", notice: str = "") -> str:
    """Пользователи приставки: список и удаление (модерация)."""
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    if users:
        rows = "\n".join(
            (
                f'<tr><td>{escape(str(item.get("email") or ""))}</td>'
                f'<td>{escape(str(item.get("role") or ""))}</td>'
                f'<td>{escape(str(item.get("lastLogin") or "")[:10])}</td>'
                + (
                    "<td></td>"
                    if str(item.get("id")) == current_id
                    else f'<td><form method="post" action="/users/delete">'
                    f'<input type="hidden" name="id" value="{escape(str(item.get("id")), quote=True)}">'
                    f'<button type="submit" class="ghost danger">удалить</button></form></td>'
                )
                + "</tr>"
            )
            for item in users
        )
    else:
        rows = '<tr><td colspan="4" class="empty">никто ещё не регистрировался</td></tr>'
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Пользователи — AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: collapse; width: 100%; max-width: 40rem; }}
  th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8884; }}
</style>
</head>
<h1>Пользователи</h1>
{_nav(can_edit=True)}
{notice_html}
<p class="role">Регистрация открыта, защита — модерация: удалённый человек
теряет доступ и своё пространство на этом устройстве.</p>
<table>
<tr><th>почта</th><th>роль</th><th>последний вход</th><th></th></tr>
{rows}
</table>
<p><a href="/">← назад в каталог</a></p>
</body>
</html>"""


def pick_model_page(
    provider_name: str,
    provider_id: str,
    models: list[str],
    *,
    current: str = "",
    notice: str = "",
    can_edit: bool = False,
) -> str:
    """Выбор своей модели провайдера: список живых моделей радиокнопками."""
    notice_html = (
        f'<p class="notice bad">{escape(notice)}</p>' if notice else ""
    )
    if models:
        rows = "\n".join(
            f'<label><input type="radio" name="model" value="{escape(model, quote=True)}"'
            f'{" checked" if model == current else ""}> {escape(model)}</label>'
            for model in models
        )
        body = f"""<form method="post" action="/pick/{escape(provider_id, quote=True)}">
  {rows}
  <button type="submit">выбрать</button>
</form>"""
    else:
        body = (
            '<p class="role">модели не получились — проверьте ключ '
            f'у «{escape(provider_name)}»</p>'
        )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Модель — {escape(provider_name)}</title>
<style>
{_style()}
  body {{ max-width: 34rem; }}
  label {{ display: block; margin: .2rem 0; }}
</style>
</head>
<h1>Модель: {escape(provider_name)}</h1>
{_nav(can_edit=can_edit)}
<p class="role">Какую модель обслуживать по умолчанию — выбор ваш, личный.</p>
{notice_html}
{body}
<p><a href="/">← назад в каталог</a></p>
</body>
</html>"""


def test_page(
    provider_name: str,
    model: str,
    question: str,
    *,
    answer: str = "",
    error: str = "",
    can_edit: bool = False,
) -> str:
    """Итог тестового чата: один вопрос — один ответ или честная ошибка."""
    notice_html = (
        f'<p class="notice bad">{escape(error)}</p>' if error else ""
    )
    answer_html = f"<p>{escape(answer)}</p>" if answer else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Тест — {escape(provider_name)}</title>
<style>
{_style()}
  body {{ max-width: 34rem; }}
  .q {{ opacity: .6; }}
</style>
</head>
<h1>Тест: {escape(provider_name)} · {escape(model or "—")}</h1>
{_nav(can_edit=can_edit)}
<p class="role">Тест — настоящий, но крошечный запрос: на платной модели он
стоит доли цента.</p>
<p class="q">«{escape(question)}»</p>
{notice_html}
{answer_html}
<p><a href="/">← назад в каталог</a></p>
</body>
</html>"""


def key_page(
    provider_name: str,
    *,
    provider_id: str,
    has_key: bool,
    notice: str = "",
    can_edit: bool = False,
    login_url: str = "",
    key_url: str = "",
) -> str:
    """Ввод ключа одного провайдера: форма плюс честное предупреждение.

    В скрытом поле — id провайдера (не имя: сервер ищет запись по id).
    Ссылки «где взять ключ» и «страница логина» — из полей каталога.
    """
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    value_hint = "сейчас задан — можно оставить пустым, чтобы удалить" if has_key else ""
    hints = []
    if _safe_url(key_url):
        hints.append(
            f'Ещё нет ключа? <a href="{escape(_safe_url(key_url), quote=True)}" '
            f'target="_blank" rel="noopener">получить ключ у провайдера</a>'
        )
    if _safe_url(login_url):
        hints.append(
            f'Страница входа провайдера: <a href="{escape(_safe_url(login_url), quote=True)}" '
            f'target="_blank" rel="noopener">открыть</a>'
        )
    hints_html = f'<p class="role">{" · ".join(hints)}</p>' if hints else ""
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
{_nav(can_edit=can_edit)}
{notice_html}
<form method="post" action="/key">
  <input type="hidden" name="id" value="{escape(provider_id, quote=True)}">
  <input name="api_key" type="password" placeholder="вставьте ключ" autocomplete="off" autofocus>
  <p class="role">{escape(value_hint)}</p>
  <button type="submit">сохранить</button>
  <a href="/">назад</a>
</form>
{hints_html}
<p class="role">Ключ остаётся на устройстве: он уходит только к провайдеру в его запросе.</p>
</body>
</html>"""
