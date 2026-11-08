"""Переводы интерфейса: EN / DE / RU.

Словари по ключам; ``t(key)`` возвращает строку на текущем языке
(задаётся через ``set_lang()`` перед отрисовкой страницы).
"""

from __future__ import annotations

from contextvars import ContextVar

#: Текущий язык (contextvar — безопасно в многопоточном сервере).
_current_lang: ContextVar[str] = ContextVar("lang", default="en")

#: Доступные языки.
LANGS = ("ru", "en", "de")

#: Человекочитаемые имена языков для переключателя.
LANG_NAMES = {"ru": "Русский", "en": "English", "de": "Deutsch"}


def set_lang(lang: str) -> None:
    """Установить язык для текущего запроса."""
    _current_lang.set(lang if lang in LANGS else "en")


def get_lang() -> str:
    """Вернуть текущий язык."""
    return _current_lang.get()

#: Словари переводов: ключ → {язык: строка}.
_STRINGS: dict[str, dict[str, str]] = {
    # --- навигация ---
    "nav.catalogue": {"ru": "каталог", "en": "catalogue", "de": "Katalog"},
    "nav.stats": {"ru": "статистика", "en": "stats", "de": "Statistik"},
    "nav.sql": {"ru": "SQL Playground", "en": "SQL Playground", "de": "SQL Playground"},
    "nav.builder": {"ru": "конструктор", "en": "builder", "de": "Konstruktor"},
    "nav.tokens": {"ru": "приложения и токены", "en": "apps & tokens", "de": "Apps & Tokens"},
    "nav.logs": {"ru": "журнал", "en": "journal", "de": "Journal"},
    "nav.users": {"ru": "пользователи", "en": "users", "de": "Benutzer"},
    "nav.logout": {"ru": "выйти", "en": "log out", "de": "abmelden"},
    "nav.theme": {"ru": "цветовая тема", "en": "colour theme", "de": "Farbthema"},
    "nav.theme.system": {"ru": "системная", "en": "system", "de": "System"},
    "nav.theme.emerald": {"ru": "зелёная", "en": "emerald", "de": "Smaragd"},
    "nav.theme.blue": {"ru": "синяя", "en": "blue", "de": "Blau"},
    "nav.theme.yellow": {"ru": "жёлтая", "en": "yellow", "de": "Gelb"},
    "nav.theme.purple": {"ru": "фиолетовая", "en": "purple", "de": "Violett"},
    "nav.theme.cyberpunk": {"ru": "киберпанк", "en": "cyberpunk", "de": "Cyberpunk"},
    "nav.theme.aurora": {"ru": "северное сияние", "en": "aurora", "de": "Aurora"},
    "nav.theme.synthwave": {"ru": "синтвейв", "en": "synthwave", "de": "Synthwave"},
    "nav.colors": {"ru": "свои цвета", "en": "custom colours", "de": "eigene Farben"},
    "nav.color.band_top": {"ru": "полоса верх", "en": "band top", "de": "Band oben"},
    "nav.color.band_bottom": {"ru": "полоса низ", "en": "band bottom", "de": "Band unten"},
    "nav.color.btn_top": {"ru": "кнопки верх", "en": "buttons top", "de": "Buttons oben"},
    "nav.color.btn_bottom": {"ru": "кнопки низ", "en": "buttons bottom", "de": "Buttons unten"},
    "nav.color.reset": {"ru": "сбросить", "en": "reset", "de": "zurücksetzen"},
    # --- вход ---
    "login.title": {"ru": "Вход", "en": "Sign in", "de": "Anmelden"},
    "login.email": {"ru": "почта", "en": "email", "de": "E-Mail"},
    "login.password": {"ru": "пароль", "en": "password", "de": "Passwort"},
    "login.submit": {"ru": "войти", "en": "sign in", "de": "anmelden"},
    "login.no_account": {"ru": "нет аккаунта?", "en": "no account?", "de": "kein Konto?"},
    "login.signup": {"ru": "зарегистрироваться", "en": "sign up", "de": "registrieren"},
    "login.emergency": {"ru": "вход по токену (аварийный)", "en": "token login (emergency)", "de": "Token-Login (Notfall)"},
    "login.token": {"ru": "токен приставки", "en": "device token", "de": "Geräte-Token"},
    "login.wrong": {"ru": "почта или пароль не подошли", "en": "email or password is wrong", "de": "E-Mail oder Passwort ist falsch"},
    # --- каталог ---
    "cat.search": {"ru": "поиск", "en": "search", "de": "Suche"},
    "cat.all": {"ru": "все", "en": "all", "de": "alle"},
    "cat.enabled": {"ru": "включён", "en": "enabled", "de": "aktiviert"},
    "cat.disabled": {"ru": "выключен", "en": "disabled", "de": "deaktiviert"},
    "cat.dead": {"ru": "нераб.", "en": "broken", "de": "defekt"},
    "cat.mine": {"ru": "ваш", "en": "yours", "de": "deins"},
    "cat.check": {"ru": "проверить", "en": "check", "de": "prüfen"},
    "cat.key": {"ru": "ключ", "en": "key", "de": "Schlüssel"},
    "cat.model": {"ru": "модель", "en": "model", "de": "Modell"},
    "cat.edit": {"ru": "править", "en": "edit", "de": "bearbeiten"},
    "cat.remove": {"ru": "убрать", "en": "remove", "de": "entfernen"},
    "cat.add": {"ru": "добавить", "en": "add", "de": "hinzufügen"},
    "cat.add_provider": {"ru": "+ добавить провайдера", "en": "+ add provider", "de": "+ Anbieter hinzufügen"},
    "cat.empty": {"ru": "каталог пуст", "en": "catalogue is empty", "de": "Katalog ist leer"},
    "cat.hero_status": {"ru": "ваше пространство AI-провайдеров", "en": "your AI provider space", "de": "Ihr KI-Anbieter-Bereich"},
    "cat.hero_title": {"ru": "Каталог провайдеров", "en": "Provider catalogue", "de": "Anbieterkatalog"},
    "cat.endpoint_label": {"ru": "Для приложений · endpoint OpenAI", "en": "For apps · OpenAI endpoint", "de": "Für Apps · OpenAI-Endpunkt"},
    "cat.providers": {"ru": "провайдеров", "en": "providers", "de": "Anbieter"},
    "cat.active": {"ru": "включено", "en": "active", "de": "aktiv"},
    "cat.with_key": {"ru": "с ключом", "en": "with key", "de": "mit Schlüssel"},
    "cat.key_set": {"ru": "задан", "en": "set", "de": "gesetzt"},
    # --- статистика ---
    "stats.title": {"ru": "Статистика", "en": "Statistics", "de": "Statistik"},
    "stats.model": {"ru": "модель", "en": "model", "de": "Modell"},
    "stats.requests": {"ru": "запросов", "en": "requests", "de": "Anfragen"},
    "stats.tokens_in": {"ru": "токенов вход", "en": "tokens in", "de": "Tokens rein"},
    "stats.tokens_out": {"ru": "токенов выход", "en": "tokens out", "de": "Tokens raus"},
    "stats.code_words": {"ru": "Кодовые слова", "en": "Code words", "de": "Codewörter"},
    # --- журнал ---
    "logs.title": {"ru": "Журнал", "en": "Journal", "de": "Journal"},
    "logs.clear": {"ru": "очистить", "en": "clear", "de": "löschen"},
    "logs.empty": {"ru": "событий пока нет", "en": "no events yet", "de": "noch keine Ereignisse"},
    "logs.show_body": {"ru": "показать тело", "en": "show body", "de": "Body anzeigen"},
    "logs.req": {"ru": "Тело запроса", "en": "Request body", "de": "Anfrage-Body"},
    "logs.resp": {"ru": "Тело ответа", "en": "Response body", "de": "Antwort-Body"},
    # --- пользователи ---
    "users.title": {"ru": "Пользователи", "en": "Users", "de": "Benutzer"},
    "users.block": {"ru": "заблокировать", "en": "block", "de": "blockieren"},
    "users.unblock": {"ru": "разблокировать", "en": "unblock", "de": "freigeben"},
    "users.delete": {"ru": "удалить", "en": "delete", "de": "löschen"},
    "users.blocked": {"ru": "заблокирован", "en": "blocked", "de": "blockiert"},
    "users.empty": {"ru": "никто ещё не регистрировался", "en": "no one has registered yet", "de": "noch niemand hat sich registriert"},
    "users.role": {"ru": "роль", "en": "role", "de": "Rolle"},
    "users.last_login": {"ru": "последний вход", "en": "last login", "de": "letzter Login"},
    # --- токены ---
    "tokens.title": {"ru": "Приложения и токены", "en": "Apps & Tokens", "de": "Apps & Tokens"},
    "tokens.create": {"ru": "создать токен", "en": "create token", "de": "Token erstellen"},
    "tokens.revoke": {"ru": "отозвать", "en": "revoke", "de": "widerrufen"},
    "tokens.name": {"ru": "название", "en": "name", "de": "Name"},
    "tokens.empty": {"ru": "токенов пока нет", "en": "no tokens yet", "de": "noch keine Tokens"},
    "tokens.api_on": {"ru": "включить доступ", "en": "enable access", "de": "Zugang aktivieren"},
    "tokens.api_off": {"ru": "выключить доступ", "en": "disable access", "de": "Zugang deaktivieren"},
    "tokens.api_on_hint": {"ru": "доступ приложений включён — токены работают", "en": "app access enabled — tokens work", "de": "App-Zugang aktiviert — Tokens funktionieren"},
    "tokens.api_off_hint": {"ru": "доступ приложений ВЫКЛЮЧЕН — ни один токен не пройдёт", "en": "app access DISABLED — no token will pass", "de": "App-Zugang DEAKTIVIERT — kein Token wird durchgelassen"},
    "tokens.setup": {"ru": "Настройка в приложении", "en": "App setup", "de": "App-Einrichtung"},
    "tokens.setup_hint": {"ru": "Тип endpoint — OpenAI. Адрес вставьте в поле «custom base URL», а ключом будет ваш токен ниже.", "en": "Endpoint type — OpenAI. Paste the address into the \"custom base URL\" field; your token below is the key.", "de": "Endpunkt-Typ — OpenAI. Fügen Sie die Adresse in das Feld „custom base URL“ ein; Ihr Token unten ist der Schlüssel."},
    # --- SQL Playground ---
    "sql.title": {"ru": "SQL Playground", "en": "SQL Playground", "de": "SQL Playground"},
    "sql.query": {"ru": "SQL-запрос", "en": "SQL query", "de": "SQL-Abfrage"},
    "sql.run": {"ru": "выполнить запрос", "en": "run query", "de": "Abfrage ausführen"},
    "sql.schema": {"ru": "Схема базы", "en": "Database schema", "de": "Datenbankschema"},
    "sql.result": {"ru": "Результат запроса", "en": "Query result", "de": "Abfrageergebnis"},
    "sql.hero_admin": {
        "ru": "Полный доступ: админ — любая база, пользователь — своя.",
        "en": "Full access: admin — any database, user — own only.",
        "de": "Voller Zugriff: Admin — jede Datenbank, Nutzer — nur eigene.",
    },
    "sql.hero_user": {
        "ru": "Доступ только к вашей базе данных.",
        "en": "Access to your own database only.",
        "de": "Zugriff nur auf Ihre eigene Datenbank.",
    },
    "sql.admin_hint": {
        "ru": "Разрешены все команды SQL.",
        "en": "All SQL commands are allowed.",
        "de": "Alle SQL-Befehle sind erlaubt.",
    },
    "sql.user_hint": {"ru": "Только SELECT.", "en": "SELECT only.", "de": "Nur SELECT."},
    "sql.time_limit": {
        "ru": "Ограничение по времени и объёму ответа.",
        "en": "Limited by time and result size.",
        "de": "Begrenzt nach Zeit und Ergebnisgröße.",
    },
    "sql.include_data": {
        "ru": "Показывать примеры записей",
        "en": "Show sample rows",
        "de": "Beispielzeilen anzeigen",
    },
    "sql.db": {"ru": "База:", "en": "Database:", "de": "Datenbank:"},
    "sql.db_catalogue": {"ru": "catalogue (каталог)", "en": "catalogue", "de": "Katalog"},
    "sql.db_user": {"ru": "user {id}", "en": "user {id}", "de": "Benutzer {id}"},
    "sql.db_yours": {"ru": "user {id} (ваша)", "en": "user {id} (yours)", "de": "Benutzer {id} (Ihre)"},
    "sql.no_dbs": {
        "ru": "Базы данных не найдены.",
        "en": "No databases found.",
        "de": "Keine Datenbanken gefunden.",
    },
    "sql.not_configured": {
        "ru": "База данных не настроена.",
        "en": "The database is not configured.",
        "de": "Die Datenbank ist nicht konfiguriert.",
    },
    "sql.schema_unavailable": {
        "ru": "Схема пока недоступна.",
        "en": "Schema is not available yet.",
        "de": "Das Schema ist noch nicht verfügbar.",
    },
    "sql.constructor": {
        "ru": "📐 визуальный конструктор",
        "en": "📐 visual builder",
        "de": "📐 visueller Konstruktor",
    },
    "sql.diagram": {
        "ru": "Связи между таблицами",
        "en": "Relations between tables",
        "de": "Beziehungen zwischen Tabellen",
    },
    "sql.fields": {"ru": "{n} полей", "en": "{n} columns", "de": "{n} Spalten"},
    "sql.f": {"ru": "поле", "en": "column", "de": "Spalte"},
    "sql.t": {"ru": "тип", "en": "type", "de": "Typ"},
    "sql.fk": {"ru": "FK: {a} → {b}", "en": "FK: {a} → {b}", "de": "FK: {a} → {b}"},
    "sql.tables_n": {"ru": "таблиц", "en": "tables", "de": "Tabellen"},
    "sql.rels_n": {"ru": "связей", "en": "relations", "de": "Beziehungen"},
    "sql.rows_n": {"ru": "строк", "en": "rows", "de": "Zeilen"},
    "sql.rows_one": {"ru": "строка", "en": "row", "de": "Zeile"},
    "sql.rows_few": {"ru": "строки", "en": "rows", "de": "Zeilen"},
    "sql.truncated": {
        "ru": " · показана первая часть",
        "en": " · first part shown",
        "de": " · erster Teil gezeigt",
    },
    "sql.too_big": {
        "ru": "Превышен лимит объёма ответа.",
        "en": "The result size limit was exceeded.",
        "de": "Die Ergebnisgrößen-Grenze wurde überschritten.",
    },
    "sql.no_rows": {
        "ru": "Запрос не вернул строк.",
        "en": "The query returned no rows.",
        "de": "Die Abfrage lieferte keine Zeilen.",
    },
    "sql.empty_schema": {"ru": "Схема пуста.", "en": "Schema is empty.", "de": "Das Schema ist leer."},
    "sql.legend_1n": {
        "ru": "один-ко-многим (1:N)",
        "en": "one-to-many (1:N)",
        "de": "eins-zu-viele (1:N)",
    },
    "sql.legend_nm": {
        "ru": "через связующую таблицу (N:M)",
        "en": "via junction table (N:M)",
        "de": "über Verknüpfungstabelle (N:M)",
    },
    "sql.legend_counts": {
        "ru": "таблиц: {t} · связей: {r} · связующих (N:M): {j}",
        "en": "tables: {t} · relations: {r} · junctions (N:M): {j}",
        "de": "Tabellen: {t} · Beziehungen: {r} · Verknüpfungen (N:M): {j}",
    },
    # --- конструктор (SQL builder) ---
    "bld.doc_title": {"ru": "Конструктор SQL — AI Bridge", "en": "SQL builder — AI Bridge", "de": "SQL-Konstruktor — AI Bridge"},
    "bld.title": {"ru": "Конструктор SQL", "en": "SQL builder", "de": "SQL-Konstruktor"},
    "bld.db": {"ru": "База:", "en": "Database:", "de": "Datenbank:"},
    "bld.no_bases": {"ru": "нет баз", "en": "no databases", "de": "keine Datenbanken"},
    "bld.hints": {
        "ru": "ПКМ или двойной клик — новая нода · Ctrl+колесо — масштаб · пробел+тяни — панорама",
        "en": "right-click or double-click — new node · Ctrl+wheel — zoom · space+drag — pan",
        "de": "Rechtsklick oder Doppelklick — neue Node · Strg+Rad — Zoom · Leertaste+Ziehen — verschieben",
    },
    "bld.export": {"ru": "⤓ экспорт workflow", "en": "⤓ export workflow", "de": "⤓ Workflow exportieren"},
    "bld.import": {"ru": "⤒ импорт workflow", "en": "⤒ import workflow", "de": "⤒ Workflow importieren"},
    "bld.canvas_hint": {
        "ru": "тяни от зелёного выхода ко входу · ПКМ — меню ноды",
        "en": "drag from the green output to an input · right-click — node menu",
        "de": "vom grünen Output zum Input ziehen · Rechtsklick — Node-Menü",
    },
    "bld.script": {"ru": "Скрипт", "en": "Script", "de": "Skript"},
    "bld.run": {"ru": "▶ выполнить", "en": "▶ run", "de": "▶ ausführen"},
    "bld.clear": {"ru": "очистить", "en": "clear", "de": "löschen"},
    "bld.script_hint": {
        "ru": "скрипт собирается из нод; ручные правки живут до следующего изменения графа",
        "en": "the script is built from nodes; manual edits last until the graph changes",
        "de": "das Skript entsteht aus Nodes; manuelle Änderungen gelten bis zum nächsten Graph-Wechsel",
    },
    "bld.cmd_card": {"ru": "Команда", "en": "Command", "de": "Befehl"},
    "bld.cmd_hint": {
        "ru": "выбрана в меню ⌘ у ноды",
        "en": "picked from the ⌘ node menu",
        "de": "aus dem ⌘-Node-Menü gewählt",
    },
    "bld.ai": {"ru": "AI", "en": "AI", "de": "KI"},
    "bld.ai_prompt": {
        "ru": "промпт — не может быть пустым",
        "en": "prompt — must not be empty",
        "de": "Prompt — darf nicht leer sein",
    },
    "bld.ai_explain": {"ru": "объяснить ошибку", "en": "explain the error", "de": "Fehler erklären"},
    "bld.ai_fix": {"ru": "исправить ошибку", "en": "fix the error", "de": "Fehler korrigieren"},
    "bld.ai_gen": {
        "ru": "сгенерировать код по промпту",
        "en": "generate code from prompt",
        "de": "Code aus Prompt erzeugen",
    },
    "bld.ai_gen_node": {"ru": "сгенерировать код", "en": "generate code", "de": "Code erzeugen"},
    "bld.result": {"ru": "Результат", "en": "Result", "de": "Ergebnis"},
    "bld.zoom_out": {"ru": "уменьшить", "en": "zoom out", "de": "verkleinern"},
    "bld.zoom_in": {"ru": "увеличить", "en": "zoom in", "de": "vergrößern"},
    "bld.zoom_fit": {"ru": "вместить всё", "en": "fit everything", "de": "alles einpassen"},
    "bld.auto": {"ru": "авто: ", "en": "auto: ", "de": "auto: "},
    "bld.new_value": {"ru": "новое", "en": "new", "de": "neu"},
    "bld.node_ai_title": {"ru": "AI по этой таблице", "en": "AI for this table", "de": "KI für diese Tabelle"},
    "bld.cmd_btn_title": {"ru": "команды SQL", "en": "SQL commands", "de": "SQL-Befehle"},
    "bld.sample_row": {"ru": "первая строка-пример", "en": "first sample row", "de": "erste Beispielzeile"},
    "bld.by_default": {"ru": "по умолчанию", "en": "by default", "de": "Standard"},
    "bld.no_nodes_field": {
        "ru": "нет поля nodes — не workflow",
        "en": "no nodes field — not a workflow",
        "de": "Feld nodes fehlt — kein Workflow",
    },
    # типы нод
    "bld.type.table": {"ru": "Таблица", "en": "Table", "de": "Tabelle"},
    "bld.type.output": {"ru": "Результат", "en": "Result", "de": "Ergebnis"},
    "bld.cat.sources": {"ru": "Источники", "en": "Sources", "de": "Quellen"},
    "bld.cat.selection": {"ru": "Выборка", "en": "Selection", "de": "Auswahl"},
    "bld.cat.joins": {"ru": "Соединения", "en": "Joins", "de": "Joins"},
    "bld.cat.output": {"ru": "Вывод", "en": "Output", "de": "Ausgabe"},
    "bld.hint.table": {
        "ru": "источник: клик по столбцу вставляет его имя в скрипт",
        "en": "source: clicking a column inserts its name into the script",
        "de": "Quelle: Klick auf eine Spalte fügt ihren Namen ins Skript ein",
    },
    "bld.hint.select": {"ru": "какие столбцы вернуть", "en": "which columns to return", "de": "welche Spalten zurückgeben"},
    "bld.hint.filter": {"ru": "фильтр строк", "en": "row filter", "de": "Zeilenfilter"},
    "bld.hint.group": {"ru": "группировка + агрегат", "en": "grouping + aggregate", "de": "Gruppierung + Aggregat"},
    "bld.hint.sort": {"ru": "сортировка строк", "en": "row sorting", "de": "Zeilensortierung"},
    "bld.hint.limit": {"ru": "сколько строк вернуть", "en": "how many rows to return", "de": "wie viele Zeilen zurückgeben"},
    "bld.hint.join": {
        "ru": "два источника соединяются по ключу",
        "en": "two sources joined by a key",
        "de": "zwei Quellen über einen Schlüssel verbinden",
    },
    "bld.hint.output": {
        "ru": "отсюда собирается финальный SQL",
        "en": "the final SQL is built from here",
        "de": "das finale SQL wird hier gebaut",
    },
    # подписи виджетов
    "bld.w.table": {"ru": "таблица", "en": "table", "de": "Tabelle"},
    "bld.w.cols": {"ru": "столбцы", "en": "columns", "de": "Spalten"},
    "bld.w.cond": {"ru": "условие", "en": "condition", "de": "Bedingung"},
    "bld.w.by": {"ru": "группировка", "en": "group by", "de": "gruppieren nach"},
    "bld.w.agg": {"ru": "агрегат", "en": "aggregate", "de": "Aggregat"},
    "bld.w.col": {"ru": "столбец", "en": "column", "de": "Spalte"},
    "bld.w.dir": {"ru": "порядок", "en": "order", "de": "Reihenfolge"},
    "bld.w.n": {"ru": "строк", "en": "rows", "de": "Zeilen"},
    "bld.w.how": {"ru": "тип", "en": "type", "de": "Typ"},
    "bld.w.on": {"ru": "условие", "en": "on", "de": "Bedingung"},
    # меню
    "bld.search": {"ru": "поиск ноды…", "en": "search node…", "de": "Node suchen…"},
    "bld.not_found": {"ru": "ничего не найдено", "en": "nothing found", "de": "nichts gefunden"},
    "bld.clone": {"ru": "Клонировать", "en": "Clone", "de": "Klonieren"},
    "bld.collapse": {"ru": "Свернуть", "en": "Collapse", "de": "Einklappen"},
    "bld.expand": {"ru": "Развернуть", "en": "Expand", "de": "Ausklappen"},
    "bld.insert_select": {"ru": "SELECT * FROM …", "en": "SELECT * FROM …", "de": "SELECT * FROM …"},
    "bld.insert_hint": {"ru": "вставить в скрипт", "en": "insert into script", "de": "ins Skript einfügen"},
    "bld.ai_node": {"ru": "AI: объяснить ошибку", "en": "AI: explain the error", "de": "AI: Fehler erklären"},
    "bld.color": {"ru": "цвет ноды", "en": "node colour", "de": "Node-Farbe"},
    "bld.delete": {"ru": "Удалить", "en": "Delete", "de": "Löschen"},
    "bld.collapse_title": {"ru": "свернуть/развернуть", "en": "collapse/expand", "de": "einklappen/ausklappen"},
    "bld.in": {"ru": "вход: ", "en": "input: ", "de": "Input: "},
    "bld.out": {"ru": "выход: ", "en": "output: ", "de": "Output: "},
    "bld.result_note": {
        "ru": "запрос отсюда уходит в скрипт",
        "en": "the query goes to the script from here",
        "de": "die Abfrage geht von hier ins Skript",
    },
    "bld.select_cols": {"ru": "SELECT столбцы…", "en": "SELECT columns…", "de": "SELECT Spalten…"},
    # сообщения
    "bld.exported": {
        "ru": "workflow сохранён: {n} нод, {l} линий",
        "en": "workflow saved: {n} nodes, {l} links",
        "de": "Workflow gespeichert: {n} Nodes, {l} Links",
    },
    "bld.imported": {
        "ru": "workflow загружен: {n} нод",
        "en": "workflow loaded: {n} nodes",
        "de": "Workflow geladen: {n} Nodes",
    },
    "bld.import_fail": {
        "ru": "импорт не удался: {e}",
        "en": "import failed: {e}",
        "de": "Import fehlgeschlagen: {e}",
    },
    "bld.ai_empty": {
        "ru": "Промпт не может быть пустым — напишите вопрос для AI.",
        "en": "The prompt cannot be empty — write your question for the AI.",
        "de": "Der Prompt darf nicht leer sein — schreiben Sie Ihre Frage an die KI.",
    },
    "bld.ai_thinking": {"ru": "AI думает…", "en": "AI is thinking…", "de": "Die KI denkt…"},
    "bld.ai_no_answer": {"ru": "пустой ответ", "en": "empty answer", "de": "leere Antwort"},
    "bld.ai_conn": {"ru": "Ошибка связи: {e}", "en": "Connection error: {e}", "de": "Verbindungsfehler: {e}"},
    "bld.net_error": {"ru": "Ошибка сети: {e}", "en": "Network error: {e}", "de": "Netzwerkfehler: {e}"},
    "bld.no_results": {"ru": "нет результатов", "en": "no results", "de": "keine Ergebnisse"},
    # карточка команды
    "bld.example_of": {"ru": "Пример использования · {t}", "en": "Example usage · {t}", "de": "Beispiel · {t}"},
    "bld.before": {"ru": "до — таблица {t}", "en": "before — table {t}", "de": "vorher — Tabelle {t}"},
    "bld.after": {"ru": "после — результат команды", "en": "after — command result", "de": "nachher — Ergebnis des Befehls"},
    "bld.no_compare": {
        "ru": "для этой команды сравнение не показывается",
        "en": "no before/after shown for this command",
        "de": "für diesen Befehl wird kein Vorher/Nachher gezeigt",
    },
    "bld.no_rows": {"ru": "строк нет", "en": "no rows", "de": "keine Zeilen"},
    # описания команд
    "bld.d0": {"ru": "Возвращает все столбцы и все строки таблицы.", "en": "Returns all columns and all rows of the table.", "de": "Liefert alle Spalten und alle Zeilen der Tabelle."},
    "bld.d1": {"ru": "Оставляет только нужные столбцы (проекция) — остальное отбрасывается.", "en": "Keeps only the chosen columns (projection) — the rest is dropped.", "de": "Behält nur die gewählten Spalten (Projektion) — der Rest entfällt."},
    "bld.d2": {"ru": "Убирает повторы: остаются только разные значения.", "en": "Removes duplicates: only distinct values remain.", "de": "Entfernt Duplikate: nur verschiedene Werte bleiben."},
    "bld.d3": {"ru": "Соединяет таблицу с другой по общей колонке: связь 1:N.", "en": "Joins the table with another one by a shared column: 1:N relation.", "de": "Verbindet die Tabelle über eine gemeinsame Spalte: 1:N-Beziehung."},
    "bld.d4": {"ru": "Фильтрует строки: остаются только те, где колонка равна значению.", "en": "Filters rows: keeps only those where the column equals a value.", "de": "Filtert Zeilen: behält nur jene, bei denen die Spalte gleich einem Wert ist."},
    "bld.d5": {"ru": "Несколько условий через AND/OR: строка проходит, если выполнены все.", "en": "Several conditions with AND/OR: a row passes when all hold.", "de": "Mehrere Bedingungen mit AND/OR: eine Zeile besteht, wenn alle gelten."},
    "bld.d6": {"ru": "Сортирует строки по колонке (ASC по умолчанию, DESC — по убыванию).", "en": "Sorts rows by a column (ASC by default, DESC for descending).", "de": "Sortiert Zeilen nach einer Spalte (standardmäßig ASC, DESC absteigend)."},
    "bld.d7": {"ru": "Складывает строки в группы с одинаковым значением колонки.", "en": "Collects rows into groups with the same column value.", "de": "Bündelt Zeilen zu Gruppen mit gleichem Spaltenwert."},
    "bld.d8": {"ru": "Фильтр уже по группам (WHERE — по строкам, HAVING — по агрегатам).", "en": "Filters groups (WHERE works on rows, HAVING on aggregates).", "de": "Filtert Gruppen (WHERE filtert Zeilen, HAVING Aggregate)."},
    "bld.d9": {"ru": "Обрезает результат: возвращается только первых n строк.", "en": "Cuts the result: only the first n rows are returned.", "de": "Kürzt das Ergebnis: nur die ersten n Zeilen werden geliefert."},
    "bld.d10": {"ru": "Считает количество строк (всегда одна итоговая строка).", "en": "Counts rows (always a single summary row).", "de": "Zählt Zeilen (immer eine einzige Ergebniszeile)."},
    "bld.d11": {"ru": "Считает, сколько разных значений встречается в колонке.", "en": "Counts how many different values appear in the column.", "de": "Zählt, wie viele verschiedene Werte in der Spalte vorkommen."},
    "bld.d12": {"ru": "Максимальное и минимальное значение колонки.", "en": "Maximum and minimum value of the column.", "de": "Maximal- und Minimalwert der Spalte."},
    "bld.d13": {"ru": "Среднее и сумма по числовой колонке.", "en": "Average and sum over a numeric column.", "de": "Mittelwert und Summe über eine numerische Spalte."},
    "bld.d14": {"ru": "Добавляет новую строку: перечислите колонки и их значения.", "en": "Adds a new row: list the columns and their values.", "de": "Fügt eine neue Zeile hinzu: Spalten und Werte angeben."},
    "bld.d15": {"ru": "Меняет значения в существующих строках (обычно с условием WHERE).", "en": "Changes values in existing rows (usually with a WHERE condition).", "de": "Ändert Werte in bestehenden Zeilen (meist mit WHERE-Bedingung)."},
    "bld.d16": {"ru": "Удаляет строки; без WHERE удаляет всю таблицу (осторожно!).", "en": "Deletes rows; without WHERE it drops the whole table (careful!).", "de": "Löscht Zeilen; ohne WHERE die ganze Tabelle (Vorsicht!)."},
    "bld.d17": {"ru": "Классика аналитики: сколько строк в каждой группе.", "en": "Analytics classic: how many rows are in each group.", "de": "Analyse-Klassiker: wie viele Zeilen hat jede Gruppe."},
    # примечания «до/после»
    "bld.note.distinct": {"ru": "было {a} строк → стало {b}", "en": "was {a} rows → now {b}", "de": "vorher {a} Zeilen → jetzt {b}"},
    "bld.note.join_none": {"ru": "в примере нет строк с совпадающим ключом", "en": "no rows with a matching key in the sample", "de": "im Beispiel keine Zeile mit passendem Schlüssel"},
    "bld.note.where_eq": {"ru": "условие: {c} = '{v}'", "en": "condition: {c} = '{v}'", "de": "Bedingung: {c} = '{v}'"},
    "bld.note.where_gt": {"ru": "убраны пустые значения {c}", "en": "empty values of {c} removed", "de": "leere Werte von {c} entfernt"},
    "bld.note.order": {"ru": "сортировка по {c} ↑", "en": "sorted by {c} ↑", "de": "sortiert nach {c} ↑"},
    "bld.note.having": {
        "ru": "ни одна группа не проходит COUNT(*) > 1 — в примере все значения разные",
        "en": "no group passes COUNT(*) > 1 — all sample values are distinct",
        "de": "keine Gruppe besteht COUNT(*) > 1 — alle Beispielwerte sind verschieden",
    },
    "bld.note.limit": {"ru": "было {n} строк → оставлены первые {k}", "en": "was {n} rows → kept the first {k}", "de": "vorher {n} Zeilen → erste {k} behalten"},
    "bld.note.maxmin": {"ru": "по колонке {c}", "en": "on column {c}", "de": "auf Spalte {c}"},
    "bld.note.avgsum": {"ru": "по числовой колонке {c}", "en": "on numeric column {c}", "de": "auf numerischer Spalte {c}"},
    "bld.note.insert": {
        "ru": "строка добавлена в конец (VALUES нужно заполнить своими значениями)",
        "en": "row appended (fill VALUES with your own values)",
        "de": "Zeile angehängt (VALUES mit eigenen Werten füllen)",
    },
    "bld.note.update": {"ru": "первая строка: {c} → «{v}»", "en": "first row: {c} → '{v}'", "de": "erste Zeile: {c} → '{v}'"},
    "bld.note.delete": {"ru": "первая строка удалена", "en": "first row removed", "de": "erste Zeile entfernt"},
    # --- общие ---
    "common.save": {"ru": "сохранить", "en": "save", "de": "speichern"},
    "common.cancel": {"ru": "отмена", "en": "cancel", "de": "abbrechen"},
    "common.back": {"ru": "назад", "en": "back", "de": "zurück"},
    "common.yes": {"ru": "да", "en": "yes", "de": "ja"},
    "common.no": {"ru": "нет", "en": "no", "de": "nein"},
    "common.on": {"ru": "вкл", "en": "on", "de": "an"},
    "common.off": {"ru": "выкл", "en": "off", "de": "aus"},
    "common.loading": {"ru": "загрузка…", "en": "loading…", "de": "lädt…"},
    "common.error": {"ru": "ошибка", "en": "error", "de": "Fehler"},
    "common.copy": {"ru": "копировать", "en": "copy", "de": "kopieren"},
    "common.copied": {"ru": "скопировано", "en": "copied", "de": "kopiert"},
}


def t(key: str, lang: str | None = None) -> str:
    """Вернуть строку ``key`` на текущем языке (или указанном ``lang``).

    Если ключа нет — возвращаем сам ключ, чтобы ничего не падало.
    """
    if lang is None:
        lang = _current_lang.get()
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    return entry.get(lang) or entry.get("ru") or key


def strings_for(prefixes: tuple[str, ...]) -> dict[str, str]:
    """Словарь ``{ключ: строка на текущем языке}`` для данных префиксов.

    Нужен, чтобы отдать переводы в JavaScript страницы: там сообщения
    собираются динамически (меню, подсказки, примеры команд).
    """
    lang = _current_lang.get()
    return {
        key: (entry.get(lang) or entry.get("ru") or key)
        for key, entry in _STRINGS.items()
        if key.startswith(prefixes)
    }
