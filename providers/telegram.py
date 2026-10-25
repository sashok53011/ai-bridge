"""Уведомления в Telegram: обычные HTTPS-запросы к api.telegram.org.

Бот — самый дешёвый канал уведомлений: на сервере не крутится ничего,
только два вызова. Токен задаёт администратор (хранится в ``notify.json``,
на страницу не выводится — как ключи провайдеров), а chat_id у каждого
свой: человек пишет боту сообщение и жмёт «узнать chat_id» — приставка
сама прочитает ``getUpdates`` и запомнит ответ.

Все функции честно возвращают причину отказа (или ``None`` — успех):
уведомление не должно ронять отправку письма, из которого оно выросло.
"""

from __future__ import annotations

import httpx

#: Сколько ждём ответа Telegram: уведомление — не повод тормозить почту.
TIMEOUT = 10.0


def _call(method: str, *, token: str, params: dict | None = None,
          transport: httpx.BaseTransport | None = None) -> dict:
    """Один вызов Telegram API. Ошибки сети и API — ``{"error": ...}``."""
    if not token:
        return {"error": "токен бота не задан"}
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        # transport — только для тестов: в жизни обычный https.
        with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
            response = client.get(url, params=params or {})
    except httpx.HTTPError as exc:
        return {"error": f"нет связи с Telegram: {exc}"}
    try:
        body = response.json()
    except ValueError:
        return {"error": f"Telegram ответил не JSON (HTTP {response.status_code})"}
    if not body.get("ok"):
        description = str(body.get("description") or f"HTTP {response.status_code}")
        return {"error": f"Telegram отказал: {description}"}
    return {"result": body.get("result")}


def send(token: str, chat_id: str, text: str, *,
         transport: httpx.BaseTransport | None = None) -> str | None:
    """Отправить сообщение в чат. ``None`` — ушло, иначе — причина отказа."""
    if not str(chat_id or "").strip():
        return "chat_id не задан"
    outcome = _call(
        "sendMessage",
        token=token,
        params={"chat_id": str(chat_id), "text": str(text or "")},
        transport=transport,
    )
    return outcome.get("error")


def last_chat_id(token: str, *,
                 transport: httpx.BaseTransport | None = None) -> tuple[str | None, str | None]:
    """Чей-то chat_id из последнего входящего сообщения боту.

    ``(chat_id, None)`` или ``(None, причина)``. Работает без вебхука:
    человек пишет боту, приставка читает ``getUpdates`` — и готово.
    """
    outcome = _call("getUpdates", token=token, params={"timeout": 0},
                    transport=transport)
    if outcome.get("error"):
        return None, str(outcome["error"])
    updates = outcome.get("result") or []
    best: tuple[int, str] | None = None
    for item in updates:
        message = (item or {}).get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            continue
        stamp = int(item.get("update_id") or 0)
        if best is None or stamp > best[0]:
            best = (stamp, chat_id)
    if best is None:
        return None, "боту ещё не писали — отправьте ему любое сообщение и повторите"
    return best[1], None


def hello(token: str, *,
          transport: httpx.BaseTransport | None = None) -> tuple[str | None, str | None]:
    """Проверка бота по getMe: ``(@имя, None)`` или ``(None, причина)``."""
    outcome = _call("getMe", token=token, transport=transport)
    if outcome.get("error"):
        return None, str(outcome["error"])
    username = str((outcome.get("result") or {}).get("username") or "").strip()
    return ("@" + username if username else "бот"), None
