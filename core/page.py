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


#: Крошечный скрипт копирования: кнопка кладёт адрес в буфер, при отказе
#: (старый браузер, http) выделяет текст — останется Ctrl+C. Весь остальной
#: странице JavaScript не нужен.
COPY_SCRIPT = """<script>
function cp(el, id) {
  var node = document.getElementById(id);
  var done = function() { el.textContent = 'скопировано ✓'; setTimeout(function() { el.textContent = 'копировать'; }, 2000); };
  if (navigator.clipboard) { navigator.clipboard.writeText(node.textContent).then(done, function() { select(node); }); }
  else { select(node); }
  function select(n) { var r = document.createRange(); r.selectNodeContents(n); window.getSelection().removeAllRanges(); window.getSelection().addRange(r); }
}
</script>"""


def _copy_line(value: str, node_id: str) -> str:
    """Адрес + кнопка «копировать»: текст не редактируется, кнопка — в буфер."""
    return (
        f'<span class="addr"><code id="{escape(node_id, quote=True)}">{escape(value)}</code> '
        f'<button type="button" class="ghost" onclick="cp(this, '
        f"'{escape(node_id, quote=True)}')\">копировать</button></span>"
    )


def _nav(*, can_edit: bool) -> str:
    """Верхняя строка: личные разделы и выход."""
    links = [
        '<a href="/stats">статистика</a>',
        '<a href="/tokens">приложения и токены</a>',
    ]
    if can_edit:
        links.append('<a href="/logs">журнал</a>')
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

    # Логотип: первая валидная ссылка из списка, если админ заполнил.
    logo_html = ""
    for line in str(entry.get("logoUrl") or "").splitlines():
        candidate = _safe_url(line)
        if candidate:
            logo_html = (
                f'<img class="logo" src="{escape(candidate, quote=True)}" alt="" '
                f'onerror="this.remove()"> '
            )
            break

    # Справка о модели: контекст, цена, способности — если админ заполнил.
    facts = [
        escape(entry[key])
        for key in ("context", "pricing", "capabilities")
        if entry.get(key)
    ]
    facts_html = f'<span class="facts">{" · ".join(facts)}</span>' if facts else ""

    # Страница провайдера с описанием моделей и ценами — ссылка под моделью.
    if _safe_url(entry.get("infoUrl")):
        facts_html += (
            f'<span class="facts"><a href="{escape(_safe_url(entry["infoUrl"]), quote=True)}" '
            f'target="_blank" rel="noopener">описание и цены</a></span>'
        )

    # Действие над строкой. Админ правит определение (править/убрать),
    # человек ведёт своё пространство: ключ, тест, своя модель. Вход к
    # провайдеру и получение ключа — кнопками: это витрина провайдера.
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
    if _safe_url(entry.get("loginUrl")):
        actions.append(
            f'<a class="btn-ghost" href="{escape(_safe_url(entry["loginUrl"]), quote=True)}" '
            f'target="_blank" rel="noopener">вход у провайдера</a>'
        )
    if _safe_url(entry.get("getApiKeyUrl")):
        actions.append(
            f'<a class="btn-ghost" href="{escape(_safe_url(entry["getApiKeyUrl"]), quote=True)}" '
            f'target="_blank" rel="noopener">где взять ключ</a>'
        )
    if can_edit:
        actions.append(f'<a class="btn-ghost" href="/edit/{entry_id}">править</a>')
        if can_remove(entry, is_superuser=can_edit):
            actions.append(
                f'<form method="post" action="/remove/{entry_id}">'
                f'<button type="submit" class="ghost danger">убрать</button></form>'
            )

    # Адрес провайдера на страницах не показывается никому — ни человеку,
    # ни админу: витрина делится названием, адрес живёт только в формах
    # админа (добавить/править), иначе управлять каталогом нечем.
    return (
        f'<tr class="{"on" if entry.get("enabled") else ""}">'
        f'<td class="name">{logo_html}{name} {" ".join(badges)}</td>'
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
        rows = '<tr><td colspan="5" class="empty">каталог пуст</td></tr>'

    notice_html = (
        f'<p class="notice{" bad" if notice_bad else ""}">{escape(notice)}</p>'
        if notice
        else ""
    )
    role_html = f'<p class="role">{escape(role)}</p>' if role else ""

    # Добавить провайдера может каждый: админ — в каталог для всех,
    # человек — в своё личное пространство (форма рассказывает разницу).
    address_html = (
        '<p class="role">Для приложений (тип endpoint — OpenAI): '
        f"{_copy_line(app_address, 'v1addr')}</p>"
        if app_address
        else ""
    )
    copy_script = COPY_SCRIPT if app_address else ""
    add_html = '<p class="add"><a class="btn-ghost" href="/add">+ добавить провайдера</a></p>'

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
  img.logo {{ height: 1.2em; vertical-align: middle; }}
  a.btn-ghost {{ display: inline-block; padding: .25rem .7rem; border: 1px solid #8886; border-radius: .4rem; text-decoration: none; color: inherit; }}
  .empty {{ opacity: .6; }}
  .notice {{ background: rgba(42, 128, 60, .15); padding: .5rem .8rem; border-radius: .4rem; }}
  .notice.bad {{ background: rgba(160, 48, 48, .15); }}
  .role {{ opacity: .6; font-size: .85rem; }}
  .nav {{ font-size: .9rem; }}
  .addr code {{ font-family: monospace; user-select: all; }}
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
<tr><th>провайдер</th><th>модель</th><th>проверки</th><th>ключ</th><th></th></tr>
{rows}
</table>

{add_html}

<p class="role">Ключи хранятся только на этом устройстве и уходят напрямую провайдеру —
на сервер каталога они не попадают никогда.</p>
{copy_script}
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
            field("Страница с описанием и ценами", "infoUrl", entry.get("infoUrl", "")),
            field("Контекст", "context", entry.get("context", ""), placeholder="128K"),
            field("Цена", "pricing", entry.get("pricing", ""), placeholder="$0.1/1M вход, $0.4/1M выход"),
            field(
                "Способности", "capabilities", entry.get("capabilities", ""),
                placeholder="vision, thinking, tools",
            ),
            (
                '<label>Логотипы (URL провайдера по строке)'
                f'<textarea name="logoUrl" rows="3">{escape(entry.get("logoUrl", ""))}</textarea></label>'
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
  input, textarea {{ width: 100%; padding: .35rem; }}
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


def add_page(
    *,
    values: dict | None = None,
    models: list[str] | None = None,
    models_error: str = "",
    is_admin: bool = False,
    notice: str = "",
) -> str:
    """Форма добавления провайдера: полная, как у правки, плюс ключ и модели.

    ``values`` — уже введённое (после «показать модели» ничего не теряется);
    ``models`` — список с сервера (тогда модель выбирается из списка);
    ``models_error`` — почему список не получился (подсказка человеку).
    """
    v = values or {}

    def field(label: str, name: str, *, placeholder: str = "", value: str = "") -> str:
        return (
            f'<label>{escape(label)}'
            f'<input name="{name}" value="{escape(str(value), quote=True)}" '
            f'placeholder="{escape(placeholder, quote=True)}"></label>'
        )

    if models is not None:
        options = list(models)
        current = str(v.get("model") or "")
        if current and current not in options:
            options.insert(0, current)
        opts = "\n    ".join(
            f'<option value="{escape(option, quote=True)}"'
            f'{" selected" if option == current else ""}>{escape(option)}</option>'
            for option in options
        )
        model_control = f'<label>Модель<select name="model">\n    {opts}\n  </select></label>'
    else:
        model_control = field(
            "Модель", "model", value=str(v.get("model") or ""), placeholder="qwen3:0.6b"
        )

    fetch_note = (
        escape(models_error)
        if models_error
        else "Кнопка «показать модели» запросит список у провайдера — она ниже, под полем модели."
    )
    destination = (
        "попадёт в каталог — видят все"
        if is_admin
        else "попадёт в ваше личное пространство — видят только вы"
    )
    notice_html = f'<p class="notice bad">{escape(notice)}</p>' if notice else ""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Новый провайдер — AI-приставка</title>
<style>
{_style()}
  body {{ max-width: 34rem; }}
  input, select, textarea {{ width: 100%; padding: .35rem; font-family: monospace; }}
  button {{ margin: .3rem .3rem 0 0; }}
</style>
</head>
<h1>Новый провайдер</h1>
{_nav(can_edit=is_admin)}
{notice_html}
<form method="post" action="/add">
  {field("Название", "name", value=str(v.get("name") or ""), placeholder="Мой LMS")}
  {field("Адрес (базовый, без /chat/completions)", "url", value=str(v.get("url") or ""), placeholder="https://…/v1")}
  {field("API-ключ (останется на устройстве)", "api_key", value="", placeholder="sk-…")}
  {model_control}
  <button type="submit" name="action" value="fetch">показать модели</button>
  {field("Страница логина провайдера", "loginUrl", value=str(v.get("loginUrl") or ""))}
  {field("Где получить ключ", "getApiKeyUrl", value=str(v.get("getApiKeyUrl") or ""))}
  {field("Страница с описанием и ценами", "infoUrl", value=str(v.get("infoUrl") or ""))}
  <label>Логотипы (URL провайдера по строке)
  <textarea name="logoUrl" rows="3">{escape(str(v.get("logoUrl") or ""))}</textarea></label>
  {field("Контекст", "context", value=str(v.get("context") or ""), placeholder="128K")}
  {field("Цена", "pricing", value=str(v.get("pricing") or ""), placeholder="$0.1/1M вход, $0.4/1M выход")}
  {field("Способности", "capabilities", value=str(v.get("capabilities") or ""), placeholder="vision, thinking, tools")}
  <button type="submit" name="action" value="save">сохранить</button>
  <a href="/">назад</a>
</form>
<p class="role">{destination}. Ключ уходит только к провайдеру, на сервер каталога не попадает никогда.</p>
<p class="role">{fetch_note}</p>
<p class="role">Цена, контекст и способности — справка для выбора модели: заполните
значениями выбранной модели, её увидят все. Если провайдер не отдаёт список
моделей без ключа — введите ключ выше и нажмите «показать модели» ещё раз.</p>
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
    copy_script = ""
    if app_address:
        copy_script = COPY_SCRIPT
        address_html = f"""<h2>Настройка в приложении</h2>
<p class="role">Тип endpoint — <b>OpenAI</b>. Адрес вставьте в поле
«custom base URL», а ключом будет ваш токен ниже.</p>
<p>{_copy_line(app_address, "v1big")}</p>"""

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
  .addr code {{ font-family: monospace; user-select: all; }}
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
{copy_script}
</body>
</html>"""


def stats_page(
    rows: list[dict], *, can_edit: bool = False, notice: str = "",
    penalized: list[dict] | None = None,
) -> str:
    """Статистика по моделям: мои расходы, проверки сообщества, способности.

    Каждому видны его собственные расходы; админу сверху — суммы «все
    люди» и секция «на паузе» с кнопкой прощения (репутация моделей).
    Строки приходят собранными (webui), здесь только разметка.
    """
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    people_column = "<th>все люди</th>" if can_edit else ""

    def paused_html() -> str:
        """Секция «на паузе» — только админу, кнопка «вернуть» рядом."""
        if not can_edit or not penalized:
            return ""
        lines = [
            "<h2>На паузе</h2>",
            "<p class=\"role\">Модели с штрафами за поломки. Пауза снимается "
            "сама после нормального ответа — или кнопкой ниже.</p>",
            "<table>",
            "<tr><th>провайдер/модель</th><th>штрафов</th><th>блокировок</th>"
            "<th>вернётся через</th><th></th></tr>",
        ]
        for row in penalized:
            left = int(row.get("until", 0))
            when = f"{left // 60} мин" if left else "—"
            lines.append(
                f'<tr><td class="model">{escape(str(row.get("key") or "—"))}</td>'
                f'<td>{int(row.get("fails", 0))}</td>'
                f'<td>{int(row.get("blocks", 0))}</td>'
                f'<td>{when}</td>'
                f'<td><form method="post" action="/stats/release" style="display:inline">'
                f'<input type="hidden" name="key" value="{escape(str(row.get("key") or ""))}">'
                f'<button type="submit">вернуть</button></form></td></tr>'
            )
        lines.append("</table>")
        return "\n".join(lines)

    def cell(row: dict) -> str:
        tokens = row.get("tokens") or (0, 0)
        tokens_text = f"{tokens[0]} / {tokens[1]}" if (tokens[0] or tokens[1]) else "—"
        all_text = ""
        if can_edit:
            all_row = row.get("all") or {}
            all_text = (
                f"<td>{int(all_row.get('requests', 0))} · "
                f"{int(all_row.get('prompt', 0))}/{int(all_row.get('completion', 0))}</td>"
            )
        caps = escape(str(row.get("caps") or "—"))
        return (
            f'<tr><td class="model">{escape(str(row.get("model") or "—"))}</td>'
            f'<td>{escape(str(row.get("provider") or ""))}</td>'
            f'<td>{int(row.get("requests", 0))}{" (" + str(row.get("fails", 0)) + " ✗)" if row.get("fails") else ""}</td>'
            f'<td class="num">{tokens_text}</td>'
            f'<td>{int(row.get("switches", 0))}</td>'
            f'<td class="num">{escape(str(row.get("community") or "—"))}</td>'
            f'<td class="caps">{caps}</td>'
            f"{all_text}</tr>"
        )

    body = "\n".join(cell(row) for row in rows) or (
        f'<tr><td colspan="{8 if can_edit else 7}" class="empty">пока нет ни моделей, ни запросов</td></tr>'
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Статистика — AI-приставка</title>
<style>
{_style()}
  body {{ max-width: 64rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .35rem .5rem; border-bottom: 1px solid #8884; }}
  th {{ font-size: .85rem; opacity: .7; }}
  td.num {{ font-family: monospace; white-space: nowrap; }}
  td.model {{ white-space: nowrap; }}
  td.caps {{ opacity: .7; }}
</style>
</head>
<h1>Статистика</h1>
{_nav(can_edit=can_edit)}
{notice_html}
<p class="role">Твои расходы по моделей; «сообщество» — сколько людей смогли
пользоваться моделью. Обновляется само раз в 30 секунд.</p>
<table>
<tr><th>модель</th><th>провайдер</th><th>мои запросы</th><th>токены в / вы</th><th>переключений</th><th>сообщество</th><th>способности</th>{people_column}</tr>
{body}
</table>
{paused_html()}
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


def _log_row(item: dict) -> str:
    """Строка журнала: событие плюс тела запроса/ответа, если записаны."""
    extra = ""
    for key, label in (("req", "запрос"), ("resp", "ответ")):
        value = str(item.get(key) or "")
        if value:
            extra += (
                f'<div class="body" title="{escape(value, quote=True)}">'
                f"{escape(label)}: {escape(value[:300])}</div>"
            )
    return (
        f'<tr><td class="ts">{escape(str(item.get("ts") or "")[11:19])}</td>'
        f'<td class="kind">{escape(str(item.get("kind") or ""))}</td>'
        f'<td>{escape(str(item.get("user") or ""))}</td>'
        f'<td class="text">{escape(str(item.get("text") or ""))}{extra}</td></tr>'
    )


def logs_page(
    events: list[dict],
    *,
    autoscroll: bool = False,
    bodies: bool = False,
    notice: str = "",
) -> str:
    """Журнал событий: таблица, новые сверху, обновление раз в 5 секунд.

    Без JavaScript страница работает: обновление — meta-refresh; кнопка
    autoscroll кладёт флаг в cookie, а сам скролл к свежим (наверх) делает
    крошечный скрипт — без него остаётся обычное поведение браузера.
    ``bodies`` — пишутся ли тела запросов (кнопка переключает до рестарта).
    """
    rows = "\n".join(_log_row(item) for item in events) or (
        '<tr><td colspan="4" class="empty">событий пока нет</td></tr>'
    )
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    bodies_note = (
        '<p class="notice">тела запросов и ответов пишутся на диск — '
        'не оставляйте включённым постоянно: это чужая переписка</p>'
        if bodies
        else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="5">
<title>Журнал — AI-приставка</title>
<style>
{_style()}
  body {{ max-width: 62rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
  th, td {{ text-align: left; padding: .3rem .5rem; border-bottom: 1px solid #8884; }}
  td.ts {{ white-space: nowrap; font-family: monospace; }}
  td.kind {{ white-space: nowrap; }}
  td.text {{ word-break: break-word; }}
  td.body {{ font-family: monospace; font-size: .8em; opacity: .7; word-break: break-all; white-space: pre-wrap; }}
</style>
</head>
<h1>Журнал</h1>
{_nav(can_edit=True)}
{notice_html}
<p><form method="post" action="/logs/autoscroll" style="display:inline">
  <button type="submit" class="ghost">автоскролл: {"вкл" if autoscroll else "выкл"}</button>
</form>
<form method="post" action="/logs/bodies" style="display:inline">
  <button type="submit" class="ghost">тела запросов: {"вкл" if bodies else "выкл"}</button>
</form>
<form method="post" action="/logs/clear" style="display:inline">
  <button type="submit" class="ghost danger">очистить</button>
</form>
<span class="role">обновляется само раз в 5 секунд</span></p>
{bodies_note}
<table>
<tr><th>время</th><th>тип</th><th>кто</th><th>событие</th></tr>
{rows}
</table>
<script>
if (document.cookie.indexOf("ai_bridge_autoscroll=on") >= 0) window.scrollTo(0, 0);
</script>
</body>
</html>"""


def users_page(users: list[dict], *, current_id: str = "", notice: str = "") -> str:
    """Пользователи приставки: блокировка и удаление (модерация)."""
    notice_html = f'<p class="notice">{escape(notice)}</p>' if notice else ""
    if users:
        rows = []
        for item in users:
            mine = str(item.get("id")) == current_id
            blocked = bool(item.get("blocked"))
            badge = ' <span class="badge badge-dead">заблокирован</span>' if blocked else ""
            controls = "<td></td>" if mine else (
                f'<td><form method="post" action="/users/block">'
                f'<input type="hidden" name="id" value="{escape(str(item.get("id")), quote=True)}">'
                f'<button type="submit" class="ghost">'
                f'{"разблокировать" if blocked else "заблокировать"}</button></form> '
                f'<form method="post" action="/users/delete">'
                f'<input type="hidden" name="id" value="{escape(str(item.get("id")), quote=True)}">'
                f'<button type="submit" class="ghost danger">удалить</button></form></td>'
            )
            rows.append(
                f'<tr><td>{escape(str(item.get("email") or ""))}{badge}</td>'
                f'<td>{escape(str(item.get("role") or ""))}</td>'
                f'<td>{escape(str(item.get("lastLogin") or "")[:10])}</td>'
                f"{controls}</tr>"
            )
        rows_html = "\n".join(rows)
    else:
        rows_html = '<tr><td colspan="4" class="empty">никто ещё не регистрировался</td></tr>'
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Пользователи — AI-приставка</title>
<style>
{_style()}
  table {{ border-collapse: collapse; width: 100%; max-width: 44rem; }}
  th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8884; }}
</style>
</head>
<h1>Пользователи</h1>
{_nav(can_edit=True)}
{notice_html}
<p class="role">Регистрация открыта; защита — модерация: заблокированный
не пускается ни одной приставкой (проверка перед каждым запросом),
удалённый теряет аккаунт и своё пространство на этом устройстве.</p>
<table>
<tr><th>почта</th><th>роль</th><th>последний вход</th><th></th></tr>
{rows_html}
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
    facts: str = "",
) -> str:
    """Выбор своей модели провайдера: список живых моделей радиокнопками."""
    notice_html = (
        f'<p class="notice bad">{escape(notice)}</p>' if notice else ""
    )
    facts_html = (
        f'<p class="role">Справка о текущей модели: {escape(facts)}</p>' if facts else ""
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
{facts_html}
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
