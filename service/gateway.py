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
try:
    from storage.rules import ACTION_CAPABILITY, LEARN_ACTIONS, describe, match_response
except ImportError:  # раздел «Правила» удалён — заглушки
    ACTION_CAPABILITY = {}
    LEARN_ACTIONS = {}
    def describe(rule):  # type: ignore[misc]
        return ""
    def match_response(*a, **k):  # type: ignore[misc]
        return False
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

#: Музыка: её эндпоинты не отличимы от TTS — только по именам моделей.
_MUSIC_HINTS = ("lyria", "music", "stable-audio", "sonilo", "sfx", "text-to-sound")

#: Обработка звука: эндпоинт → действие в форме (value = «действие|модель»).
_VOICE_ACTIONS = {
    "/v1/audio/voice-changer": "changer",
    "/v1/audio/voice-isolator": "isolator",
    "/alpha/audio/stem-separation": "stem",
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


def _wait_for_job(client: Any, key: str, job_id: str, *, budget: float = 240.0) -> str:
    """Опрос ``/v1/jobs/{id}`` до результата; возвращает публичную ссылку.

    Jobs не удерживают соединение (202 → опрос), поэтому здесь честное
    ожидание с нарастающим интервалом; не дождались или задание упало —
    ServerError с человеческой причиной.
    """
    import time
    from urllib.parse import quote

    deadline = time.monotonic() + budget
    delay = 2.0
    while True:
        status, payload = client.fetch_json(
            f"/v1/jobs/{quote(job_id, safe='')}", api_key=key)
        data = payload if isinstance(payload, dict) else {}
        state = str(data.get("status") or "")
        if state == "succeeded":
            url = str(data.get("result_url") or "")
            if url:
                return url
            raise ServerError("задание завершилось без ссылки на результат")
        if state == "failed":
            raise ServerError(str(data.get("error") or data.get("message")
                                  or "задание не удалось"))
        if status >= 400:
            raise ServerError(f"задание недоступно: HTTP {status}")
        if time.monotonic() >= deadline:
            raise ServerError("ждали дольше 4 минут — задание ещё идёт, попробуйте позже")
        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)


def _job_or_none(response: Any) -> str:
    """202 от jobs → идентификатор задания; не jobs → пусто (sync-путь)."""
    if getattr(response, "status_code", 0) != 202:
        return ""
    try:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    except ValueError:
        return ""
    job_id = str((data or {}).get("id") or "").strip() if isinstance(data, dict) else ""
    if not job_id:
        location = str(getattr(response, "headers", {}).get("Location") or "")
        job_id = location.rsplit("/", 1)[-1].strip() if location else ""
    return job_id


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


_BUCKETS = ("image", "audio", "three", "embedding", "stt", "music", "voice")


def media_models(
    entry: dict,
    key: str,
    *,
    make_provider: Callable[..., Provider] = Provider,
) -> dict[str, list[dict]]:
    """Модели для секций теста: {image, audio, music, three, embedding, stt, voice}.

    Один поход в каталог — все списки сразу: тип читается из метаданных
    (Pollinations), секция появляется только там, где провайдер честно
    заявил модели такого типа. Музыка — по именам (её эндпоинты неотличимы
    от TTS), обработка звука — по своим эндпоинтам (в списке — действие).
    Метаданных нет или каталог не отвечает — все списки пусты.
    """
    empty: dict[str, list[dict]] = {bucket: [] for bucket in _BUCKETS}
    try:
        meta = make_provider(entry["url"], timeout=15.0).fetch_model_meta(key)
    except Exception:  # noqa: BLE001 — секции лучше исчезнут, чем задержат тест
        return empty
    out: dict[str, list[dict]] = {bucket: [] for bucket in _BUCKETS}
    for name, meta_dict in sorted(meta.items()):
        paid = bool(meta_dict.get("paid"))
        endpoints = meta_dict.get("endpoints") or []

        # Обработка звука — по своим эндпоинтам (модель несут действие).
        action = next((act for path, act in _VOICE_ACTIONS.items()
                       if path in endpoints), "")
        if action:
            out["voice"].append({"name": name, "paid": paid, "action": action})
            continue

        if _meta_fits(meta_dict, "tts"):
            lowered = name.lower()
            bucket = "music" if any(hint in lowered for hint in _MUSIC_HINTS) else "audio"
            out[bucket].append({"name": name, "paid": paid})
            continue

        for bucket, test_type in (("image", "image"), ("three", "3d"),
                                  ("embedding", "embedding"), ("stt", "stt")):
            if _meta_fits(meta_dict, test_type):
                out[bucket].append({"name": name, "paid": paid})
                break
    return out


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


def generate_audio(
    entry: dict,
    key: str,
    *,
    text: str,
    model: str = "",
    voice: str = "",
    make_provider: Callable[..., Provider] = Provider,
    timeout: float = 120.0,
    job_wait: float = 240.0,
) -> dict:
    """Озвучка через ``GET /audio/{text}`` — как в доках Pollinations.

    Возвращает ``{"text", "model", "voice", "url", "error"}``: mp3 не
    храним — страница ставит плеер по публичной ссылке из заголовка
    ``Link``. Долгие генерации (музыка) идут заданием jobs: Prefer →
    202 → опрос; провайдер без jobs отвечает сразу — путь не меняется.
    """
    text = (text or "").strip()

    def fail(note: str) -> dict:
        return {"text": text, "model": model, "voice": voice, "url": "", "error": note}

    if not text:
        return fail("текст не задан")
    if not key:
        return fail("нужен ключ — введите его кнопкой «ключ»")

    client = make_provider(entry["url"], timeout=timeout)
    try:
        response = client.media_get(
            "audio", text, model, api_key=key,
            params={"voice": voice} if voice else None,
            prefer_async=True,
        )
    except NeedsKeyError:
        return fail("нужен ключ — введите его кнопкой «ключ»")
    except ServerError as exc:
        return fail(str(exc))

    try:
        job_id = _job_or_none(response)
        if job_id:
            try:
                url = _wait_for_job(client, key, job_id, budget=job_wait)
            except ServerError as exc:
                return fail(str(exc))
            return {"text": text, "model": model, "voice": voice,
                    "url": url, "error": ""}
        if response.status_code >= 400:
            detail = _read_capped(response, 64 * 1024).decode("utf-8", errors="replace")
            return fail(f"HTTP {response.status_code}: {detail[:300]}".strip())
        link = _enclosure(str(response.headers.get("Link") or ""))
        if link:
            return {"text": text, "model": model, "voice": voice,
                    "url": link, "error": ""}
        if not _read_capped(response, 64 * 1024):
            return fail("пустой ответ озвучки")
        return fail("звук пошёл, но ссылка не пришла — показать нечего")
    finally:
        response.close()


def generate_3d(
    entry: dict,
    key: str,
    *,
    image: str,
    model: str,
    resolution: str = "low",
    prompt: str = "",
    make_provider: Callable[..., Provider] = Provider,
    timeout: float = 180.0,
    job_wait: float = 240.0,
) -> dict:
    """3D из картинки: ``GET /3d/{prompt}?image=…&model=…&resolution=…``.

    Возвращает ``{"model", "image", "url", "error"}`` — glb не храним,
    страница даёт ссылку «скачать». Картинка обязана быть http(s)-ссылкой
    (так в доках): чужие схемы не пропускаем. Долгая генерация идёт
    заданием jobs (Prefer → 202 → опрос), без jobs — обычный синхронный
    ответ.
    """
    image = (image or "").strip()
    model = (model or "").strip()
    resolution = (resolution or "low").strip()

    def fail(note: str) -> dict:
        return {"model": model, "image": image, "url": "", "error": note}

    if not image:
        return fail("нужна ссылка на картинку (image) — так в доках")
    if not image.startswith(("http://", "https://")):
        return fail("картинкой может быть только http(s)-ссылка")
    if not model:
        return fail("модель не выбрана")
    if not key:
        return fail("нужен ключ — введите его кнопкой «ключ»")

    client = make_provider(entry["url"], timeout=timeout)
    try:
        response = client.media_get(
            "3d", prompt or "trellis", model, api_key=key,
            params={"image": image, "resolution": resolution},
            prefer_async=True,
        )
    except NeedsKeyError:
        return fail("нужен ключ — введите его кнопкой «ключ»")
    except ServerError as exc:
        return fail(str(exc))

    try:
        job_id = _job_or_none(response)
        if job_id:
            try:
                url = _wait_for_job(client, key, job_id, budget=job_wait)
            except ServerError as exc:
                return fail(str(exc))
            return {"model": model, "image": image, "url": url, "error": ""}
        if response.status_code >= 400:
            detail = _read_capped(response, 64 * 1024).decode("utf-8", errors="replace")
            return fail(f"HTTP {response.status_code}: {detail[:300]}".strip())
        link = _enclosure(str(response.headers.get("Link") or ""))
        if link:
            return {"model": model, "image": image, "url": link, "error": ""}
        if not _read_capped(response, 64 * 1024):
            return fail("пустой ответ генерации")
        return fail("модель построена, но ссылка не пришла — скачать нечего")
    finally:
        response.close()


def embed_text(
    entry: dict,
    key: str,
    *,
    text: str,
    model: str,
    make_provider: Callable[..., Provider] = Provider,
    timeout: float = 60.0,
) -> dict:
    """Вектор текста: ``POST /v1/embeddings`` (OpenAI-совместимый).

    Возвращает ``{"text", "model", "count", "sample", "error"}``:
    в целях страницы достаточно длины и первых чисел — сам вектор
    (тысячи чисел) в ответ не тащим.
    """
    text = (text or "").strip()
    model = (model or "").strip()

    def fail(note: str) -> dict:
        return {"text": text, "model": model, "count": 0, "sample": "",
                "error": note}

    if not text:
        return fail("текст не задан")
    if not model:
        return fail("модель не выбрана")
    if not key:
        return fail("нужен ключ — введите его кнопкой «ключ»")

    payload = json.dumps({"model": model, "input": text},
                         ensure_ascii=False).encode("utf-8")
    try:
        response = make_provider(entry["url"], timeout=timeout).forward_path(
            "/embeddings", payload, api_key=key, timeout=timeout, stream=False,
        )
    except NeedsKeyError:
        return fail("нужен ключ — введите его кнопкой «ключ»")
    except ServerError as exc:
        return fail(str(exc))

    if response.status_code >= 400:
        detail = response.read().decode("utf-8", errors="replace")
        return fail(f"HTTP {response.status_code}: {detail[:300]}".strip())

    try:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    except ValueError:
        return fail("провайдер ответил не-JSON на вектор")
    items = data.get("data") if isinstance(data, dict) else None
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    vector = item.get("embedding")
    if not isinstance(vector, list) or not vector:
        return fail("в ответе нет вектора")

    def piece(value: object) -> str:
        try:
            return f"{float(value):.4g}"  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return str(value)[:12]

    return {
        "text": text, "model": model, "count": len(vector),
        "sample": ", ".join(piece(value) for value in vector[:12]),
        "error": "",
    }


def process_audio(
    entry: dict,
    key: str,
    *,
    action: str,
    model: str,
    audio: bytes | None = None,
    audio_name: str = "sample.wav",
    mime: str = "application/octet-stream",
    voice: str = "",
    make_provider: Callable[..., Provider] = Provider,
    timeout: float = 120.0,
) -> dict:
    """Обработка звука: смена голоса, очистка от шума, разделение дорожек.

    ``action`` — ``changer``/``isolator``/``stem`` → свой эндпоинт; вход —
    multipart с файлом. Возвращает ``{"action", "model", "audio_name",
    "url", "error"}``: результат показывается плеером по публичной ссылке
    (``Link``), байты не храним.
    """
    model = (model or "").strip()
    voice = voice.strip()
    paths = {
        "changer": "/audio/voice-changer",
        "isolator": "/audio/voice-isolator",
        "stem": "/alpha/audio/stem-separation",
    }

    def fail(note: str) -> dict:
        return {"action": action, "model": model, "audio_name": audio_name,
                "url": "", "error": note}

    if action not in paths:
        return fail("неизвестное действие — действие не выбрано")
    if not audio:
        return fail("нет аудио для обработки")
    if not model:
        return fail("модель не выбрана")
    if not key:
        return fail("нужен ключ — введите его кнопкой «ключ»")

    data = {"model": model}
    if voice:
        data["voice"] = voice
    try:
        # stem-эндпоинт живёт в корне (/alpha/…), остальные — под /v1.
        response = make_provider(entry["url"], timeout=timeout).forward_form(
            paths[action], data, {"file": (audio_name, audio, mime)},
            api_key=key, from_root=(action == "stem"),
        )
    except NeedsKeyError:
        return fail("нужен ключ — введите его кнопкой «ключ»")
    except ServerError as exc:
        return fail(str(exc))

    if response.status_code >= 400:
        detail = response.read().decode("utf-8", errors="replace")
        return fail(f"HTTP {response.status_code}: {detail[:300]}".strip())

    link = _enclosure(str(response.headers.get("Link") or ""))
    if link:
        return {"action": action, "model": model, "audio_name": audio_name,
                "url": link, "error": ""}
    # Запасной формат: JSON с прямой ссылкой (если провайдер вернул её так).
    raw = _read_capped(response, 1024 * 1024)
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
        items = parsed.get("data") if isinstance(parsed, dict) else None
        item = items[0] if isinstance(items, list) and items else {}
        url = str((item or {}).get("url")
                  or (parsed.get("url") if isinstance(parsed, dict) else "")
                  or "").strip()
    except (ValueError, AttributeError):
        url = ""
    if url.startswith(("http://", "https://")):
        return {"action": action, "model": model, "audio_name": audio_name,
                "url": url, "error": ""}
    if not raw:
        return fail("пустой ответ обработки")
    return fail("звук обработан, но ссылка не пришла — показать нечего")


def generate_transcript(
    entry: dict,
    key: str,
    *,
    model: str,
    audio: bytes | None = None,
    audio_name: str = "sample.wav",
    mime: str = "application/octet-stream",
    make_provider: Callable[..., Provider] = Provider,
    timeout: float = 120.0,
) -> dict:
    """Распознавание звука: multipart ``POST /v1/audio/transcriptions``.

    Возвращает ``{"model", "audio_name", "text", "error"}``: файл обязателен
    (страница подставляет тестовый тон, когда своего нет — иначе тест
    бессмысленен).
    """
    model = (model or "").strip()

    def fail(note: str) -> dict:
        return {"model": model, "audio_name": audio_name, "text": "",
                "error": note}

    if not audio:
        return fail("нет аудио для распознавания")
    if not model:
        return fail("модель не выбрана")
    if not key:
        return fail("нужен ключ — введите его кнопкой «ключ»")

    try:
        response = make_provider(entry["url"], timeout=timeout).forward_form(
            "/audio/transcriptions", {"model": model},
            {"file": (audio_name, audio, mime)},
            api_key=key,
        )
    except NeedsKeyError:
        return fail("нужен ключ — введите его кнопкой «ключ»")
    except ServerError as exc:
        return fail(str(exc))

    if response.status_code >= 400:
        detail = response.read().decode("utf-8", errors="replace")
        return fail(f"HTTP {response.status_code}: {detail[:300]}".strip())

    try:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    except ValueError:
        return fail("провайдер ответил не-JSON на распознавание")
    if isinstance(data, dict) and "text" in data:
        text = str(data.get("text") or "").strip()
        return {"model": model, "audio_name": audio_name,
                "text": text or "(пусто)", "error": ""}
    return fail("в ответе нет текста (нет поля text)")


def test_chat(
    entry: dict,
    key: str,
    *,
    make_provider: Callable[..., Provider] = Provider,
    question: str = DEFAULT_QUESTION,
    image_url: str = "",
    model: str = "",
    timeout: float = 60.0,
) -> dict:
    """Небольшой тестовый чат: настоящий запрос с крошечным лимитом.

    Возвращает {"question", "answer", "error"}: проверяет не только
    список моделей, а что ключ принят и модель отвечает. На платной
    модели это доли цента — на странице об этом сказано честно.
    ``image_url`` — картинка в вопросе: её ждут vision-модели и
    агенты-генераторы вроде 3D-для-Blender («картинка → ссылку»).
    ``model`` — пусто = настроенная модель; непусто = любая модель из
    каталога (список на странице теста).
    """
    image_url = (image_url or "").strip()
    model = (model or "").strip()
    content: object = question
    if image_url:
        content = [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    payload = json.dumps(
        {
            "model": model or entry.get("model") or "",
            "messages": [{"role": "user", "content": content}],
            # Без лимита по токенам: ответ всегда полный, как у клиента.
            "temperature": 0,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    def fail(text: str) -> dict:
        return {"question": question, "answer": "", "error": text}

    if not (model or entry.get("model")):
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
        verify_store=None,  # storage.verify.VerifyStore | None — «Проверка»
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
        self._verify_store = verify_store
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

    def _learn(
        self, entry: dict, model: str, capability: str, ok: bool, reason: str = "",
        *, req: str = "", resp: str = "",
    ) -> None:
        """Обучение способностей отключено: списки ведёт админ руками.

        Метод оставлен как пустышка, чтобы вызовы не ломались; ничего
        не записывает и не решает при маршрутизации.
        """
        return

    def _grey_of(self, user_id: str) -> dict:
        """Серый список человека: пусто, если каталога нет."""
        if not self._user_dir_for or not user_id:
            return {}
        directory = self._user_dir_for(user_id)
        if directory is None:
            return {}
        return ModelLists(directory).grey()

    def _skip_sleeping(self, entries: list[dict], user_id: str) -> list[dict]:
        """Убрать записи спящих провайдеров (внутренний серый список).

        Серый список — внутренняя механика: провайдер, отвечавший мягким
        отказом (лимит/кредиты), спит до срока и не получает запросов.
        Не выставляется как кодовое слово — только тихо пропускается.
        """
        if not self._user_dir_for or not user_id:
            return entries
        try:
            grey = self._grey_of(user_id)
        except Exception:  # noqa: BLE001 — серый не должен ломать чат
            return entries
        if not grey:
            return entries
        now = datetime.now(timezone.utc)
        sleeping = {
            provider_id for provider_id, cell in grey.items()
            if isinstance(cell, dict) and cell.get("until") and cell["until"] > now
        }
        if not sleeping:
            return entries
        return [entry for entry in entries if entry["id"] not in sleeping]

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

            if action in ("grey", "black"):
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → переключение (списки не тронуты)",
                          **fields)
                leave = True
            elif action == "white":
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → списки не тронуты",
                          **fields)
            elif action == "untouched":
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → списки не тронуты",
                          **fields)
            elif action in LEARN_ACTIONS:
                capability, ok = LEARN_ACTIONS[action]
                verb = "умеет" if ok else "не умеет"
                word = "картинки" if capability == "vision" else "инструменты"
                self.note("правило", user_id or "—",
                          f"{entry['name']}: {hit} → «{verb} {word}» (списки не тронуты)",
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

    def _code_lists(self) -> dict[str, list[str]]:
        """Кодовые слова → список моделей по порядку. Пусто без списков."""
        if self._lists is None:
            return {}
        try:
            out: dict[str, list[str]] = {}
            for name in self._lists.code_names():
                models = self._lists.code_models(name)
                if models:
                    out[name] = models
            return out
        except Exception:  # noqa: BLE001 — списки не должны ломать чат
            return {}

    def models_payload(self, user_id: str = "") -> dict:
        """Список кодовых слов (имён списков), которые клиент видит как модели.

        Пустой список кодовых слов — отдаём пусто: админ создаёт списки сам.
        """
        codes = self._code_lists()
        if not codes:
            return _NO_MODELS
        data = [{"id": name, "owned_by": "list"} for name in sorted(codes)]
        return {"object": "list", "data": data}

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

    def run_verify(
        self,
        provider_specs: list[dict],
        system_prompt: str,
        user_prompt: str,
        images: list[str],
        *,
        record: bool = False,
    ) -> dict:
        """Проверка: несколько провайдеров × несколько картинок.

        Каждая картинка — отдельный запрос (vision), ответ и время
        фиксируются в строке результата. Чистый отчёт: способности и
        списки не меняются, даже если ``record=True`` (пользователь
        выбрал «чисто отчёт»).
        """
        import time

        system_prompt = (system_prompt or "").strip()
        user_prompt = (user_prompt or "").strip()
        images = [str(x) for x in (images or []) if str(x).strip()]
        if not user_prompt:
            return {"provider": "", "results": [], "error": "промпт пользователя не задан"}
        if not images:
            return {"provider": "", "results": [], "error": "нет картинок"}
        if not provider_specs:
            return {"provider": "", "results": [], "error": "не выбрано ни одного провайдера"}

        entries = self._entries_provider()
        by_id = {str(e.get("id") or ""): e for e in entries}

        # План: провайдер × картинка.
        plan: list[tuple[str, str, int]] = []
        results: list[dict] = []
        for spec in provider_specs:
            pid = str(spec.get("id") or "").strip()
            model = str(spec.get("model") or "").strip()
            entry = by_id.get(pid)
            if entry is None:
                for index in range(len(images)):
                    results.append(self._verify_fail(pid, model, index, "провайдер не найден"))
                continue
            for index in range(len(images)):
                plan.append((pid, model, index))

        total = len(plan)
        if self._verify_store is not None:
            self._verify_store.plan(total)

        stopped = False
        for pid, model, index in plan:
            if self._verify_store is not None and self._verify_store.stop_requested():
                stopped = True
                break
            entry = by_id.get(pid)
            if entry is None:
                continue
            model = model or str(entry.get("model") or "").strip()
            if self._verify_store is not None:
                self._verify_store.tick(entry.get("name") or pid, f"картинка {index + 1}")
            result = self._run_one_verify(entry, model, system_prompt, user_prompt,
                                         images[index], index)
            results.append(result)
            if self._verify_store is not None:
                self._verify_store.record(result)
                self._verify_store.advance()

        provider_list = ", ".join(str(s.get("id") or "") for s in provider_specs)
        if self._verify_store is not None:
            self._verify_store.finish(
                provider_list, results, prompt=user_prompt,
                system_prompt=system_prompt, images=images,
            )

        if stopped:
            return {"provider": provider_list, "results": results,
                    "prompt": user_prompt, "system_prompt": system_prompt,
                    "images": images, "stopped": True}
        return {"provider": provider_list, "results": results,
                "prompt": user_prompt, "system_prompt": system_prompt,
                "images": images}

    def _verify_fail(self, provider_id: str, model: str, image_index: int,
                     text: str) -> dict:
        return {
            "provider": provider_id,
            "model": model,
            "image_index": image_index,
            "ts": _now_iso(),
            "status": "fail",
            "http_status": 0,
            "response": text,
            "duration": 0.0,
            "req": "",
            "resp": "",
        }

    def _run_one_verify(self, entry: dict, model: str, system_prompt: str,
                        user_prompt: str, image: str, image_index: int) -> dict:
        import time

        key = self._keys.get(entry["id"])
        if not model:
            return self._verify_fail(str(entry.get("id") or ""), model, image_index,
                                     "у провайдера не выбрана модель")
        if not key:
            return self._verify_fail(str(entry.get("id") or ""), model, image_index,
                                     "нужен ключ — введите его кнопкой «ключ»")

        body: dict = {"model": model, "messages": []}
        messages: list = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": [
            {"type": "text", "text": user_prompt},
            {"type": "image_url", "image_url": {"url": image}},
        ]})
        body["messages"] = messages
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_text = body_bytes.decode("utf-8", errors="replace")

        client = self._provider_factory(entry["url"], timeout=60.0)
        start = time.monotonic()
        try:
            response = client.forward_chat(body_bytes, api_key=key, timeout=60.0, stream=False)
            elapsed = round(time.monotonic() - start, 1)
            raw = response.read() if hasattr(response, "read") else b""
            resp_text = raw.decode("utf-8", errors="replace")
            status_code = getattr(response, "status_code", 0)
            if status_code >= 400:
                result = {
                    "provider": str(entry.get("id") or ""),
                    "model": model,
                    "image_index": image_index,
                    "ts": _now_iso(),
                    "status": "fail",
                    "http_status": status_code,
                    "response": f"HTTP {status_code}: {resp_text}",
                    "duration": elapsed,
                    "req": req_text,
                    "resp": resp_text,
                }
            else:
                try:
                    data = json.loads(resp_text) if resp_text else {}
                except ValueError:
                    data = {}
                content = ""
                if isinstance(data, dict) and "choices" in data and data["choices"]:
                    msg = data["choices"][0].get("message", {}) or {}
                    content = msg.get("content") or ""
                elif isinstance(data, dict) and data.get("error"):
                    content = str(data["error"])
                else:
                    content = resp_text
                result = {
                    "provider": str(entry.get("id") or ""),
                    "model": model,
                    "image_index": image_index,
                    "ts": _now_iso(),
                    "status": "ok",
                    "http_status": status_code,
                    "response": content,
                    "duration": elapsed,
                    "req": req_text,
                    "resp": resp_text,
                }
            response.close()
            return result
        except Exception as exc:  # noqa: BLE001
            elapsed = round(time.monotonic() - start, 1)
            return {
                "provider": str(entry.get("id") or ""),
                "model": model,
                "image_index": image_index,
                "ts": _now_iso(),
                "status": "error",
                "http_status": 0,
                "response": str(exc),
                "duration": elapsed,
                "req": req_text,
                "resp": "",
            }

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
        entries = entries_provider()
        needs = _needs_of(body, self._rules)
        # Серый список (внутренний) — пропускаем спящих провайдеров.
        entries = self._skip_sleeping(entries, user_id)
        attempts = route(entries, requested_model, needs=needs,
                         code_lists=self._code_lists())
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
            if available:
                message += f"; доступны: {available}"
            else:
                message += "; ни один провайдер не включён"
            self.note("чат", user_id or "—", f"нет маршрута: {message[:150]}", **body_fields)
            return 502, "application/json", [_error_body(message, code="model_not_available")], "", ""

        by_id = {entry["id"]: entry for entry in entries}
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
                # Живой ответ: ничего не решаем автоматически — списки
                # ведёт админ руками через кодовые слова.
                pass
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

        message = "ни один провайдер не ответил"
        if problems:
            # Все попытки с причинами: диагностика в одну строку.
            message += ": " + "; ".join(problems[:6])
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
        origin = self.headers.get("Origin", "")
        allowed = origin in ("https://aistudio.devhorizon.online", "https://docs-chat.devhorizon.online")
        if allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
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

    def do_OPTIONS(self) -> None:  # noqa: N802 - имя задано http.server
        origin = self.headers.get("Origin", "")
        allowed = origin in ("https://aistudio.devhorizon.online", "https://docs-chat.devhorizon.online")
        if allowed:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(403)
            self.end_headers()
        return

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
    verify_store=None,
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
        verify_store=verify_store,
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
