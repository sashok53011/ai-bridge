"""GitHub Copilot: вход через device-flow и обмен токена на токен API.

Copilot принимает не любой GitHub-токен: классический ``ghp_`` отвергается,
а рабочий ``gho_``/``github_pat_`` нужно обменять на короткоживущий токен
API — иначе и список моделей, и чат отвечают 401. Обмен идёт на
``api.github.com/copilot_internal/v2/token`` (как делает VS Code) и
кэшируется в памяти до истечения срока.

Вход без вставки ключа — device-flow GitHub (как ``copilot login``):
приставка показывает одноразовый код, человек вводит его на
``github.com/login/device``, GitHub выдаёт токен — и он остаётся
на устройстве (storage/keys), как любой другой ключ.

Все ошибки здесь — человекочитаемые причины: страница покажет их как
есть, а не «HTTP 400».
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

#: GitHub App VS Code — его токены Copilot API принимает после обмена.
CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"

#: Шапки запросов — как у VS Code/Copilot CLI (без них API может отказать).
EDITOR_VERSION = "vscode/1.104.1"
EXCHANGE_USER_AGENT = "GitHubCopilotChat/0.26.7"
API_USER_AGENT = "AI-Bridge/1.0"
INTEGRATION_ID = "vscode-chat"

#: Токены, которые Copilot API принимает после обмена.
SUPPORTED_PREFIXES = ("gho_", "github_pat_", "ghu_")

#: Официальный список utility-семейств Copilot (docs.github.com, раздел
#: «Utility models»): на бесплатных тарифах API принимает только их —
#: остальные именные модели отвечает 400 model_not_supported. Семейство
#: покрывает и датированные версии (gpt-4o-2024-11-20 — то же gpt-4o).
UTILITY_FAMILIES = frozenset({"gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-5.4-nano"})

#: sku тарифа последнего обмена ("free_limited_copilot" и т.п.).
#: Пусто — данных о тарифе нет, фильтр по семействам не применяется.
#: Судьба одного обмена: две учётки Copilot на одной приставке будут
#: делить это значение (на практике аккаунт один).
_last_sku = ""

#: Обновлять обменённый токен за две минуты до истечения.
_REFRESH_MARGIN = 120.0
#: Обычный срок жизни обменённого токена, если сервер его не назвал.
_DEFAULT_TTL = 1800.0


class CopilotError(Exception):
    """Причина, по которой вход или обмен токеном не состоялся."""


def is_copilot_url(url: object) -> bool:
    """Адрес принадлежит GitHub Copilot (ему нужен обмен токена)."""
    text = str(url or "").strip().lower()
    return "githubcopilot.com" in text


def start_device_flow(*, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Начать device-flow: (device_code, user_code, ссылка, интервал).

    Возвращает поля ответа GitHub или ``{"error": причину}`` — страница
    покажет причину как есть, без HTTP-кодов.
    """
    try:
        with httpx.Client(timeout=15.0, transport=transport) as client:
            response = client.post(
                DEVICE_CODE_URL,
                data={"client_id": CLIENT_ID, "scope": "read:user"},
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        return {"error": f"нет связи с GitHub: {exc}"}
    if response.status_code >= 400:
        return {"error": f"GitHub отказал в коде входа (HTTP {response.status_code})"}
    try:
        body = response.json()
    except ValueError:
        return {"error": "GitHub ответил не-JSON на запрос кода входа"}
    device_code = str(body.get("device_code") or "")
    user_code = str(body.get("user_code") or "")
    if not device_code or not user_code:
        return {"error": "GitHub не выдал код входа"}
    try:
        interval = max(int(body.get("interval") or 5), 1)
    except (TypeError, ValueError):
        interval = 5
    try:
        expires_in = int(body.get("expires_in") or 900)
    except (TypeError, ValueError):
        expires_in = 900
    return {
        "device_code": device_code,
        "user_code": user_code,
        "url": str(body.get("verification_uri") or "https://github.com/login/device"),
        "interval": interval,
        "expires_in": expires_in,
    }


def poll_device_flow(
    device_code: str, *, transport: httpx.BaseTransport | None = None
) -> tuple[str, str]:
    """Один опрос GitHub: («done»|«pending»|«error», токен или причина).

    «pending» — человек ещё не ввёл код (или GitHub просит подождать);
    «error» — дальше опрашивать бессмысленно (истёк, отклонён, сеть).
    """
    if not device_code:
        return "error", "вход не начат"
    try:
        with httpx.Client(timeout=15.0, transport=transport) as client:
            response = client.post(
                TOKEN_URL,
                data={
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        return "error", f"нет связи с GitHub: {exc}"
    if response.status_code >= 400:
        return "error", f"GitHub ответил отказом (HTTP {response.status_code})"
    try:
        body = response.json()
    except ValueError:
        return "error", "GitHub ответил не-JSON на проверку кода"
    token = str(body.get("access_token") or "")
    if token:
        return "done", token
    error = str(body.get("error") or "")
    if error == "slow_down":
        # RFC 8628: опрашивайте реже — вызывающий увеличит интервал.
        return "pending", "slow_down"
    if error == "authorization_pending":
        return "pending", ""
    if error == "expired_token":
        return "error", "код истёк — начните вход заново"
    if error == "access_denied":
        return "error", "вы отклонили вход"
    return "error", error or "GitHub не выдал токен"


#: Обменённые токены в памяти процесса: отпечаток → (токен, срок годности).
_exchange_cache: dict[str, tuple[str, float]] = {}


def _fingerprint(raw_token: str) -> str:
    """Короткий отпечаток сырого токена: ключ кэша без хранения секрета."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()[:16]


def exchange(
    raw_token: str,
    *,
    client: httpx.Client | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """GitHub-токен → токен Copilot API (кэшируется до истечения срока).

    ``client`` — готовый клиент (его MockTransport видит и обмен, и сам
    провайдер в тестах); без него клиент создаётся на ``transport``.
    ``ghp_`` отвергается сразу с понятной причиной — обмен всё равно
    не пройдёт.
    """
    raw = raw_token.strip()
    if not raw:
        raise CopilotError("пустой ключ — обменять нечего")
    if raw.startswith("ghp_"):
        raise CopilotError(
            "GitHub отверг классический токен ghp_ — Copilot его не принимает; "
            "войте через GitHub или возьмите github_pat_"
        )
    if not raw.startswith(SUPPORTED_PREFIXES):
        # Не наш формат (например, уже обменённый токен) — не трогаем.
        return raw

    fingerprint = _fingerprint(raw)
    cached = _exchange_cache.get(fingerprint)
    now = time.time()
    if cached and now < cached[1] - _REFRESH_MARGIN:
        return cached[0]

    request_headers = {
        "Authorization": f"token {raw}",
        "User-Agent": EXCHANGE_USER_AGENT,
        "Accept": "application/json",
        "Editor-Version": EDITOR_VERSION,
    }
    if client is not None:
        response = _get_exchange(client, request_headers)
    else:
        with httpx.Client(timeout=15.0, transport=transport) as own:
            response = _get_exchange(own, request_headers)

    if response.status_code >= 400:
        detail = _error_message(response)
        raise CopilotError(f"GitHub не принял ключ для обмена: {detail}")
    try:
        body = response.json()
    except ValueError as exc:
        raise CopilotError("GitHub ответил не-JSON на обмен токена") from exc
    token = str(body.get("token") or "")
    if not token:
        raise CopilotError("GitHub вернул пустой токен при обмене")
    # Тариф аккаунта: от него зависит, какие модели API вообще принимает.
    global _last_sku
    _last_sku = str(body.get("sku") or "")
    try:
        expires_at = float(body.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    if expires_at <= now:
        expires_at = now + _DEFAULT_TTL
    _exchange_cache[fingerprint] = (token, expires_at)
    return token


def _get_exchange(client: httpx.Client, headers: dict[str, str]) -> httpx.Response:
    """Запрос обмена; таймаут короткий: обмен — не повод висеть."""
    try:
        return client.get(EXCHANGE_URL, headers=headers, timeout=15.0)
    except httpx.HTTPError as exc:
        raise CopilotError(f"нет связи с GitHub при обмене токена: {exc}") from exc


def _error_message(response: httpx.Response) -> str:
    """Причина отказа: message из тела важнее голого статуса."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        message = str(body.get("message") or "").strip()
        if message:
            return f"{message} (HTTP {response.status_code})"
    return f"HTTP {response.status_code}"


def auth_headers(
    raw_token: str,
    *,
    client: httpx.Client | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, str]:
    """Заголовки запроса к Copilot API: обменённый Bearer + шапки VS Code."""
    if not raw_token:
        return {}
    token = exchange(raw_token, client=client, transport=transport)
    return {
        "Authorization": f"Bearer {token}",
        "Copilot-Integration-Id": INTEGRATION_ID,
        "Editor-Version": EDITOR_VERSION,
        "User-Agent": API_USER_AGENT,
    }


def usable_models(payload: object) -> list[str]:
    """Какие модели из /models реально принимает API Copilot.

    GitHub отдаёт в ``/models`` **весь каталог** независимо от тарифа —
    на Copilot Free это 48 моделей, из которых API принимает только
    utility-семейства (400 model_not_supported на остальных). Поэтому
    список режем по трём честным признакам:

      1. ``capabilities.type != "chat"`` — эмбеддинги чатить не умеют;
      2. ``supported_endpoints`` задан, но без ``/chat/completions`` —
         приставка говорит только этим протоколом, «один /responses»
         нам не помощник;
      3. на бесплатном тарифе (sku начинается с "free") — только
         utility-семейства: остальные модели тарифу не доступны.

    ``policy.state=disabled`` не фильтруем: на бесплатном тарифе так
    помечены даже те claude/gemini, которые всё равно отвалит правило 3,
    а смысл правила «выключил админ организации» становится доступен
    только на платном тарифе — там его применение изменило бы список
    в обе стороны без наших данных. Пустой или не-JSON payload — пустой
    список (провайдер не ответил(list) — пустота честнее каши).
    """
    if isinstance(payload, dict):
        items = payload.get("data")
    elif isinstance(payload, list):
        items = payload
    else:
        items = None
    if not isinstance(items, list):
        return []

    free_tariff = _last_sku.startswith("free")
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or item.get("name") or "").strip()
        if not name:
            continue
        caps = item.get("capabilities")
        caps = caps if isinstance(caps, dict) else {}
        if str(caps.get("type") or "chat") != "chat":
            continue
        endpoints = item.get("supported_endpoints")
        if isinstance(endpoints, list) and endpoints:
            if "/chat/completions" not in [str(e) for e in endpoints]:
                continue
        if free_tariff:
            family = str(caps.get("family") or "")
            if family not in UTILITY_FAMILIES:
                continue
        names.append(name)
    return sorted(set(names))


def explain_error(status: int, body: str, *, retry_after: str = "") -> str:
    """Причина отказа Copilot по-человечески; "" — случай не наш.

    400 model_not_supported на бесплатном тарифе — самая частая ловушка:
    каталог /models шире тарифа, и человек получает одинаковый JSON на
    любую «премиальную» модель. 429 — utility-лимит бесплатного тарифа,
    живёт десятки минут (retry-after из заголовка, если передали).
    """
    text = body or ""
    if "model_not_supported" in text or "The requested model is not supported" in text:
        if _last_sku.startswith("free"):
            return (
                "Copilot не принимает эту модель на вашем тарифе (бесплатный): "
                "из каталога /models доступны только utility-модели — "
                "gpt-4o-mini, gpt-4o, gpt-4.1. Выберите одну из них."
            )
        return (
            "Copilot не принимает эту модель — возможно, её вывели из списка "
            "или имя устарело. Обновите список моделей и выберите другую."
        )
    if status == 429 and ("rate limit" in text or "exceeded" in text.lower()):
        wait = ""
        try:
            seconds = int(str(retry_after).strip())
            if seconds > 0:
                wait = f" — попробуйте примерно через {max(1, (seconds + 59) // 60)} мин"
        except ValueError:
            pass
        return (
            "Лимит utility-моделей Copilot исчерпан (бесплатный тариф ограничивает "
            f"частоту){wait}."
        )
    return ""
