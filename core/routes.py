"""Маршрутизация приставки: кому достаётся запрос и в каком порядке.

Приставка экспонирует **настроенные модели** включённых провайдеров.
Если клиент назвал модель — сначала идём к провайдерам, у которых она
настроена; не назвал — берём первый по приоритету. Отказ провайдера
(сеть, 401/402/403/429/404, 5xx) уводит запрос к следующему включённому;
4xx (кроме 404) — это сам запрос плох, смена провайдера не поможет.

**Названная модель не приговор**: если все, у кого она есть, отказали,
приставка продолжает по приоритету другими включёнными моделями —
пустой ответ хуже чужого. Кто в итоге ответил, видно в заголовках
`x-ai-bridge-provider/model` и в журнале. Запрос с картинками или
инструментами получает последний шанс только от моделей со способностями.

Имя модели можно уточнить провайдером: «ollama/qwen3» — к ollama
с моделью qwen3, даже если она не настроена в каталоге; префикс решает
только порядок (свой провайдер первым), а не запрет на переключение.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from core.models import offers_model

#: На такие ответы провайдера стоит переключаться дальше. 401/403 —
#: ключ провайдера X не годится, но у Y он другой и может быть валидным;
#: 429 — лимит или исчерпанная квота (OpenAI так отвечает и за то, и за
#: другое), 402 — пустой баланс у провайдеров вроде OpenRouter,
#: 404 — обычно «модель неизвестна этому провайдеру», смена безвредна;
#: остальные 4xx — вина клиента, и приставка честно возвращает их как есть.
FALLBACK_STATUSES = (401, 402, 403, 404, 429, 500, 502, 503, 504)


def is_local_url(url: str) -> bool:
    """Локальный ли адрес провайдера (своя сеть, не интернет).

    У локальных моделей (Ollama, LM Studio) первый запрос может ждать,
    пока модель загрузится в память, — до пяти минут; для таких
    провайдеров таймаут чата берётся длиннее.
    """
    host = (urlparse(str(url or "")).hostname or "").strip().lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def enabled(entries: list[dict]) -> list[dict]:
    """Включённые провайдеры: уровни списков, совпадение группы, приоритет.

    Уровень (`_tier`) ставит гейтвей по спискам: 1 — белый, 2 —
    неизвестный, 3 — проснувшийся серый, 4 — чёрный (последний шанс).
    Совпадение (`_fit`) — насколько группа модели подходит именно этому
    запросу: 0 — точное (текст → текстовая группа, картинка → vision,
    инструменты → tools), 1 — надмножество, 2 — нужного нет. Без списков
    обе пометки0 — прежний порядок (приоритет, имя) не меняется.
    """
    return sorted(
        (entry for entry in entries if entry.get("enabled")),
        key=lambda entry: (
            int(entry.get("_tier") or 0),
            int(entry.get("_fit") or 0),
            int(entry.get("fallbackPriority") or 0),
            entry.get("name") or entry["id"],
        ),
    )


def request_needs(data: object) -> tuple[str, ...]:
    """Что просит запрос: ("vision",) и/или ("tools",); пусто — ничего особого.

    Распознаём стандартные признаки OpenAI-формата: сообщения с картинками
    (image_url / input_image) и инструменты (tools / tool_choice). Требование
    сохраняется при переключении провайдеров: кормить картинкой модель
    без vision — пустая трата времени.
    """
    if not isinstance(data, dict):
        return ()
    needs: list[str] = []
    if _asks_for_images(data):
        needs.append("vision")
    if _asks_for_tools(data):
        needs.append("tools")
    return tuple(needs)


def _asks_for_images(data: dict) -> bool:
    messages = data.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and str(part.get("type") or "") in (
                    "image_url",
                    "input_image",
                ):
                    return True
    return False


def _asks_for_tools(data: dict) -> bool:
    tools = data.get("tools")
    if isinstance(tools, list) and tools:
        return True
    return bool(data.get("tool_choice"))


def _capable(entry: dict, needs: tuple[str, ...]) -> bool:
    """Есть ли у записи все нужные способности (текст «Способности»)."""
    caps = str(entry.get("capabilities") or "").lower()
    return all(word in caps for word in needs)


def _refused_for(entry: dict, needs: tuple[str, ...]) -> bool:
    """Доказала ли модель отказ от нужного (а не «просто не заявила»).

    Разница ключевая: незаявленную названную модель пробуем (вдруг умеет),
    а доказавшую отказ — не ставим первой в цепочку. Отказ накапливает
    гейтвей в `_refused` из обученной памяти (capabilities.json).
    """
    refused = entry.get("_refused") or set()
    return any(need in refused for need in needs)


def advertised_models(entries: list[dict]) -> list[dict]:
    """Модели, которые приставка предлагает клиентам.

    Это настроенные модели включённых провайдеров; при совпадении моделей
    побеждает провайдер с более высоким приоритетом.
    """
    seen: dict[str, str] = {}
    for entry in enabled(entries):
        model = str(entry.get("model") or "").strip()
        if model and model not in seen:
            seen[model] = entry["id"]
    return [{"id": model, "owned_by": provider_id} for model, provider_id in seen.items()]


def route(entries: list[dict], requested: str = "", needs: tuple[str, ...] = ()) -> list[dict]:
    """Цепочка попыток для запроса: [{providerId, model}, ...].

    ``needs`` — требования самого запроса (vision/tools): когда модель
    отказалась и мы переключаемся, дальше по приоритету идут только модели
    с нужными способностями — кормить картинкой модель без vision глупо.

    Названная модель у всех отказала — цепочка продолжается другими
    включёнными моделями («последний шанс»): личная приставка отвечает
    чужим ответом лучше, чем мёртвой ошибкой.

    Пустая цепочка — обслужить нечим: ни одного включённого провайдера
    с настроенной моделью.
    """
    chain = enabled(entries)
    wanted = str(requested or "").strip()
    if not chain:
        return []

    def attempt(entry: dict) -> dict:
        return {"providerId": entry["id"], "model": str(entry.get("model") or "").strip()}

    def last_resort(attempts: list[dict], seen: set) -> list[dict]:
        """Последний шанс — остальные включённые по приоритету.

        Способные (заявившие нужное) идут первыми. Немаркированные не
        выбрасываются: иначе замкнутая петля — выучить vision можно только
        получив картинку, а роутер давал бы её лишь уже помеченным, и ни
        одна модель никогда не доказала бы способность. Немаркированная
        пробуется последней: отказ выучится (следующий раз пропустим),
        успех откроет vision.
        """
        capable, plain = [], []
        for entry in chain:
            if entry["id"] in seen or not str(entry.get("model") or "").strip():
                continue
            (capable if (not needs or _capable(entry, needs)) else plain).append(entry)
        for entry in capable:
            attempts.append(attempt(entry))
            seen.add(entry["id"])
        for entry in plain:
            attempts.append(attempt(entry))
            seen.add(entry["id"])
        return attempts

    # Модель не названа: каждый включённый со своей настроенной моделью.
    # Способные (заявившие нужное) идут первыми; немаркированные не
    # отбрасываются — иначе vision нельзя никогда выучить (нужно получить
    # картинку, чтобы доказать, а фильтр давал бы её только помеченным).
    if not wanted:
        pool = [entry for entry in chain if str(entry.get("model") or "").strip()]
        if needs:
            pool.sort(key=lambda entry: 0 if _capable(entry, needs) else 1)
        return [attempt(entry) for entry in pool]

    # Полное совпадение (включая «префикс/модель» в имени самой модели)
    # важнее разбора префикса как провайдера: у openrouter модель
    # «anthropic/claude-…» не должна уезжать к провайдеру anthropic.
    matches = [
        entry for entry in chain if offers_model(str(entry.get("model") or ""), [wanted])
    ]

    # «ollama/qwen3» — явный выбор провайдера для модели не из каталога.
    # Префикс решает только порядок: закреплённый идёт первым, но его
    # отказ переводит дальше, как у обычной модели. Даже доказавшая
    # отказ от нужного остаётся в цепочке (в хвосте через _fit) — отказ
    # бывает временным, и «не умеет» должно сниматься успехом.
    #
    # Однако при запросе с картинками/инструментами названная, но
    # неподходящая модель сразу уходит в хвост: картинку текстовой не
    # даём. Первыми идут способные (белый список первым).
    if needs and matches:
        capable = [entry for entry in matches if _capable(entry, needs)]
        plain = [entry for entry in matches if not _capable(entry, needs)]
        attempts = [attempt(entry) for entry in capable]
        # last_resort видит все записи, кроме названных целиком; они
        # распределены сами: подходящие уже в голове, неподходящие —
        # добавятся в самый конец.
        attempts = last_resort(attempts, {entry["id"] for entry in matches})
        attempts.extend(attempt(entry) for entry in plain)
        return _tail_refused(attempts, entries, needs)

    if not matches:
        prefix, slash, rest = wanted.partition("/")
        if slash and rest.strip():
            for entry in chain:
                if entry["id"] == prefix.strip().lower():
                    pinned = [{"providerId": entry["id"], "model": rest.strip()}]
                    return last_resort(pinned, {entry["id"]})

    # Названная модель с доказанным отказом от нужного не исключается
    # совсем (иначе «не умеет» не снимется само и модель умрёт насовсем),
    # а просто уходит в конец цепочки: подходитющие идут первыми. Отказ
    # часто временный (402/429, обновление модели) — пусть шанс остаётся.
    # Степень совпадения (_fit=2 для не умеющих нужного) и решает порядок.

    attempts = [attempt(entry) for entry in matches]
    # Требования запроса и «последний шанс» — одно и то же движение:
    # дальше по приоритету, с проверкой способностей.
    attempts = last_resort(attempts, {item["providerId"] for item in attempts})

    # Доказавшие отказ от нужного уходят в конец цепочки (мягкая
    # стратегия): закрепление по префиксу или высокий приоритет не
    # должны ставить их первыми, когда есть подходящие. Из цепочки не
    # выкидываем — отказ бывает временным, и успех снимает «не умеет».
    return _tail_refused(attempts, entries, needs)


def _tail_refused(attempts: list[dict], entries: list[dict],
                  needs: tuple[str, ...]) -> list[dict]:
    """Доказавшие отказ от нужного — в хвост цепочки (но не убраны)."""
    if not (needs and entries):
        return attempts
    by_id = {entry["id"]: entry for entry in entries}

    def _is_refused(attempt: dict) -> bool:
        entry = by_id.get(attempt["providerId"])
        return bool(entry) and _refused_for(entry, needs)

    return [a for a in attempts if not _is_refused(a)] + [
        a for a in attempts if _is_refused(a)
    ]


def should_fallback(status: int) -> bool:
    """Переключать ли на следующего провайдера после ответа `status`."""
    return status in FALLBACK_STATUSES
