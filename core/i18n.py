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
    "sql.run": {"ru": "выполнить SELECT", "en": "run SELECT", "de": "SELECT ausführen"},
    "sql.schema": {"ru": "Схема базы", "en": "Database schema", "de": "Datenbankschema"},
    "sql.relations": {"ru": "Связи коллекций", "en": "Collection relations", "de": "Sammlungsbeziehungen"},
    "sql.no_relations": {"ru": "Связи не обнаружены.", "en": "No relations found.", "de": "Keine Beziehungen gefunden."},
    "sql.result": {"ru": "Результат запроса", "en": "Query result", "de": "Abfrageergebnis"},
    "sql.full_access": {"ru": "Полный доступ: схема PocketBase и SQL-запросы.", "en": "Full access: PocketBase schema and SQL queries.", "de": "Vollzugriff: PocketBase-Schema und SQL-Abfragen."},
    "sql.read_only": {"ru": "Схема PocketBase и SQL-запросы только для чтения.", "en": "PocketBase schema and SQL queries, read-only.", "de": "PocketBase-Schema und SQL-Abfragen, nur lesen."},
    "sql.admin_hint": {"ru": "Разрешены SELECT, WITH, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, REPLACE, PRAGMA.", "en": "Allowed: SELECT, WITH, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, REPLACE, PRAGMA.", "de": "Erlaubt: SELECT, WITH, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, REPLACE, PRAGMA."},
    "sql.user_hint": {"ru": "Разрешены SELECT и WITH.", "en": "SELECT and WITH only.", "de": "Nur SELECT und WITH."},
    "sql.time_limit": {"ru": "Запрос ограничен по времени и объёму ответа.", "en": "Query is limited by time and result size.", "de": "Abfrage ist durch Zeit und Ergebnisgröße begrenzt."},
    "sql.not_configured": {"ru": "Задайте AI_BRIDGE_SQLITE_PATH в окружении службы, указав путь к data.db PocketBase.", "en": "Set AI_BRIDGE_SQLITE_PATH in the service environment to the path of PocketBase's data.db.", "de": "Setzen Sie AI_BRIDGE_SQLITE_PATH in der Service-Umgebung auf den Pfad von PocketBases data.db."},
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
