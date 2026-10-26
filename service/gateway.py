"""Локальный HTTP-сервис: AI-приставка в формате OpenAI.

Другие приложения настаивают на привычном OpenAI-совместимом адресе и не
знают о провайдерах ничего: они шлют запрос сюда, а приставка сама выбирает
включённого провайдера, подставляет свой ключ и, если провайдер отказал,
пробует следующего по приоритету.

    GET  /v1/models             -> список моделей включённых провайдеров
    POST /v1/chat/completions   -> запрос к провайдеру (с фолбэком)
    GET  /                      -> короткая справка

Слушает по умолчанию 127.0.0.1 — наружу торчать нечему: ключи остаются
на устройстве и уходят только к провайдеру.
"""

from __future__ import annotations

import hmac
import json
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from core.models import union_models
from core.routes import advertised_models, is_local_url, request_needs, route, should_fallback
from providers import copilot
from providers.client import NeedsKeyError, Provider, ServerError
from storage.lists import GREY_CYCLE_LIMIT, ModelLists
from storage.rules import ACTION_CAPABILITY, LEARN_ACTIONS, describe, match_response

#: Пустой ответ на пустой список: клиенты ждут JSON, а не пустоту.
_NO_MODELS = {"object": "list", "data": []}

#: Как часто обновляются live-списки моделей (секунды).
_MODELS_TTL = 600.0

#: Таймаут вытягивания списка моделей у одного провайдера.
_MODELS_FETCH_TIMEOUT = 8.0

#: Таймаут чата для локальных провайдеров (секунды): холодная загрузка
#: модели в память Ollama/LM Studio — до пяти минут, ждём с запасом.
LOCAL_TIMEOUT = 600.0

#: Вопрос тестового чата: крошечный, но настоящий.
DEFAULT_QUESTION = "Привет! Ответь одним словом: всё ли работает?"


def _now_iso() -> str:
    """Текущее время в ISO (UTC) — для отметок в результатах тестов."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


#: Типы автотестов, которым нужны «свои» модели из каталога.
_MEDIA_TEST_TYPES = frozenset({"image", "tts", "stt", "video", "3d", "embedding"})

#: Свой эндпоинт у каждого такого типа (Pollinations — OpenAI-совместимые
#: картинки/звук/векторы; видео и 3D живут на корневых GET-путях).
_MEDIA_ENDPOINTS = {
    "image": "/v1/images/generations",
    "tts": "/v1/audio/speech",
    "stt": "/v1/audio/transcriptions",
    "embedding": "/v1/embeddings",
}

#: Тип каталога для тестов, у которых нет своего OpenAI-эндпоинта.
_MEDIA_CATEGORIES = {"image": "image", "video": "video", "3d": "3d",
                     "embedding": "embedding"}

#: Запасной выбор по именам — когда провайдер не отдаёт типы моделей
#: (обычный OpenAI-список, где category и supported_endpoints нет).
_MEDIA_NAME_HINTS = {
    "image": ("dall-e", "gpt-image", "flux", "stable-diffusion", "sdxl", "sd3",
              "imagen", "midjourney", "krea", "ideogram", "seedream",
              "qwen-image", "z-image", "recraft", "lightning-image"),
    "tts": ("tts", "speech", "eleven", "kokoro"),
    "stt": ("whisper", "transcribe", "scribe"),
    "video": ("sora", "veo", "seedance", "kling", "runway", "hailuo"),
    "embedding": ("embedding",),
    "3d": ("trellis", "rodin", "asset-harvester"),
}


def _meta_fits(meta: dict, test_type: str) -> bool:
    """Подходит ли модель каталога под тип теста.

    Тип каталога главнее эндпоинтов: video/3d-модели рекламируют и
    image-эндпоинты, иначе их гоняло бы и по картинкам. Звук — наоборот:
    tts и stt различаются только своим эндпоинтом.
    """
    category = str(meta.get("category") or "")
    endpoints = meta.get("endpoints") or []
    if category:
        wanted = _MEDIA_CATEGORIES.get(test_type)
        if wanted:
            return category == wanted
        endpoint = _MEDIA_ENDPOINTS.get(test_type, "")
        return bool(endpoint) and endpoint in endpoints
    endpoint = _MEDIA_ENDPOINTS.get(test_type, "")
    return bool(endpoint) and endpoint in endpoints


def _models_for_test(test_type: str, models: list[str], meta: dict) -> list[str]:
    """Модели одного типа теста: метаданные каталога, иначе — имена."""
    if test_type not in _MEDIA_TEST_TYPES:
        return models
    if meta:
        return [m for m in models if _meta_fits(meta.get(m, {}), test_type)]
    hints = tuple(hint.lower() for hint in _MEDIA_NAME_HINTS.get(test_type, ()))
    return [m for m in models if any(hint in m.lower() for hint in hints)]


def _enclosure(link_header: str) -> str:
    """Публичная ссылка из заголовка ``Link: <…>; rel="enclosure"``.

    Pollinations кладёт её на ответы генерации — по ней файл и показывается
    (само тело — мегабайты, их мы в журнал не пишем).
    """
    for part in link_header.split(","):
        piece = part.strip()
        if piece.startswith("<") and 'rel="enclosure"' in piece:
            end = piece.find(">")
            if end > 1:
                return piece[1:end]
    return ""


def _read_capped(response: Any, limit: int) -> bytes:
    """Читает поток не больше limit байт — большие медиа не тянем в память."""
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= limit:
                break
    except Exception:  # noqa: BLE001 — оборванный поток: прочитанное есть
        pass
    return b"".join(chunks)


def _media_outcome(test_type: str, raw: bytes, ctype: str, link: str,
                   resp_text: str) -> tuple[str, str, str]:
    """Успех медиа-ответа: (статус, что показать, что сохранить в журнал).

    Картинки и звук провайдер отдаёт ссылкой (base64 — не храним): в
    autotest.json должны остаться строки, а не мегабайты байтов.
    """
    def first_item() -> dict:
        try:
            data = json.loads(resp_text) if resp_text else {}
        except ValueError:
            data = {}
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0]
        return {}

    if test_type == "image":
        item = first_item()
        url = str(item.get("url") or "").strip()
        if url:
            return "ok", url, resp_text
        b64 = str(item.get("b64_json") or "")
        if b64:
            note = f"b64_json — {len(b64)} знаков, тело не сохранено"
            return "ok", note, note
        return "fail", "в ответе нет картинки (data пусто)", resp_text[:400]

    if test_type == "embedding":
        vector = first_item().get("embedding")
        if isinstance(vector, list) and vector:
            return "ok", f"вектор из {len(vector)} чисел", resp_text
        return "fail", "в ответе нет вектора", resp_text[:400]

    if test_type == "tts":
        public = _enclosure(link)
        if public:
            return "ok", public, public
        if raw:
            kilobytes = max(1, (len(raw) + 1023) // 1024)
            note = f"{ctype or 'audio'}, {kilobytes} КБ"
            return "ok", note, note
        return "fail", "пустой звуковой ответ", ""

    if test_type == "stt":
        try:
            data = json.loads(resp_text) if resp_text else {}
        except ValueError:
            data = {}
        if isinstance(data, dict) and "text" in data:
            text = str(data.get("text") or "").strip()
            return "ok", text or "распознано: пусто", resp_text
        return "fail", "в ответе нет текста (нет поля text)", resp_text[:400]

    # video / 3d: тело большое — показываем публичную ссылку из заголовка,
    # а если её нет, честно говорим, что показывать нечего.
    public = _enclosure(link)
    if public:
        return "ok", public, public
    if raw:
        return "ok", f"{ctype or 'файл'} — ссылка не пришла, показать нечего", ""
    return "fail", "пустой ответ генерации", ""


def _reaction(status: int, test_type: str, record: bool,
              *, capability: str = "", used_tool: bool = False) -> str:
    """Что сделал бы (или сделал) роутер с таким ответом провайдера."""
    prefix = "сработала" if record else "сработала бы"
    if status == 0:
        return f"{prefix}: чёрный список (сеть/таймаут)"
    if status >= 400:
        if status == 402:
            if test_type == "free":
                return "сработала: в список «доказано платные»"
            return f"{prefix}: серый список на сутки"
        if status in (401, 403):
            return f"{prefix}: списки не тронуты (дело в ключе)"
        if status == 404 or status >= 500:
            return f"{prefix}: чёрный список"
        if status == 400:
            if capability:
                word = "картинки" if capability == "vision" else "инструменты"
                return f"{prefix}: выучить «не умеет {word}»"
            return f"{prefix}: запрос отклонён провайдером, списки не тронуты"
        return f"{prefix}: списки не тронуты (ошибка запроса)"
    if test_type in _MEDIA_TEST_TYPES:
        # Генерация — не способность маршрутизации: списки не трогаем.
        return f"{prefix}: списки не тронуты (генерация — не способность)"
    if test_type == "vision":
        base = "белый список + выучить «умеет картинки»"
    elif test_type == "tools":
        base = "белый список + выучить «умеет инструменты»"
    else:
        base = "белый список (подтверждена)"
    if test_type == "tools" and used_tool:
        base += "; инструмент вызван"
    return f"{prefix}: {base}"


def _hidden_error(payload: bytes) -> str:
    """Скрытая ошибка внутри 200-ответа: пустая строка — ответ здоров.

    Часть шлюзов отвечает HTTP 200, а кладёт в JSON объект ошибки
    ({"error": {...}} или {"type": "error"} без choices) — для прокси
    это отказ, а не чат: такого провайдера надо переключать.
    """
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(data, dict) or "choices" in data:
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        detail = str(error.get("message") or error.get("type") or error)
    elif isinstance(error, str):
        detail = error
    elif data.get("type") == "error" or data.get("object") == "error":
        detail = str(data.get("message") or "провайдер сообщил об ошибке")
    else:
        return ""
    return f"скрытая ошибка в ответе 200: {detail[:200]}"


def _garbage_answer(payload: bytes, content_type: str) -> bool:
    """Обещал JSON (content-type) — а отдал мусор или пустоту.

    Узкая эвристика для репутации: шлюз сообщает «application/json»,
    но тело не разбирается (или пустое) — ответ непригоден, это поломка
    модели, а не запроса. Не-JSON content-type молча пропускаем: у
    провайдера может быть своя форма, её отдаём клиенту как есть.
    """
    if "json" not in (content_type or "").lower():
        return False
    if not payload.strip():
        return True
    try:
        json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return True
    return False


def _needs_of(body: bytes, rules=None) -> tuple[str, ...]:
    """Что требует запрос (vision/tools) из его тела; не-JSON — пусто.

    Встроенные признаки OpenAI-формата (image_url / tools) плюс строки
    из правил запроса админа (storage.rules): так админ может научить
    приставку новым маркерам картинок или инструментов. Правило ищет
    строку и в сыром теле, и в значениях полей — кириллица в JSON часто
    экранирована (\\u0434...), и по сырому тексту её не найти.
    """
    needs: list[str] = []
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        data = None
    if isinstance(data, dict):
        needs.extend(request_needs(data))
    if rules is not None:
        try:
            raw = body.decode("utf-8", errors="replace").lower()
        except Exception:  # noqa: BLE001
            raw = ""
        haystack = raw + " " + " ".join(_all_strings(data)).lower() if raw else ""
        for rule in rules.request_rules():
            needle = str(rule.get("text") or "").strip().lower()
            action = str(rule.get("action") or "")
            capability = ACTION_CAPABILITY.get(action, "")
            if needle and capability and needle in haystack and capability not in needs:
                needs.append(capability)
    return tuple(needs)


def _all_strings(value: object) -> list[str]:
    """Все строки из разобранного JSON (значения полей, куски сообщений)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _all_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _all_strings(item)]
    return []


def _merge_capabilities(configured: object, known: dict) -> str:
    """Текст «Способностей» с учётом выученных фактов.

    Умеет — дописывает слово; не умеет — вычёркивает его из текста.
    Порядок слов из справки сохраняется, выученное добавляется в конец.
    """
    words = [
        word.strip().lower()
        for word in str(configured or "").split(",")
        if word.strip()
    ]
    for capability, ok in known.items():
        if ok and capability not in words:
            words.append(capability)
        elif not ok and capability in words:
            words = [word for word in words if word != capability]
    return ", ".join(words)


def _usage_from(payload: bytes) -> dict:
    """Сколько токенов съел запрос (поле usage в ответе); пусто — не было."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    usage = data.get("usage") if isinstance(data, dict) else None
    return usage if isinstance(usage, dict) else {}


def _absorb_usage(state: dict, line: str) -> None:
    """Находит usage в строке SSE-потока и запоминает последний."""
    line = line.strip()
    if not line.startswith("data:"):
        return
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return
    try:
        data = json.loads(payload)
    except ValueError:
        return
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict):
        state["usage"] = usage


#: Понятные фразы отказа из-за невозможностей модели (400): не умеет
#: картинки/инструменты — или вообще несовместимый канал (live-модели
#: Gemini отвечают только через WebSocket). Каждой фразе соответствует
#: способность, которой она касается: пусто — не про способности
#: (канал WebSocket не даёт права учить vision/tools).
_CAPABILITY_HINTS = (
    ("image_url is not supported", "vision"),
    ("images are not supported", "vision"),
    ("does not support image", "vision"),
    ("no image input", "vision"),
    ("does not support tool", "tools"),
    ("does not support function", "tools"),
    ("tool use is not supported", "tools"),
    ("no tools support", "tools"),
    ("only supports real-time bidirectional streaming", ""),
    ("bidi generatecontent", ""),
    ("bidigeneratecontent", ""),
)

#: Широкий путь (когда точная фраза не совпала): слова-отрицания и слова
#: про способности. Провайдеры формулируют отказ по-разному (Cohere:
#: «image content is not supported»), и точный список всегда отстаёт.
#: Привязка к needs запроса защищает от ложных срабатываний на
#: по-настоящему кривых запросах («invalid temperature» — не про vision).
_NEGATIONS = (
    "not supported", "unsupported", "does not support", "do not support",
    "doesn't support", "doesnt support", "does not accept", "do not accept",
    "doesn't accept", "doesnt accept", "cannot", "can't", "cant",
    "no support", "not allowed", "not permitted", "reject",
    "not recognized", "unrecognized", "not available", "unable to",
    "cannot handle", "can't handle",
)
_VISION_WORDS = ("image", "images", "vision", "picture", "photo", "multimodal", "visual")
_TOOLS_WORDS = ("tool", "tools", "function", "functions", "tool_use", "tool call")


def _capability_refusal(payload: bytes, needs: tuple[str, ...] = ()) -> tuple[str, str]:
    """(сообщение, способность) отказа модели (400); ("", "") — не отказ.

    Сначала — точные фразы (включая канал WebSocket). Если не совпало —
    широкий путь: 400 с отрицанием и упоминанием способности, о которой
    просил запрос. Так ловится Cohere («image content is not supported»)
    и подобные формулировки. Способность в ответе — чтобы приставка
    выучила: заявленное «умеет» провернулось мимо факта провайдера.
    """
    text = payload.decode("utf-8", errors="replace").lower()
    for hint, capability in _CAPABILITY_HINTS:
        if hint in text:
            return f"модель отвергла запрос ({hint})", capability
    if not any(negation in text for negation in _NEGATIONS):
        return "", ""  # нет отрицания — это не отказ модели
    if "vision" in needs and any(word in text for word in _VISION_WORDS):
        return "модель отвергла запрос (не поддерживает картинки)", "vision"
    if "tools" in needs and any(word in text for word in _TOOLS_WORDS):
        return "модель отвергла запрос (не поддерживает инструменты)", "tools"
    return "", ""


def image_models(
    entry: dict,
    key: str,
    *,
    make_provider: Callable[..., Provider] = Provider,
) -> list[dict]:
    """Image-модели провайдера для формы генерации: ``[{"name", "paid"}]``.

    Только метаданные каталога: секция «Картинка» появляется там, где
    провайдер честно заявил image-модели (Pollinations), а не у всех
    подряд; метаданных нет или каталог не отвечает — пусто, секции нет.
    """
    try:
        meta = make_provider(entry["url"], timeout=15.0).fetch_model_meta(key)
    except Exception:  # noqa: BLE001 — секция лучше исчезнет, чем задержит тест
        return []
    return [
        {"name": name, "paid": bool(meta_dict.get("paid"))}
        for name, meta_dict in sorted(meta.items())
        if _meta_fits(meta_dict, "image")
    ]


def generate_image(
    entry: dict,
    key: str,
    *,
    prompt: str,
    model: str,
    make_provider: Callable[..., Provider] = Provider,
    timeout: float = 120.0,
) -> dict:
    """Генерация картинки одним запросом — дополнительный тест страницы.

    Возвращает ``{"model", "prompt", "url", "b64", "error"}``: ссылку
    страница покажет картинкой, base64 — data-URI (разово, в журнал не
    пишется). Без промпта, модели или ключа ничего не отправляется —
    каждая нехватка честной строкой.
    """
    prompt = (prompt or "").strip()
    model = (model or "").strip()

    def fail(text: str) -> dict:
        return {"model": model, "prompt": prompt, "url": "", "b64": "", "error": text}

    if not prompt:
        return fail("промпт не задан")
    if not model:
        return fail("модель не выбрана")
    if not key:
        return fail("нужен ключ — введите его кнопкой «ключ»")

    payload = json.dumps(
        {"model": model, "prompt": prompt, "n": 1, "response_format": "url"},
        ensure_ascii=False,
    ).encode("utf-8")

    try:
        response = make_provider(entry["url"], timeout=timeout).forward_path(
            "/images/generations", payload, api_key=key, timeout=timeout, stream=False,
        )
    except NeedsKeyError:
        return fail("нужен ключ — введите его кнопкой «ключ»")
    except ServerError as exc:
        return fail(str(exc))

    if response.status_code >= 400:
        detail = response.read().decode("utf-8", errors="replace")[:300]
        return fail(f"HTTP {response.status_code}: {detail}".strip())

    try:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    except ValueError:
        return fail("провайдер ответил не-JSON на генерацию картинки")
    items = data.get("data") if isinstance(data, dict) else None
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    url = str(item.get("url") or "").strip()
    b64 = str(item.get("b64_json") or "").strip()
    if url:
        return {"model": model, "prompt": prompt, "url": url, "b64": "", "error": ""}
    if b64:
        return {"model": model, "prompt": prompt, "url": "", "b64": b64, "error": ""}
    return fail("в ответе нет картинки (data пусто)")


def test_chat(
    entry: dict,
    key: str,
    *,
    make_provider: Callable[..., Provider] = Provider,
    question: str = DEFAULT_QUESTION,
    timeout: float = 60.0,
) -> dict:
    """Небольшой тестовый чат: настоящий запрос с крошечным лимитом.

    Возвращает {"question", "answer", "error"}: проверяет не только
    список моделей, а что ключ принят и модель отвечает. На платной
    модели это доли цента — на странице об этом сказано честно.
    """
    payload = json.dumps(
        {
            "model": entry.get("model") or "",
            "messages": [{"role": "user", "content": question}],
            # Без лимита по токенам: ответ всегда полный, как у клиента.
            "temperature": 0,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    def fail(text: str) -> dict:
        return {"question": question, "answer": "", "error": text}

    if not entry.get("model"):
        return fail("у провайдера не выбрана модель — выберите её кнопкой «модель»")
    if not key:
        return fail("нужен ключ — введите его кнопкой «ключ»")

    if is_local_url(str(entry.get("url") or "")):
        # Локальная модель может грузиться в память до пяти минут.
        timeout = max(timeout, LOCAL_TIMEOUT)

    try:
        response = make_provider(entry["url"], timeout=timeout).forward_chat(
            payload, api_key=key, timeout=timeout, stream=False
        )
    except NeedsKeyError:
        return fail("нужен ключ — введите его кнопкой «ключ»")
    except ServerError as exc:
        return fail(str(exc))

    if response.status_code >= 400:
        detail = response.read().decode("utf-8", errors="replace")[:300]
        # У Copilot свой частый отказ (400 model_not_supported): каталог
        # /models шире тарифа — человеку нужна причина, а не JSON.
        if copilot.is_copilot_url(str(entry.get("url") or "")):
            human = copilot.explain_error(
                response.status_code, detail,
                retry_after=str(response.headers.get("retry-after") or ""),
            )
            if human:
                return fail(human)
        return fail(f"HTTP {response.status_code}: {detail}".strip())

    payload = response.read()
    hidden = _hidden_error(payload)
    if hidden:
        return fail(hidden)

    try:
        data = json.loads(payload.decode("utf-8"))
        message = data["choices"][0].get("message") or {}
        answer = (message.get("content") or "").strip()
        if not answer:
            # Модель с рассуждением израсходовала лимит на «размышления» —
            # покажем их, чем молча выдать пустоту.
            thinking = (
                message.get("reasoning") or message.get("reasoning_content") or ""
            ).strip()
            if thinking:
                answer = f"(модель рассуждала, итог обрезан лимитом) {thinking[:200]}"
    except Exception:  # noqa: BLE001 — чужой формат ответа не должен ронять страницу
        answer = ""
    return {"question": question, "answer": answer or "(пустой ответ)", "error": ""}


#: Как часто приставка переспрашивает реестр доступа о человеке (секунды).
#: Десятки запросов человека в окне = один поход к серверу каталога.
ACCESS_TTL = 300.0

#: Память о недоступном реестре (секунды): не долбить сервер каждый запрос.
ACCESS_DOWN = 60.0


def _error_body(message: str, *, kind: str = "invalid_request_error", code: str | None = None) -> bytes:
    """Ошибка в формате, который понимают OpenAI-клиенты."""
    payload = {"error": {"message": message, "type": kind, "param": None, "code": code}}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _with_model(body: bytes, model: str) -> bytes:
    """Подставляет отроутенную модель в тело запроса.

    Клиент мог попросить «ollama/qwen3» (выбор провайдера) или не назвать
    модель вовсе — провайдеру в обоих случаях говорят по-человечески, что
    грузить. Тело, которое не разобрать как JSON, уходит как есть:
    отклонит уже провайдер, и это честнее, чем молча переписывать.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    if payload.get("model") == model:
        return body
    payload["model"] = model
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Gateway:
    """Всё, что нужно, чтобы ответить на запрос приложения.

    ``entries_provider`` — вызываемое, отдающее свежий список провайдеров
    с переключателями (каталог + снапшот устройства). ``keys`` — хранилище
    ключей; ключ уходит только к провайдеру в его заголовке Authorization.
    ``token`` — токен самой приставки; если задан, все /v1/* маршруты
    требуют его в Authorization: Bearer. ``user_tokens`` + ``context_for_user``
    включают персональный режим: токен принадлежит человеку, и запрос
    обслуживается его переключателями и его ключами. ``provider_factory``
    подменяется в тестах.
    """

    def __init__(
        self,
        entries_provider: Callable[[], list[dict]],
        keys,  # storage.keys.KeyStore
        *,
        timeout: float = 600.0,
        provider_factory: Callable[..., Provider] = Provider,
        token: str = "",
        user_tokens=None,  # storage.usertokens.UserTokens | None
        context_for_user: Callable[[str], tuple[Callable[[], list[dict]], object] | None] | None = None,
        api_gate: Callable[[str], bool] | None = None,
        access_checker: Callable[[str], bool] | None = None,
        event_log=None,  # service.eventlog.EventLog | None
        usage_store=None,  # storage.usage.UsageStore | None
        reputation=None,  # storage.reputation.ReputationStore | None
        capabilities=None,  # storage.capabilities.CapabilityStore | None
        lists=None,  # storage.lists.ModelLists | None (глобальные каталога)
        user_dir_for=None,  # Callable[[str], Path | None] — каталог человека (серый список)
        rules=None,  # storage.rules.RuleStore | None — «строка → действие»
        autotest_store=None,  # storage.autotest.AutotestStore | None
    ) -> None:
        self._entries_provider = entries_provider
        self._keys = keys
        self._timeout = timeout
        self._provider_factory = provider_factory
        self._token = token
        self._user_tokens = user_tokens
        self._context_for_user = context_for_user
        self._api_gate = api_gate
        self._access_checker = access_checker
        self._event_log = event_log
        self._usage_store = usage_store
        self._reputation = reputation
        self._capabilities = capabilities
        self._lists = lists
        self._user_dir_for = user_dir_for
        self._rules = rules
        self._autotest_store = autotest_store
        # Реестр доступа: {человек: (время, годен?)} и память о поломке.
        self._access_cache: dict[str, tuple[float, bool]] = {}
        self._access_down_until = 0.0
        # Кэш live-списков моделей: {провайдер: (время, [модели])}.
        self._live_cache: dict[str, tuple[float, list[str]]] = {}
        self._cache_lock = threading.Lock()

    # ------------------------------------------------------------ аутентификация

    @property
    def auth_required(self) -> bool:
        return bool(self._token)

    def check_token(self, authorization_header: str) -> bool:
        """Есть ли в заголовке нужный токен приставки.

        Клиент шлёт Bearer-токен приставки (как в OpenRouter); настоящие
        ключи провайдеров сюда не попадают — приставка подставляет их сама.
        """
        if not self._token:
            return True
        value = authorization_header.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        return hmac.compare_digest(value.encode("utf-8"), self._token.encode("utf-8"))

    def authenticate(self, authorization_header: str) -> tuple[bool, str, str]:
        """(разрешено, id владельца токена, причина отказа). Пустой id — устройство.

        В персональном режиме токен принадлежит человеку: его id уходит
        дальше, и запрос обслуживается его пространством. Тумблер
        «доступ приложений» (api_gate) выключает все токены человека разом.
        """
        value = authorization_header.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()

        if self._user_tokens is not None:
            if not value and not self._token:
                return True, "", ""  # --no-auth: открыто, обслуживается устройство
            owner = self._user_tokens.owner(value)
            if not owner:
                return False, "", ""
            if self._api_gate is not None and not self._api_gate(owner):
                return False, "", "доступ приложений отключён владельцем токена"
            return True, owner, ""

        if not self._token:
            return True, "", ""
        return hmac.compare_digest(value.encode("utf-8"), self._token.encode("utf-8")), "", ""

    def authorize(self, user_id: str) -> tuple[bool, str]:
        """Пропустить ли человека: зарегистрирован и не заблокирован.

        Реестр — сервер каталога; его ответ помнится ACCESS_TTL секунд
        на человека (десятки запросов = один поход), ошибка реестра —
        пауза ACCESS_DOWN, без долба по каждому запросу. Без
        ``access_checker`` (тесты, режим без реестра) пускают всех.
        """
        if not user_id or self._access_checker is None:
            return True, ""
        now = time.monotonic()
        if now < self._access_down_until:
            return False, "доступ не подтверждён: реестр недоступен, попробуйте через минуту"
        cached = self._access_cache.get(user_id)
        if cached and now - cached[0] < ACCESS_TTL:
            return cached[1], (
                "" if cached[1] else "нет доступа: аккаунт не зарегистрирован или заблокирован"
            )
        try:
            ok = bool(self._access_checker(user_id))
        except Exception:  # noqa: BLE001 — любая битость реестра = пауза, а не паника
            self._access_down_until = now + ACCESS_DOWN
            return False, "доступ не подтверждён: реестр недоступен, попробуйте через минуту"
        self._access_cache[user_id] = (now, ok)
        return ok, (
            "" if ok else "нет доступа: аккаунт не зарегистрирован или заблокирован"
        )

    def _context(self, user_id: str) -> tuple[Callable[[], list[dict]], object]:
        """(список провайдеров, ключи) для владельца токена."""
        if user_id and self._context_for_user is not None:
            context = self._context_for_user(user_id)
            if context is not None:
                return context
        return self._entries_provider, self._keys

    # ------------------------------------------------------------ live-модели

    def _refresh_live_models(self, entries: list[dict]) -> None:
        """Обновляет кэш live-списков для всех включённых провайдеров.

        Вызывается из фонового потока; молчащий провайдер просто не
        попадает в кэш — его настроенные модели всё равно в списке.
        """
        now = time.monotonic()
        for entry in entries:
            if not entry.get("enabled"):
                continue
            provider_id = entry["id"]
            with self._cache_lock:
                cached = self._live_cache.get(provider_id)
                if cached and now - cached[0] < _MODELS_TTL:
                    continue
            try:
                client = self._provider_factory(
                    entry["url"], timeout=_MODELS_FETCH_TIMEOUT
                )
                key = self._keys.get(provider_id)
                models = [m["name"] for m in client.fetch_models(key)]
            except Exception:  # noqa: BLE001 — молчащий провайдер не крашит список
                continue
            with self._cache_lock:
                self._live_cache[provider_id] = (now, models)

    def start_refresh_loop(self, interval: float = _MODELS_TTL) -> None:
        """Фоновое обновление live-списков: сразу и потом по интервалу."""

        def loop() -> None:
            while True:
                try:
                    self._refresh_live_models(self._entries_provider())
                except Exception:  # noqa: BLE001 — фон не должен умирать
                    pass
                time.sleep(interval)

        threading.Thread(target=loop, daemon=True).start()

    # ------------------------------------------------------------ журнал

    def note(self, kind: str, user: str, text: str, **fields) -> None:
        """Событие в журнал приставки; без журнала — молча пропускаем."""
        if self._event_log is not None:
            self._event_log.record(kind, user, text, **fields)

    def _record_usage(
        self, user_id: str, entry: dict, model: str, usage: dict, *, ok: bool, switched: bool
    ) -> None:
        """Сбросить расходы запроса в статистику; ошибка — не повод падать."""
        if self._usage_store is None or not user_id:
            return
        try:
            self._usage_store.add(
                user_id,
                entry["id"],
                model,
                prompt=int(usage.get("prompt_tokens") or 0),
                completion=int(usage.get("completion_tokens") or 0),
                ok=ok,
                switched=switched,
            )
        except Exception:  # noqa: BLE001 — статистика не должна ломать чат
            pass

    @staticmethod
    def _rep_key(entry: dict, model: str) -> str:
        return f"{entry.get('id')}/{model}"

    def _fault(self, entry: dict, model: str, reason: str) -> None:
        """Поломка модели: записать штраф; ушла на паузу — в журнал."""
        if self._reputation is None:
            return
        try:
            paused = self._reputation.fault(self._rep_key(entry, model))
        except Exception:  # noqa: BLE001 — репутация не должна ломать чат
            return
        if paused:
            self.note(
                "пауза",
                "—",
                f"модель {model} ({entry.get('name') or entry.get('id')}) "
                f"ушла на паузу: {reason}",
            )

    def _healed(self, entry: dict, model: str) -> None:
        """Нормальный ответ немедленно снимает штрафы с модели."""
        if self._reputation is None:
            return
        try:
            self._reputation.ok(self._rep_key(entry, model))
        except Exception:  # noqa: BLE001
            pass

    def _repute_chain(self, attempts: list[dict], by_id: dict) -> list[dict]:
        """Отсечь модели на паузе. Но если ушли все — берём и наказанные:
        чужой ответ лучше мёртвой ошибки, а удачный запрос снимет паузу."""
        if self._reputation is None or not attempts:
            return attempts
        try:
            free = [
                attempt
                for attempt in attempts
                if self._reputation.remaining(
                    self._rep_key(by_id.get(attempt["providerId"], {}), attempt["model"])
                )
                == 0
            ]
        except Exception:  # noqa: BLE001
            return attempts
        return free or attempts

    # ------------------------------------------------------------ способности

    #: Как человек называет то, что просил запрос — для журнала.
    _CAP_WORDS = {"vision": "с картинкой", "tools": "с инструментами"}

    def _learn(
        self, entry: dict, model: str, capability: str, ok: bool, reason: str = "",
        *, req: str = "", resp: str = "",
    ) -> None:
        """Запомнить доказанный факт и рассказать о нём в журнале.

        Каждый шаг описывается подробно — журнал это видит:
        - успех на неизвестной модели: «выучила vision»;
        - успех после прежнего «не умеет»: «перевернула "не умеет"»;
        - повторный успех: «подтвердила vision»;
        - отказ провайдера: «выучила "не умеет tools"» / «подтверждено».
        Отказ канала («только через WebSocket») — не про способности:
        не учи ни vision, ни tools, но событие отметить всё равно надо.
        """
        if self._capabilities is None:
            return
        key = self._rep_key(entry, model)
        subject = f"{key} ({entry.get('name') or entry.get('id')})"
        if not capability:
            if reason:
                fields = {}
                if req:
                    fields["req"] = req
                if resp:
                    fields["resp"] = resp
                self.note(
                    "способности", "—",
                    f"{subject}: {reason} — не про способности, не учила",
                    **fields,
                )
            return
        try:
            was = self._capabilities.known(key).get(capability)
            self._capabilities.learn(key, capability, ok)
        except Exception:  # noqa: BLE001 — знания не должны ломать чат
            return
        with_what = self._CAP_WORDS.get(capability, f"запросом ({capability})")
        if ok:
            if was is None:
                text = f"{subject}: запрос {with_what} прошёл — выучила {capability}"
            elif was:
                text = f"{subject}: запрос {with_what} прошёл — подтвердила {capability}"
            else:
                text = (
                    f"{subject}: запрос {with_what} прошёл — перевернула "
                    f"«не умеет», теперь {capability}"
                )
        else:
            detail = reason or "провайдер отверг запрос"
            if was is None:
                text = f"{subject}: {detail} — выучила «не умеет {capability}»"
            elif was:
                text = (
                    f"{subject}: {detail} — отозвала {capability}: "
                    "выучила «не умеет»"
                )
            else:
                text = f"{subject}: {detail} — «не умеет {capability}» подтверждено"
        fields = {}
        if req:
            fields["req"] = req
        if resp:
            fields["resp"] = resp
        self.note("способности", "—", text, **fields)

    def _skipped_by_capability(
        self, entries: list[dict], attempts: list[dict], needs: tuple[str, ...]
    ) -> list[str]:
        """Модели, не попавшие в цепочку из-за способностей.

        Нужно для честной диагностики: когда фолбэк «пустой», в ошибке
        видно, сколько моделей отсеяно и почему («запрос требует vision»),
        а не только причина последней попытки.
        """
        if not needs:
            return []
        used = {attempt["providerId"] for attempt in attempts}
        skipped: list[str] = []
        for entry in entries:
            model = str(entry.get("model") or "").strip()
            if not model or entry["id"] in used:
                continue
            caps = str(entry.get("capabilities") or "").lower()
            if not all(word in caps for word in needs):
                skipped.append(f"{entry['id']}/{model}")
        return skipped

    def _apply_lists(self, entries: list[dict], user_id: str = "",
                     needs: tuple[str, ...] = ()) -> list[dict]:
        """Четыре уровня списков + степень совпадения группы.

        Уровни (`_tier`): 1 — белый (подтверждённые), 2 — неизвестный,
        3 — серый этого человека с истёкшим сном, 4 — чёрный (последний
        шанс). Спящий **провайдер** (серый) исключается целиком — со
        всеми моделями. Уровень решает, в каком порядке эшелоны идут;
        внутри эшелона сортирует `core.routes`.

        Степень совпадения (`_fit`) — какой группе модель отвечает на
        **этот** запрос: 0 — точное совпадение (текстовой запрос → текстовая
        группа, картинка → vision, инструменты → tools), 1 — надмножество
        (умеет больше, чем просят — дороже, но сгодится), 2 — не умеет
        нужного (из цепочки её выкинет фильтр способностей, но пусть
        сортировка не поднимает её вверх).
        """
        if self._lists is None:
            return entries
        try:
            white = self._lists.whitelist()
            black = self._lists.blacklist()
            grey = self._grey_of(user_id)
        except Exception:  # noqa: BLE001 — списки не должны ломать чат
            return entries
        now = datetime.now(timezone.utc)
        needed = set(needs)
        kept: list[dict] = []
        for entry in entries:
            model = str(entry.get("model") or "").strip()
            key = f"{entry['id']}/{model}".lower()
            short = model.lower()
            cell = grey.get(entry["id"])  # серый — провайдером, со всеми моделями
            if cell and cell["until"] > now:
                continue  # спит — не трогаем все его модели
            if black and (key in black or short in black):
                tier = 4
            elif (self._lists is not None and
                  (key in self._lists.paid() or short in self._lists.paid())):
                tier = 4  # платная — последний шанс, после бесплатных
            elif white and (key in white or short in white):
                tier = 1
            elif cell:  # срок сна истёк — провайдер пробудился
                tier = 3
            else:
                tier = 2
            fit = self._fit_of(entry, needed)
            kept.append({**entry, "_tier": tier, "_fit": fit})
        return kept

    @staticmethod
    def _fit_of(entry: dict, needed: set) -> int:
        """Степень совпадения группы модели с запросом.

        Способности берутся из обученного поля «Способности» (оно уже
        склеено с рукописной справкой в `_taught_entries`).
        """
        caps = str(entry.get("capabilities") or "").lower()
        has = {
            word for word in ("vision", "tools") if word in caps
        }
        if not needed:
            # Текстовый запрос: точка — текстовая группа; надмножество (vision/tools) дороже.
            return 0 if not has else 1
        if has == needed:
            return 0  # точное совпадение: vision→vision, tools→tools, оба→оба
        if needed < has:
            return 1  # умеет больше, чем просят — сгодится, но это надмножество
        return 2  # нужного нет

    def _grey_of(self, user_id: str) -> dict:
        """Серый список человека: пусто, если каталога нет."""
        if not self._user_dir_for or not user_id:
            return {}
        directory = self._user_dir_for(user_id)
        if directory is None:
            return {}
        return ModelLists(directory).grey()

    # ------------------------------------------------------------ решения

    #: Мягкие причины: модель жива, но «не сегодня» (лимит/кредиты/«позже»).
    _SOFT_HINTS = (
        "insufficient credit", "out of credit", "no credit", "more credits",
        "try again later", "try later", "rate limit", "too many requests",
        "quota exceeded", "usage limit", "credits", "баланс", "кредит",
        "лимит", "квот", "попробуйте позже", "попробуй позже",
    )

    def _is_soft(self, text: str) -> bool:
        """Похоже ли сообщение на мягкий отказ (живая модель, временная причина)."""
        low = str(text or "").lower()
        return any(hint in low for hint in self._SOFT_HINTS)

    def _grey_store(self, user_id: str):
        """ModelLists персонального каталога человека; None — каталога нет."""
        if not self._user_dir_for or not user_id:
            return None
        directory = self._user_dir_for(user_id)
        return ModelLists(directory) if directory is not None else None

    def _key_of(self, entry: dict, model: str) -> str:
        return f"{entry['id']}/{model}"

    # ------------------------------------------------------------ правила

    def _rule_hits(self, entry: dict, *, req_text: str, resp_text: str,
                   status: int) -> list[tuple[str, str]]:
        """Все подошедшие правила ответа провайдера: [(действие, условие)].

        Условие составное (строка в запросе + строка в ответе + статус):
        все указанные части обязаны совпасть. Срабатывают **все**
        подошедшие правила по порядку — одной строкой можно и выучить,
        и в белый положить. Правило админа сильнее встроенного решения:
        проверяется до него.
        """
        if self._rules is None:
            return []
        try:
            rules = self._rules.response_rules(entry["id"])
        except Exception:  # noqa: BLE001 — правила не должны ломать чат
            return []
        hits: list[tuple[str, str]] = []
        for rule in rules:
            if match_response(rule, req_text=req_text, resp_text=resp_text,
                              status=status):
                action = str(rule.get("action") or "")
                if action:
                    hits.append((action, describe(rule)))
        return hits

    def _apply_rules(self, entry: dict, model: str,
                     hits: list[tuple[str, str]], user_id: str,
                     *, status: int = 0, resp: str = "",
                     req: str = "") -> bool:
        """Выполнить все подошедшие правила. True — переключиться дальше.

        Действия: grey / black / white / switch / untouched / выучить
        (can-vision, can-tools, cant-vision, cant-tools). «Годные»
        действия (белый, только журнал, выучить «умеет») сами по себе
        не уводят — ответ отдаётся клиенту; серый/чёрный/переключить/
        «не умеет» уводят к следующему. На ответе с ошибкой приставка
        уходит к следующему в любом случае. Каждое срабатывание —
        строка в журнале с условием-основанием.
        """
        key = self._key_of(entry, model)
        leave = False
        for action, hit in hits:
            fields: dict = {
                "model": model, "provider": entry["name"],
                "rule": hit, "action": action,
            }
            if status:
                fields["status"] = status
            if resp:
                fields["resp"] = resp
            if req:
                fields["req"] = req

            if action == "grey":
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → серый список (правило)",
                          **fields)
                self._decide_soft(entry, model, f"правило: {hit}", user_id,
                                  status=status, resp=resp, req=req)
                leave = True
            elif action == "black":
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → чёрный список (правило)",
                          **fields)
                self._decide_hard(entry, model, f"правило: {hit}", user_id,
                                  status=status, resp=resp, req=req)
                leave = True
            elif action == "white":
                try:
                    known = self._capabilities.known(key) if self._capabilities else {}
                except Exception:  # noqa: BLE001
                    known = {}
                group = self._group_of(entry, known)
                self._lists.whitelist_place(key, group, f"правило: {hit}")
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → в белый список "
                          f"(группа {group})", **fields)
            elif action == "untouched":
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → списки не тронуты",
                          **fields)
            elif action in LEARN_ACTIONS:
                capability, ok = LEARN_ACTIONS[action]
                self._learn(entry, model, capability, ok, f"правило: {hit}", req=req, resp=resp)
                # Новые списки «доказано без vision/tools»: модель, доказавшая
                # отсутствие, добавляется; доказавшая присутствие — удаляется.
                if capability == "vision":
                    if ok:
                        self._lists.refused_vision_drop(key)
                    else:
                        self._lists.refused_vision_add(key)
                elif capability == "tools":
                    if ok:
                        self._lists.refused_tools_drop(key)
                    else:
                        self._lists.refused_tools_add(key)
                verb = "умеет" if ok else "не умеет"
                word = "картинки" if capability == "vision" else "инструменты"
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → выучено «{verb} {word}»",
                          **fields)
                if not ok:
                    leave = True  # сказала «не умеет» — ответ пустой, уходим
            else:
                # switch и любое неизвестное — просто уходим дальше
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → переключение на другого",
                          **fields)
                leave = True
        return leave

    #: Группы белого списка из подписи модели.
    @staticmethod
    def _group_of(entry: dict, known: dict) -> str:
        """Группа модели по обученной подписи: text/vision/tools/vision-tools."""
        caps = _merge_capabilities(entry.get("capabilities"), known)
        has_v = "vision" in caps.lower()
        has_t = "tools" in caps.lower()
        if has_v and has_t:
            return "vision-tools"
        if has_v:
            return "vision"
        if has_t:
            return "tools"
        return "text"

    def _decide_success(self, entry: dict, model: str, user_id: str,
                        status: int = 200, resp: str = "",
                        needs: tuple[str, ...] = (),
                        req: str = "") -> None:
        """Успех: в белый список (в группу её подписи), из серого/чёрного — наружу.

        Группа = подпись модели: запрос с картинкой доказал vision —
        модель ложится в vision-файл (или перекладывается из text, если
        вдруг уже была подтверждена). Событие пишется **только при смене
        состояния** — впервые, переложена, вышла из серого/чёрного.
        Повторный успех уже подтверждённой модели молчит: журнал не
        захлебнётся. Основание (провайдер, статус, тело) прикладывается.

        Успех с нужной способностью снимает отметку «доказано без …»:
        модель показала, что умеет.
        """
        if self._lists is None:
            return
        key = self._key_of(entry, model)
        try:
            known: dict = {}
            if self._capabilities is not None:
                try:
                    known = self._capabilities.known(key)
                except Exception:  # noqa: BLE001
                    known = {}
            group = self._group_of(entry, known)
            was_group = self._lists.whitelist_group(key)
            was_grey = entry["id"] in self._grey_of(user_id)
            was_black = key.lower() in self._lists.blacklist()
            changed = self._lists.whitelist_place(
                key, group, "подтверждена успехом"
            )
            if was_group == group and not (was_grey or was_black) and not changed:
                return  # уже подтверждена в этой же группе — не шумим
            self._lists.blacklist_drop(key)
            grey = self._grey_store(user_id)
            if grey is not None:
                grey.grey_drop(entry["id"])  # серый — провайдером
            # Успех с нужной способностью снимает отметку «не умеет»:
            # модель показала, что умеет.
            if "vision" in needs:
                self._lists.refused_vision_drop(key)
            if "tools" in needs:
                self._lists.refused_tools_drop(key)
            if was_grey or was_black:
                origin = "из серого" if was_grey else "из чёрного"
                text = (
                    f"{entry['name']}: HTTP {status} — подтвердила себя, "
                    f"{origin} в белый список (группа {group})"
                )
            elif was_group and was_group != group:
                text = (
                    f"{entry['name']}: HTTP {status} — переложена: "
                    f"{was_group} → {group} (доказала новую способность)"
                )
            else:
                text = (
                    f"{entry['name']}: HTTP {status} — подтвердила себя, "
                    f"впервые в белом списке (группа {group})"
                )
            fields = {
                "model": model, "provider": entry["name"], "status": status,
                "group": group,
            }
            if req:
                fields["req"] = req
            if resp:
                fields["resp"] = resp
            self.note("решение", user_id or "—", text, **fields)
        except Exception:  # noqa: BLE001 — списки не должны ломать чат
            pass

    def _decide_soft(self, entry: dict, model: str, reason: str, user_id: str,
                     status: int = 0, resp: str = "",
                     req: str = "") -> None:
        """Мягкий отказ: провайдер в серый на сутки; третий цикл — чёрный.

        Серый — провайдером (со всеми моделями), циклы считаются по
        нему; в чёрный уходит конкретная «провайдер/модель».
        """
        if self._lists is None:
            return
        key = self._key_of(entry, model)
        fields: dict = {"model": model, "provider": entry["name"]}
        if status:
            fields["status"] = status
        if req:
            fields["req"] = req
        if resp:
            fields["resp"] = resp
        try:
            grey = self._grey_store(user_id)
            if grey is None:
                return
            cell = grey.grey().get(entry["id"])
            cycle = (cell["cycle"] if cell else 0) + 1
            self._lists.whitelist_drop(key)
            if cycle >= GREY_CYCLE_LIMIT:
                grey.grey_drop(entry["id"])
                self._lists.blacklist_add(
                    key, f"{reason} · {cycle} цикла сна подряд"
                )
                self.note(
                    "решение", user_id or "—",
                    f"{entry['name']}: {reason} — {cycle} цикла сна подряд, "
                    "в чёрном списке",
                    **fields,
                )
                return
            until = grey.grey_add(entry["id"], reason, cycle=cycle)
            when = until.strftime("%d.%m %H:%M")
            self.note(
                "решение", user_id or "—",
                f"{entry['name']}: {reason} — серый список до {when} (цикл {cycle})",
                **fields,
            )
        except Exception:  # noqa: BLE001
            pass

    def _decide_hard(self, entry: dict, model: str, reason: str, user_id: str,
                     status: int = 0, resp: str = "", req: str = "") -> None:
        """Жёсткий отказ: в чёрный список, из белого и серого — наружу."""
        if self._lists is None:
            return
        key = self._key_of(entry, model)
        fields: dict = {"model": model, "provider": entry["name"]}
        if status:
            fields["status"] = status
        if req:
            fields["req"] = req
        if resp:
            fields["resp"] = resp
        try:
            self._lists.whitelist_drop(key)
            self._lists.blacklist_add(key, reason)
            # Серый не трогаем: он теперь по провайдеру, а поломка одной
            # его модели не должна будить (или гасить) остальные.
            self.note(
                "решение", user_id or "—",
                f"{entry['name']}: {reason} — в чёрном списке",
                **fields,
            )
        except Exception:  # noqa: BLE001
            pass

    def _decide_client(self, entry: dict, model: str, user_id: str,
                       status: int = 0, resp: str = "", req: str = "") -> None:
        """Провайдер отверг запрос (400) не по способности — списки не тронуты.

        Это не поломка модели (не её вина) и не повод учить «не умеет»:
        запрос неподдерживаемого формата или квирка провайдера. Решение
        озвучиваем, чтобы в журнале не было тишины, но без обвинений —
        «вина клиента» звучит резко и часто неверно.
        """
        fields: dict = {"model": model, "provider": entry["name"]}
        if status:
            fields["status"] = status
        if req:
            fields["req"] = req
        if resp:
            fields["resp"] = resp
        self.note(
            "решение", user_id or "—",
            f"{entry['name']}: HTTP {status} — запрос отклонён провайдером, "
            "списки не тронуты",
            **fields,
        )

    def _taught_entries(self, entries: list[dict]) -> list[dict]:
        """Копии записей с выученными способностями в поле «Способности».

        Живой факт перекрывает рукописную справку в обе стороны: успех
        дописывает способность, честный отказ вычёркивает — кто последний,
        тот и прав. Маршрутизатор (`core/routes.py`) читает текст поля и
        не меняется; знание вливается через существующий вход.

        Дополнительно помечаем `_refused` — способности, от которых модель
        **доказала** отказ (обученный `False` или явное «не умеет …» в
        справке). Это не то же самое, что «просто не заявлена»: доказанный
        отказ позволяет роутеру не тратить попытку на заведомо
        неподходящую модель, даже если клиент её назвал.

        Новые списки «доказано без vision/tools» (novision.txt / notools.txt)
        дополняют эту память: модель в списке считается отказавшей, и
        приставка больше не кормит её картинками/инструментами.
        """
        if self._capabilities is None and self._lists is None:
            return entries
        taught: list[dict] = []
        for entry in entries:
            key = self._rep_key(entry, str(entry.get("model") or "").strip())
            known = {}
            if self._capabilities is not None:
                try:
                    known = self._capabilities.known(key)
                except Exception:  # noqa: BLE001
                    known = {}
            refused = {
                capability
                for capability, value in known.items()
                if value is False
            }
            # Явное «не умеет …» в рукописной справке — тоже отказ.
            text = str(entry.get("capabilities") or "").lower()
            for capability in ("vision", "tools"):
                if f"не умеет {capability}" in text:
                    refused.add(capability)
            # Новые списки: «доказано без vision/tools» — глобальные, как
            # blacklist. Модель в списке считается доказавшей отсутствие.
            if self._lists is not None:
                model_key = key.lower()
                short = str(entry.get("model") or "").strip().lower()
                if model_key in self._lists.refused_vision() or short in self._lists.refused_vision():
                    refused.add("vision")
                if model_key in self._lists.refused_tools() or short in self._lists.refused_tools():
                    refused.add("tools")
            if known or refused:
                entry = {
                    **entry,
                    "capabilities": _merge_capabilities(
                        entry.get("capabilities"), known
                    ) if known else entry.get("capabilities"),
                    "_refused": refused,
                }
            taught.append(entry)
        return taught

    # ------------------------------------------------------------ решения

    def models_payload(self, user_id: str = "") -> dict:
        """Объединённый список: настроенные (без префикса) + live (с префиксом)."""
        entries_provider, keys = self._context(user_id)
        entries = entries_provider()
        configured = advertised_models(entries)

        # Берём кэш для включённых провайдеров; протухшее обновляем тут же
        # (stale-while-revalidate): сеть не блокирует ответ.
        live: dict[str, list[str]] = {}
        now = time.monotonic()
        stale: list[dict] = []
        with self._cache_lock:
            for entry in entries:
                if not entry.get("enabled"):
                    continue
                cached = self._live_cache.get(entry["id"])
                if cached and now - cached[0] < _MODELS_TTL:
                    live[entry["id"]] = cached[1]
                else:
                    stale.append(entry)

        # Протухшие обновляем синхронно — это быстро (короткий таймаут),
        # и клиент получает полный список с первого раза.
        for entry in stale:
            try:
                client = self._provider_factory(
                    entry["url"], timeout=_MODELS_FETCH_TIMEOUT
                )
                key = keys.get(entry["id"])
                models = [m["name"] for m in client.fetch_models(key)]
                live[entry["id"]] = models
                with self._cache_lock:
                    self._live_cache[entry["id"]] = (now, models)
            except Exception:  # noqa: BLE001 — молчащий провайдер не крашит список
                continue

        models = union_models(
            [{"id": e["id"], "model": e.get("model", "")} for e in entries if e.get("enabled")],
            live,
        )
        return {"object": "list", "data": models} if models else _NO_MODELS

    def _chat_timeout(self, entry: dict) -> float:
        """Таймаут чата для одного провайдера.

        Локальные модели загружаются в память на первом запросе — им
        ждём до LOCAL_TIMEOUT; остальные работают с настроенным.
        """
        if is_local_url(str(entry.get("url") or "")):
            return max(self._timeout, LOCAL_TIMEOUT)
        return self._timeout

    # ------------------------------------------------------------ автотесты

    def run_autotest(
        self,
        provider_id: str,
        image_b64: str,
        prompt: str,
        tests: list[str],
        *,
        record: bool = False,
        voice: str = "",
        only_model: str = "",
        audio: bytes | None = None,
        audio_name: str = "sample.wav",
    ) -> dict:
        """Прогнать тесты по всем моделям провайдера.

        Промпт — только пользовательский: своих подсказок нет. Обычные
        типы гоняются по всем моделям, медиа-типы (картинки, звук, видео,
        векторы) — только по своим из метаданных каталога. Возвращает
        {"provider", "results", "prompt"}; при ошибке — "error".
        """
        prompt = (prompt or "").strip()
        if not prompt:
            return {"provider": provider_id, "error": "промпт не задан", "results": []}

        entries = self._entries_provider()
        entry = next((e for e in entries if e["id"] == provider_id), None)
        if entry is None:
            return {"provider": provider_id, "error": "провайдер не найден", "results": []}

        key = self._keys.get(provider_id)
        if self._autotest_store is not None:
            self._autotest_store.tick("получение списка моделей…", "")
        # Список моделей — короткий таймаут: висеть на нём нельзя,
        # по неудаче просто идём настроенной моделью.
        list_client: Any = None
        try:
            list_client = self._provider_factory(entry["url"], timeout=15.0)
            models = [m["name"] for m in list_client.fetch_models(key)]
        except Exception:  # noqa: BLE001 — нет списка — используем настроенную
            models = [str(entry.get("model") or "").strip()] if entry.get("model") else []
        if not models:
            return {"provider": provider_id, "error": "моделей не найдено", "results": []}

        # Метаданные каталога — чтобы медиа-типы гоняли только свои модели.
        meta: dict = {}
        if list_client is not None and not only_model and any(
            test_type in _MEDIA_TEST_TYPES for test_type in tests
        ):
            try:
                meta = list_client.fetch_model_meta(key)
            except Exception:  # noqa: BLE001 — нет меты: запасной выбор по именам
                meta = {}

        # План прогона: тип теста решает, какими моделями его гонять;
        # тип без моделей — честная строка ошибки, а не тихий пропуск.
        results: list[dict] = []
        plan: list[tuple[str, str]] = []
        for test_type in tests:
            chosen = [only_model] if only_model else _models_for_test(test_type, models, meta)
            if not chosen:
                results.append({
                    "model": "—",
                    "test": test_type,
                    "ts": _now_iso(),
                    "status": "fail",
                    "http_status": 0,
                    "response": f"у провайдера нет моделей типа {test_type} в каталоге",
                    "used_tool": None,
                    "duration": 0,
                    "req": "",
                    "resp": "",
                })
                continue
            plan.extend((model, test_type) for model in chosen)

        total = len(plan)
        if self._autotest_store is not None:
            self._autotest_store.plan(total)

        stopped = False
        for model, test_type in plan:
            if self._autotest_store is not None and self._autotest_store.stop_requested():
                stopped = True
                break
            if self._autotest_store is not None:
                self._autotest_store.tick(model, test_type)
            result = self._run_one_autotest(
                entry, model, test_type, image_b64, prompt,
                record=record, voice=voice, audio=audio, audio_name=audio_name,
            )
            results.append(result)
            if self._autotest_store is not None:
                self._autotest_store.record(result)
                self._autotest_store.advance()

        if self._autotest_store is not None:
            self._autotest_store.finish(provider_id, results, prompt=prompt)

        if stopped:
            return {"provider": provider_id, "results": results, "prompt": prompt,
                    "stopped": True}
        return {"provider": provider_id, "results": results, "prompt": prompt}

    def _run_one_autotest(self, entry: dict, model: str, test_type: str,
                          image_b64: str, prompt: str, *, record: bool,
                          voice: str = "", audio: bytes | None = None,
                          audio_name: str = "sample.wav") -> dict:
        """Один тест одной модели. Возвращает словарь результата."""
        import json as _json
        import time

        body: dict = {"model": model}
        json_body = True
        if test_type == "vision":
            body["messages"] = [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_b64}},
            ]}]
        elif test_type == "tools":
            body["messages"] = [{"role": "user", "content": prompt}]
            body["tools"] = [{"type": "function", "function": {
                "name": "get_current_time",
                "description": "Возвращает текущее время сервера",
                "parameters": {"type": "object", "properties": {}},
            }}]
            body["tool_choice"] = "auto"
        elif test_type == "image":
            # Генерация: OpenAI-совместимый эндпоинт; ссылка вместо base64.
            body = {"model": model, "prompt": prompt, "n": 1,
                    "response_format": "url"}
        elif test_type == "tts":
            body = {"model": model, "input": prompt}
            if voice.strip():
                body["voice"] = voice.strip()
        elif test_type == "embedding":
            body = {"model": model, "input": prompt}
        elif test_type == "stt":
            body = {}  # multipart с файлом уходит отдельно
            json_body = False
        elif test_type in ("video", "3d"):
            body = {}  # GET с промптом в пути
            json_body = False
        else:  # text, free
            body["messages"] = [{"role": "user", "content": prompt}]

        if json_body:
            body_bytes = _json.dumps(body, ensure_ascii=False).encode("utf-8")
            req_text = body_bytes.decode("utf-8", errors="replace")
        elif test_type == "stt":
            req_text = f"POST /v1/audio/transcriptions — model={model}, файл={audio_name}"
        else:
            req_text = f"GET /{test_type}/{{промпт}} — model={model}"
        key = self._keys.get(entry["id"])
        client = self._provider_factory(entry["url"], timeout=60.0)

        if test_type == "stt" and not audio:
            # Без звука запрос заведомо не имеет смысла — честно и сразу.
            return {
                "model": model, "test": test_type, "ts": _now_iso(),
                "status": "fail", "http_status": 0,
                "response": "нет аудио для stt (загрузите файл или отметьте тестовый тон)",
                "used_tool": None, "duration": 0.0,
                "req": req_text, "resp": "",
                "reaction": "сработала бы: нет аудио для stt",
            }

        start = time.monotonic()
        reaction_status = 0
        reaction_used_tool = False
        reaction_cap = ""
        try:
            if test_type in ("video", "3d"):
                response = client.media_get(test_type, prompt, model, api_key=key)
            elif test_type == "stt":
                response = client.forward_form(
                    "/audio/transcriptions", {"model": model},
                    {"file": (audio_name, audio or b"", "application/octet-stream")},
                    api_key=key,
                )
            else:
                path = {"image": "/images/generations",
                        "tts": "/audio/speech",
                        "embedding": "/embeddings"}.get(test_type)
                if path is not None:
                    response = client.forward_path(path, body_bytes, api_key=key,
                                                    timeout=60.0, stream=False)
                else:
                    response = client.forward_chat(body_bytes, api_key=key,
                                                   timeout=60.0, stream=False)
            elapsed = round(time.monotonic() - start, 1)
            status_code = getattr(response, "status_code", 0)
            reaction_status = status_code
            ctype = (response.headers.get("content-type") or "").split(";")[0].strip()
            link = str(response.headers.get("Link") or "")

            # Текст и JSON — целиком; большие медиа (звук, видео) — куском:
            # мегабайты в память и в журнал не тянем.
            if test_type in ("video", "3d"):
                raw = _read_capped(response, 64 * 1024)
            elif test_type == "tts":
                raw = _read_capped(response, 2 * 1024 * 1024)
            else:
                raw = response.read() if hasattr(response, "read") else b""
            resp_text = raw.decode("utf-8", errors="replace")

            if status_code >= 400:
                if status_code == 402:
                    try:
                        self._lists.paid_add(f"{entry['id']}/{model}")
                    except Exception:  # noqa: BLE001
                        pass
                if status_code == 400:
                    try:
                        _refusal, reaction_cap = _capability_refusal(
                            raw, needs=(test_type,) if test_type in ("vision", "tools") else ()
                        )
                    except Exception:  # noqa: BLE001
                        reaction_cap = ""
                result = {
                    "model": model,
                    "test": test_type,
                    "ts": _now_iso(),
                    "status": "fail",
                    "http_status": status_code,
                    "response": f"HTTP {status_code}: {resp_text}",
                    "used_tool": None,
                    "duration": elapsed,
                    "req": req_text,
                    "resp": resp_text,
                }
                if record and status_code == 400 and reaction_cap:
                    self._learn(entry, model, reaction_cap, False, "автотест",
                                req=req_text, resp=resp_text)
                    if reaction_cap == "vision":
                        self._lists.refused_vision_add(f"{entry['id']}/{model}")
                    elif reaction_cap == "tools":
                        self._lists.refused_tools_add(f"{entry['id']}/{model}")
            elif test_type in _MEDIA_TEST_TYPES:
                # Генерация/распознавание: свой разбор ответа; запись в
                # способности не участвует — маршрутизация их не использует.
                status_word, shown, stored = _media_outcome(
                    test_type, raw, ctype, link, resp_text)
                result = {
                    "model": model,
                    "test": test_type,
                    "ts": _now_iso(),
                    "status": status_word,
                    "http_status": status_code,
                    "response": shown,
                    "used_tool": None,
                    "duration": elapsed,
                    "req": req_text,
                    "resp": stored,
                }
            else:
                try:
                    data = _json.loads(resp_text) if resp_text else {}
                except ValueError:
                    data = {}
                content = ""
                used_tool = False
                if isinstance(data, dict) and "choices" in data and data["choices"]:
                    msg = data["choices"][0].get("message", {}) or {}
                    content = msg.get("content") or ""
                    used_tool = bool(msg.get("tool_calls"))
                elif isinstance(data, dict) and data.get("error"):
                    content = str(data["error"])
                else:
                    content = resp_text
                reaction_used_tool = used_tool
                result = {
                    "model": model,
                    "test": test_type,
                    "ts": _now_iso(),
                    "status": "ok",
                    "http_status": status_code,
                    "response": content,
                    "used_tool": used_tool if test_type == "tools" else None,
                    "duration": elapsed,
                    "req": req_text,
                    "resp": resp_text,
                }
                if record:
                    if test_type == "vision":
                        self._learn(entry, model, "vision", True, "автотест",
                                    req=req_text, resp=resp_text)
                        try:
                            self._lists.refused_vision_drop(f"{entry['id']}/{model}")
                        except Exception:  # noqa: BLE001
                            pass
                    elif test_type == "tools":
                        self._learn(entry, model, "tools", True, "автотест",
                                    req=req_text, resp=resp_text)
                        try:
                            self._lists.refused_tools_drop(f"{entry['id']}/{model}")
                        except Exception:  # noqa: BLE001
                            pass
            response.close()
        except Exception as exc:  # noqa: BLE001
            elapsed = round(time.monotonic() - start, 1)
            reaction_status = 0
            result = {
                "model": model,
                "test": test_type,
                "ts": _now_iso(),
                "status": "error",
                "http_status": 0,
                "response": str(exc),
                "used_tool": None,
                "duration": elapsed,
                "req": req_text,
                "resp": "",
            }

        result["reaction"] = _reaction(
            reaction_status, test_type, record,
            capability=reaction_cap, used_tool=reaction_used_tool,
        )
        return result

    def chat(self, body: bytes, requested_model: str, user_id: str = ""):
        """Проксирует чат по цепочке маршрутов.

        Возвращает ``(status, content_type, chunks, provider_name, model)`` —
        куски ответа отдаются как есть, в том числе поток SSE; имя
        провайдера и модель уходят клиенту заголовками, чтобы смена
        провайдера была видна. Если никто не справился —
        ``(502, ..., [описание со всеми попытками])``.
        """
        entries_provider, keys = self._context(user_id)
        entries = self._taught_entries(entries_provider())
        needs = _needs_of(body, self._rules)
        entries = self._apply_lists(entries, user_id, needs)
        attempts = route(entries, requested_model, needs=needs)
        started = time.monotonic()
        # Тело запроса для журнала — пишется всегда (лимит 4000, на странице
        # видно целиком).
        body_fields = {"req": body.decode("utf-8", errors="replace")}
        # Текст запроса (нижний регистр) — для условий правил ответа.
        req_text = body.decode("utf-8", errors="replace").lower()
        if not attempts:
            available = ", ".join(model["id"] for model in advertised_models(entries))
            message = f"модель «{requested_model or '—'}» не обслуживается"
            if needs:
                message += (
                    f"; запрос требует: {', '.join(needs)} — включите провайдера "
                    "с такими способностями (поле «Способности»)"
                )
                skipped = self._skipped_by_capability(entries, attempts, needs)
                if skipped:
                    message += (
                        f"; без нужных способностей: {len(skipped)} — "
                        + ", ".join(skipped[:5])
                    )
            if available:
                message += f"; доступны: {available}"
            else:
                message += "; ни один провайдер не включён"
            self.note("чат", user_id or "—", f"нет маршрута: {message[:150]}", **body_fields)
            return 502, "application/json", [_error_body(message, code="model_not_available")], "", ""

        by_id = {entry["id"]: entry for entry in entries}
        # Модели на паузе пропускаются — если остались свободные.
        attempts = self._repute_chain(attempts, by_id)
        problems: list[str] = []
        # Отказы с классификацией: чем закончилась каждая попытка.
        failures: list[dict] = []

        def failed(entry: dict, model: str, kind: str, reason: str,
                   status: int = 0, resp: str = "", req: str = "") -> None:
            """Запомнить отказ попытки: kind — hard / soft / neutral."""
            problems.append(f"{entry['name']}: {reason}".strip())
            failures.append({
                "entry": entry, "model": model, "kind": kind, "reason": reason,
                "status": status, "resp": resp, "req": req,
            })

        def served(
            status,
            content_type,
            entry,
            model,
            chunks,
            resp_body: bytes = b"",
            usage: dict | None = None,
            record: bool = True,
            chat_event: bool = True,
        ):
            """Успех: отдаём ответ, пишем событие и считаем расходы.

            ``chat_event=False`` — событие «чат» напишет сам поток, когда
            дойдёт до конца: у потокового ответа тело собирается по ходу
            и попадает в журнал только целиком.
            """
            duration = round(time.monotonic() - started, 1)
            fields: dict = {
                "model": model,
                "provider": entry["name"],
                "status": status,
                "duration": duration,
                "attempts": len(problems) + 1,
                **body_fields,
            }
            if usage:
                fields["tokens_in"] = usage.get("prompt_tokens")
                fields["tokens_out"] = usage.get("completion_tokens")
            if resp_body:
                fields["resp"] = resp_body.decode("utf-8", errors="replace")
            if chat_event:
                self.note(
                    "чат",
                    user_id or "—",
                    f"{model} через {entry['name']} · HTTP {status} · {duration}s",
                    **fields,
                )
            if record:
                self._record_usage(
                    user_id,
                    entry,
                    model,
                    usage or {},
                    ok=status < 400,
                    switched=bool(problems),
                )
            if status < 400:
                # Живой ответ снимает с модели все прошлые штрафы.
                self._healed(entry, model)
                # Тело ответа — для журнала и для выученных фактов.
                resp_text = (
                    resp_body.decode("utf-8", errors="replace") if resp_body else ""
                )
                # И доказывает способности: запрос с картинкой/инструментами
                # прошёл — модель умеет то, что просил клиент.
                for capability in needs:
                    self._learn(entry, model, capability, True,
                                req=body_fields.get("req", ""), resp=resp_text)
                # Решение: подтверждена — в белый список (только при смене
                # состояния; основание — статус и фрагмент ответа).
                self._decide_success(entry, model, user_id, status=status, resp=resp_text,
                                    needs=needs, req=body_fields.get("req", ""))
            else:
                # Ответ дошёл, но это вина клиента (400/413/422) — списки не трогаем.
                self._decide_client(entry, model, user_id, status=status,
                                    resp=resp_body.decode("utf-8", errors="replace") if resp_body else "",
                                    req=body_fields.get("req", ""))
            return status, content_type, chunks, entry["name"], model

        for attempt in attempts:
            entry = by_id.get(attempt["providerId"])
            if entry is None:
                continue

            key = keys.get(entry["id"])
            client = self._provider_factory(entry["url"], timeout=self._chat_timeout(entry))
            try:
                response = client.forward_chat(
                    _with_model(body, attempt["model"]),
                    api_key=key,
                    timeout=self._timeout,
                    stream=True,
                )
            except ServerError as exc:
                failed(entry, attempt["model"], "hard", str(exc), req=body_fields["req"])
                continue

            if response.status_code >= 400 and should_fallback(response.status_code):
                # Тело читаем до конца: соединение с отказавшим провайдером
                # надо закрыть, а причина пригодится, если никто не справится.
                detail = response.read().decode("utf-8", errors="replace")
                status = response.status_code
                # Правило админа сильнее встроенного решения по статусу:
                # подошедшие правила решают, что делать (все по порядку).
                hits = self._rule_hits(entry, req_text=req_text,
                                       resp_text=detail.lower(), status=status)
                if hits:
                    self._apply_rules(entry, attempt["model"], hits, user_id,
                                      status=status, resp=detail, req=body_fields["req"])
                    # Ошибка остаётся ошибкой: уходим к следующему в любом
                    # случае (даже если все действия «годные»).
                    reason = "правило: " + "; ".join(hit for _, hit in hits)
                    failed(entry, attempt["model"], "neutral", reason,
                           status=status, resp=detail, req=body_fields["req"])
                    response.close()
                    continue
                reason = f"HTTP {status} {detail}".strip()
                if status in (402, 429):
                    kind = "soft"  # кредиты/лимит — временная причина
                elif status in (401, 403):
                    kind = "neutral"  # ключ не годится — не поломка модели
                else:
                    kind = "hard"
                failed(entry, attempt["model"], kind, reason, status=status, resp=detail,
                       req=body_fields["req"])
                if status == 404:
                    # 404 от чата почти всегда «модель неизвестна провайдеру» —
                    # штраф конкретной модели, не всего провайдера.
                    self._fault(
                        entry, attempt["model"], "HTTP 404 — модель неизвестна провайдеру"
                    )
                response.close()
                continue

            if response.status_code == 400:
                # Ответ 400: либо вина клиента (идёт как есть), либо
                # понятный отказ модели за отсутствие картинок/инструментов —
                # такое честнее переключить на подходящую модель.
                raw = response.read()
                text = raw.decode("utf-8", errors="replace")
                # Правило админа сильнее встроенного распознавания отказов.
                hits = self._rule_hits(entry, req_text=req_text,
                                       resp_text=text.lower(), status=400)
                if hits:
                    leave = self._apply_rules(entry, attempt["model"], hits,
                                              user_id, status=400, resp=text,
                                              req=body_fields["req"])
                    if leave:
                        reason = "правило: " + "; ".join(h for _, h in hits)
                        failed(entry, attempt["model"], "neutral", reason,
                               status=400, resp=text, req=body_fields["req"])
                        response.close()
                        continue
                    # Все действия «годные» — 400 отдаём клиенту как есть
                    # (как и без правил), списки не тронуты.
                else:
                    refusal, refused_capability = _capability_refusal(raw, needs)
                    if refusal:
                        self._fault(entry, attempt["model"], refusal)
                        # Отказ провайдера — тоже факт: заявленное «умеет»
                        # опровергнуто практикой, и выученный запрет важнее
                        # рукописной справки. Причина уходит в журнал.
                        self._learn(entry, attempt["model"], refused_capability,
                                    False, refusal,
                                    req=body_fields["req"],
                                    resp=raw.decode("utf-8", errors="replace"))
                        # WebSocket-отказ — модель нерабочая для чата (жёсткий);
                        # отказ по способности — списки не трогаем, только учим.
                        kind = "hard" if not refused_capability else "neutral"
                        failed(entry, attempt["model"], kind, refusal,
                               status=400,
                               resp=raw.decode("utf-8", errors="replace"),
                               req=body_fields["req"])
                        response.close()
                        continue

                def chunks_400(body=raw):
                    yield body

                return served(
                    400,
                    response.headers.get("content-type", "application/json"),
                    entry,
                    attempt["model"],
                    chunks_400(),
                    raw,
                )

            content_type = response.headers.get("content-type", "application/json")

            if response.status_code < 400 and "text/event-stream" not in content_type:
                # Успех (не-поток): тело читаем целиком и проверяем на
                # скрытую ошибку — шлюзы вроде отвечают 200, а кладут
                # {"error":}. Ошибки клиента (4xx без фолбэка) идут как есть.
                payload = response.read()
                response.close()
                text = payload.decode("utf-8", errors="replace")
                # Правило админа сильнее: строка в 200-ответе (например
                # «maintenance») может велеть уйти или списать в серый.
                # Условие составное — строка в запросе + статус и т.п.
                hits = self._rule_hits(entry, req_text=req_text,
                                       resp_text=text.lower(),
                                       status=response.status_code)
                blessed = False  # правила сказали «ответ годный»
                if hits:
                    leave = self._apply_rules(
                        entry, attempt["model"], hits, user_id,
                        status=response.status_code, resp=text,
                        req=body_fields["req"],
                    )
                    if leave:
                        reason = "правило: " + "; ".join(h for _, h in hits)
                        failed(entry, attempt["model"], "neutral", reason,
                               status=response.status_code, resp=text,
                               req=body_fields["req"])
                        continue
                    # Все действия «годные» (белый / выучить «умеет» /
                    # только журнал) — правило админа сильнее встроенных
                    # проверок: отдаём ответ клиенту как есть.
                    blessed = True
                if not blessed:
                    hidden = _hidden_error(payload)
                    if hidden:
                        self._fault(entry, attempt["model"], hidden)
                        kind = "soft" if self._is_soft(hidden) else "hard"
                        failed(entry, attempt["model"], kind, hidden,
                               status=response.status_code,
                               resp=payload.decode("utf-8", errors="replace"), req=body_fields["req"])
                        continue
                    if _garbage_answer(payload, content_type):
                        self._fault(entry, attempt["model"], "мусор вместо JSON")
                        failed(entry, attempt["model"], "hard",
                               "тело ответа не разбирается как JSON",
                               status=response.status_code,
                               resp=payload.decode("utf-8", errors="replace"), req=body_fields["req"])
                        continue

                def chunks(body=payload):
                    yield body

                return served(
                    response.status_code,
                    content_type,
                    entry,
                    attempt["model"],
                    chunks(),
                    payload,
                    usage=_usage_from(payload),
                )

            # Поток начат — ошибку в его середине переключить уже нельзя:
            # клиент начал получать данные. Это честное ограничение прокси.
            # Usage ищем построчно по ходу: не все шлюзы отдают его в потоке.
            # Тело потока собираем для журнала (до 4000 знаков) и пишем
            # событие «чат» в самом конце — ответ виден целиком.
            stream_state: dict = {}

            def chunks(resp=response, state=stream_state):
                buffer = ""
                body: list[str] = []  # хвост тела для журнала
                seen = 0
                try:
                    for piece in resp.iter_bytes():
                        text = piece.decode("utf-8", errors="replace")
                        if seen < 4000:
                            body.append(text[: 4000 - seen])
                            seen += len(text)
                        buffer += text
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            _absorb_usage(state, line)
                        yield piece
                finally:
                    resp.close()
                    # Поток отдан клиенту — фиксируем расходы по его концу
                    # и пишем событие с собранным телом ответа.
                    self._record_usage(
                        user_id,
                        entry,
                        attempt["model"],
                        state.get("usage") or {},
                        ok=True,
                        switched=bool(problems),
                    )
                    duration = round(time.monotonic() - started, 1)
                    self.note(
                        "чат",
                        user_id or "—",
                        f"{attempt['model']} через {entry['name']} · "
                        f"HTTP {response.status_code} · {duration}s",
                        model=attempt["model"],
                        provider=entry["name"],
                        status=response.status_code,
                        duration=duration,
                        attempts=len(problems) + 1,
                        **({"resp": "".join(body)} if body else {}),
                        **body_fields,
                    )

            return served(
                response.status_code, content_type, entry, attempt["model"], chunks(),
                record=False, chat_event=False,
            )

        skipped = self._skipped_by_capability(entries, attempts, needs)
        if skipped:
            # Почему фолбэк «пустой»: модели были, но не прошли по
            # способностям — честнее сказать об этом, чем молчать.
            problems.append(
                f"пропущено {len(skipped)} моделей: запрос требует "
                f"{', '.join(needs)} — " + ", ".join(skipped[:5])
            )
        message = "ни один провайдер не ответил"
        if problems:
            # Все попытки с причинами: диагностика в одну строку.
            message += ": " + "; ".join(problems[:6])
        # Решения по каждому отказу: мягкие и жёсткие идут в списки.
        for item in failures:
            kind = item["kind"]
            if kind == "soft":
                self._decide_soft(item["entry"], item["model"], item["reason"], user_id,
                                  status=item.get("status", 0), resp=item.get("resp", ""),
                                  req=item.get("req", ""))
            elif kind == "hard":
                self._decide_hard(item["entry"], item["model"], item["reason"], user_id,
                                  status=item.get("status", 0), resp=item.get("resp", ""),
                                  req=item.get("req", ""))
            # neutral — ключ или способность: списки не тронуты, но решение
            # озвучено, чтобы в журнале не было «тишины».
            else:
                self.note(
                    "решение", user_id or "—",
                    f"{item['entry']['name']}: {item['reason']} — списки не тронуты",
                    model=item["model"], provider=item["entry"]["name"],
                    **({"status": item["status"]} if item.get("status") else {}),
                    **({"req": item["req"]} if item.get("req") else {}),
                    **({"resp": item["resp"]} if item.get("resp") else {}),
                )
        self._record_usage(
            user_id, {"id": "—"}, requested_model or "—", {}, ok=False, switched=False
        )
        self.note(
            "чат",
            user_id or "—",
            f"не получились все попытки: {'; '.join(problems[:4])}"[:400],
            status=502,
            **body_fields,
        )
        return 502, "application/json", [_error_body(message, kind="api_error", code="upstream_failed")], "", ""


class _Server(ThreadingHTTPServer):
    """HTTP-сервис приставки: несколько потоков и спокойствие в консоли.

    ``socketserver`` зовёт ``handle_error`` на сервере (не на обработчике),
    поэтому подавление шума живёт именно здесь.
    """

    def handle_error(self, request, client_address) -> None:
        """Оборвавшийся клиент — не ошибка.

        Для стриминга это штатно: человек жмёт Esc, приложение закрывает
        вкладку. Traceback в консоль — шум, который пугает больше любой
        поломки. Настоящие ошибки (например, битый запрос) всё равно
        видны по строке доступа и в суперклассе.
        """
        exc_info = sys.exc_info()
        if exc_info and isinstance(exc_info[1], (ConnectionResetError, BrokenPipeError, TimeoutError)):
            print(f"http: {self._failed_address(client_address)}: клиент оборвал связь", flush=True)
            return
        super().handle_error(request, client_address)

    @staticmethod
    def _failed_address(client_address) -> str:
        return client_address[0] if client_address else "?"


class _Handler(BaseHTTPRequestHandler):
    """Один поток на соединение: ThreadingHTTPServer раздаёт их щедро."""

    protocol_version = "HTTP/1.1"
    server_version = "ai-bridge/1"

    # Ни тела запроса, ни ответа в журнал — там могут быть и длинный
    # контекст, и чужие слова. Видно только кто, куда и с каким итогом.
    # flush=True: службу часто запускают в пайп или фон, и без сброса
    # буфера журнал появляется только при остановке.
    def log_message(self, fmt: str, *args: object) -> None:
        print(f"http: {self.address_string()} {fmt % args}", flush=True)

    @property
    def gateway(self) -> Gateway:
        return self.server.gateway  # type: ignore[attr-defined]

    def _reject_unauthorized(self, reason: str = "", *, status: int = 401,
                             close: bool = False) -> None:
        """Отказ в формате, который понимают OpenAI-клиенты.

        401 — «нет токена», 403 — «токен есть, но доступа нет»
        (не зарегистрирован, заблокирован, реестр молчит).
        ``close=True`` — для POST: тело запроса ещё не прочитано,
        и держать соединение дальше нельзя.
        """
        message = reason or (
            "приставка ждёт свой токен в Authorization: Bearer <токен>; "
            "взять его: страница «приложения и токены»"
        )
        self._send(
            status,
            "application/json",
            [
                _error_body(
                    message,
                    kind="authentication_error" if status == 401 else "permission_error",
                    code="missing_token" if status == 401 else "access_denied",
                )
            ],
            close=close,
        )

    # ------------------------------------------------------------ ответы

    def _send(self, status: int, content_type: str, chunks, *, extra_headers: dict | None = None,
              close: bool = False) -> None:
        """Ответ клиенту. ``close=True`` — соединение закрывается после него.

        Закрывать нужно, когда тело запроса ещё не прочитано: на
        keep-alive соединении его остаток приставка приняла бы за
        следующий запрос — в журнале это выглядит как «Unsupported
        method» с мусором из JSON-тела (страница падает с 501).
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        if "event-stream" not in content_type:
            # Поток SSE идёт до закрытия соединения, остальное — с длиной.
            payload = b"".join(chunks)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for chunk in chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Клиент ушёл — это его право, не ошибка.
            pass
        finally:
            self.close_connection = True

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json", [body])

    # ------------------------------------------------------------ методы

    def _try_ui_get(self) -> bool:
        """Отдать GET веб-слою, если он подключён. True — если разобрались."""
        ui = getattr(self.server, "ui", None)
        if ui is None:
            return False
        ui.handle_get(self, self.path)
        return True

    def _try_ui_post(self) -> bool:
        """Отдать POST веб-слою, если он подключён. True — если разобрались."""
        ui = getattr(self.server, "ui", None)
        if ui is None:
            return False
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if 0 < length <= 1024 * 1024:
            body = self.rfile.read(length)
        else:
            # Тела больше мегабайта веб-слою не нужны, но в сокете они
            # остались бы — соединение после ответа закрывается.
            body = b""
            if length > 0:
                self.close_connection = True
        ui.handle_post(self, self.path, body)
        return True

    def do_GET(self) -> None:  # noqa: N802 - имя задано http.server
        path = self.path.split("?", 1)[0]
        if path in ("/v1/models", "/models"):
            allowed, user_id, reason = self.gateway.authenticate(self.headers.get("Authorization", ""))
            if not allowed:
                self.gateway.note("модели", "—", "отказ без токена", status=401)
                self._reject_unauthorized(reason)
                return
            ok, access_reason = self.gateway.authorize(user_id)
            if not ok:
                self.gateway.note("модели", user_id, access_reason, status=403)
                self._reject_unauthorized(access_reason, status=403)
                return
            payload = self.gateway.models_payload(user_id)
            self.gateway.note(
                "модели", user_id,
                f"список: {len(payload.get('data') or [])} моделей",
            )
            self._send_json(200, payload)
            return
        if path.startswith("/v1/"):
            self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")])
            return
        if self._try_ui_get():
            return
        if path == "/":
            self._send_json(
                200,
                {
                    "name": "ai-bridge",
                    "note": "OpenAI-совместимая приставка: /v1/models, /v1/chat/completions",
                },
            )
            return
        self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")])

    def do_POST(self) -> None:  # noqa: N802 - имя задано http.server
        path = self.path.split("?", 1)[0]
        if path not in ("/v1/chat/completions", "/chat/completions"):
            if path.startswith("/v1/"):
                self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")],
                           close=True)
                return
            if self._try_ui_post():
                return
            self._send(404, "application/json", [_error_body(f"нет пути {path}", code="unknown_path")],
                       close=True)
            return

        allowed, user_id, reason = self.gateway.authenticate(self.headers.get("Authorization", ""))
        if not allowed:
            self.gateway.note("чат", "—", "отказ без токена", status=401)
            self._reject_unauthorized(reason, close=True)
            return
        ok, access_reason = self.gateway.authorize(user_id)
        if not ok:
            self.gateway.note("чат", user_id, access_reason, status=403)
            self._reject_unauthorized(access_reason, status=403, close=True)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 32 * 1024 * 1024:
            self._send(
                400,
                "application/json",
                [_error_body("ожидается тело запроса (Content-Length)", code="bad_body")],
                close=True,
            )
            return

        body = self.rfile.read(length)
        try:
            requested = json.loads(body.decode("utf-8")).get("model", "")
        except (ValueError, AttributeError):
            requested = ""
        status, content_type, chunks, provider_name, used_model = self.gateway.chat(
            body, str(requested), user_id
        )
        if provider_name:
            self.log_message("%s -> %s %s", "chat/completions", provider_name, used_model)
        # Клиент видит, кто реально обслужил запрос: провайдер может
        # смениться (квота, лимит, поломка) — и это не должно быть тайной.
        extra = (
            {"x-ai-bridge-provider": provider_name, "x-ai-bridge-model": used_model}
            if provider_name
            else None
        )
        self._send(status, content_type, chunks, extra_headers=extra)


def serve(
    entries_provider: Callable[[], list[dict]],
    keys,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    timeout: float = 600.0,
    quiet: bool = False,
    ui=None,
    token: str = "",
    user_tokens=None,
    context_for_user=None,
    api_gate=None,
    access_checker=None,
    event_log=None,
    usage_store=None,
    reputation=None,
    capabilities=None,
    lists=None,
    user_dir_for=None,
    rules=None,
    autotest_store=None,
) -> int:
    """Слушает, пока не придёт Ctrl+C. Возвращает код выхода.

    ``ui`` — service.webui.WebUi: страница на том же порту, что и API.
    ``token`` — токен приставки; если задан, /v1/* требует его в заголовке.
    ``user_tokens`` + ``context_for_user`` — персональные токены людей,
    ``api_gate`` — их тумблер «доступ приложений», ``access_checker`` —
    онлайн-проверка реестра (зарегистрирован/заблокирован),
    ``event_log`` — журнал событий для страницы «Журнал»,
    ``usage_store`` — расходы моделей для страницы «Статистика»,
    ``reputation`` — штрафы и паузы моделей («репутация»),
    ``capabilities`` — выученные способности моделей (vision/tools),
    ``rules`` — правила «строка → действие» (страница «Правила»).
    Без токена служба отдаёт только OpenAI-совместимые маршруты.
    """
    gateway = Gateway(
        entries_provider, keys, timeout=timeout, token=token,
        user_tokens=user_tokens, context_for_user=context_for_user,
        api_gate=api_gate, access_checker=access_checker, event_log=event_log,
        usage_store=usage_store, reputation=reputation, capabilities=capabilities,
        lists=lists, user_dir_for=user_dir_for, rules=rules, autotest_store=autotest_store,
    )
    server = _Server((host, port), _Handler)
    server.gateway = gateway  # type: ignore[attr-defined]
    server.ui = ui  # type: ignore[attr-defined]

    gateway.start_refresh_loop()

    print(f"приставка слушает http://{host}:{port}/v1 (остановить — Ctrl+C)", flush=True)
    if ui is not None:
        print(f"страница: http://{host}:{port}/", flush=True)
    if token:
        print("для /v1/* нужен Authorization: Bearer <токен> (--token покажет)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            print("\nостановлено")
    finally:
        server.server_close()
    return 0
