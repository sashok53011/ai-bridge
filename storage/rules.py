"""Правила приставки: условие → действие, отдельно для каждого провайдера.

Администратор описывает на странице «Правила» условия, которые видит в
ответах провайдеров (и в запросах клиентов), и к каждому — что делать,
когда оно совпало. Так распознавание поведения перестаёт быть зашитым
в коде: админ знает формулировки своих провайдеров и правит их сам.

Два вида правил:

* **ответы** — живут у провайдера. Условие составное, все указанные
  части должны совпасть разом: строка в **запросе** (необязательно) +
  строка в **ответе** (необязательно) + **статус** ответа (необязательно);
  но хотя бы «строка в ответе» или «статус» обязаны быть — иначе
  правило ловило бы вообще всё. Совпало → действие. Правило админа
  сильнее встроенного решения по статусу. **Все** подошедшие правила
  срабатывают по порядку (одной строкой можно и выучить, и в белый
  положить).
* **запросы** — глобальные: в запросе клиента есть строка (например
  ``image_url``) → запрос требует способности (vision / tools), и
  роутер выбирает модель с этой способностью.

Файл — ``rules.json`` рядом с остальным состоянием; читается на каждое
обращение (крошечный). Пустой или сломанный файл — правил нет, живут
встроенные решения.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from storage.keys import default_dir

#: Действия для правил ответов: код → подпись на странице.
RESPONSE_ACTIONS = {
    "grey": "в серый список (24 ч)",
    "black": "в чёрный список",
    "white": "в белый список",
    "switch": "переключить на другого сейчас",
    "untouched": "списки не трогать (только журнал)",
    "can-vision": "выучить: умеет картинки",
    "can-tools": "выучить: умеет инструменты",
    "cant-vision": "выучить: не умеет картинки",
    "cant-tools": "выучить: не умеет инструменты",
}

#: Действия для правил запросов: запрос требует выбора модели.
REQUEST_ACTIONS = {
    "vision": "выбрать vision-модель (с картинками)",
    "tools": "выбрать tools-модель (с инструментами)",
}

#: Способности, которым соответствуют действия запроса.
ACTION_CAPABILITY = {"vision": "vision", "tools": "tools"}

#: Действия «выучить»: код → (способность, доказано «умеет»?).
LEARN_ACTIONS = {
    "can-vision": ("vision", True),
    "can-tools": ("tools", True),
    "cant-vision": ("vision", False),
    "cant-tools": ("tools", False),
}

#: Действия, после которых ответ считается годным и отдаётся клиенту.
#: Остальные (серый/чёрный/переключить/не умеет) уходят к следующему.
#: На ответе с ошибкой приставка уходит к следующему в любом случае.
SERVE_ACTIONS = frozenset({"white", "untouched", "can-vision", "can-tools"})


class RuleStore:
    """Хранилище правил в файле rules.json; читается на каждый запрос."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self._lock = threading.Lock()

    @property
    def _path(self) -> Path:
        return self.directory / "rules.json"

    def all(self) -> dict:
        """Оба вида правил: {"response": [...], "request": [...]}.

        Сломанный файл — пустые списки (правил нет, работают встроенные).
        """
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"response": [], "request": []}
        if not isinstance(data, dict):
            return {"response": [], "request": []}
        return {
            "response": [r for r in data.get("response", []) if isinstance(r, dict)],
            "request": [r for r in data.get("request", []) if isinstance(r, dict)],
        }

    def response_rules(self, provider_id: str) -> list[dict]:
        """Правила ответов одного провайдера (порядок — как добавлены)."""
        wanted = str(provider_id or "").strip().lower()
        return [
            rule
            for rule in self.all()["response"]
            if str(rule.get("provider") or "").strip().lower() == wanted
        ]

    def request_rules(self) -> list[dict]:
        """Правил запросов: строки, по которым определяется требование."""
        return self.all()["request"]

    def add(self, where: str, *, text: str = "", action: str,
            provider: str = "", req: str = "", status=None) -> bool:
        """Добавить правило. False — условий нет или неизвестное действие.

        Правило ответа: обязателен провайдер и хотя бы одна часть
        условия ответа (строка ``text`` или ``status``) — иначе ловило
        бы вообще всё. Строка запроса ``req`` — дополнительное условие.
        """
        known = RESPONSE_ACTIONS if where == "response" else REQUEST_ACTIONS
        if action not in known:
            return False
        text = str(text or "").strip()
        req = str(req or "").strip()
        parsed_status = _parse_status(status)
        if where == "response":
            if not str(provider or "").strip():
                return False
            if not text and parsed_status is None:
                return False
            rule: dict = {"provider": str(provider).strip().lower(), "action": action}
            if req:
                rule["req"] = req
            if text:
                rule["text"] = text
            if parsed_status is not None:
                rule["status"] = parsed_status
        else:
            if not text:
                return False
            rule = {"text": text, "action": action}
        with self._lock:
            data = self.all()
            data[where].append(rule)
            self._write(data)
        return True

    def edit(self, where: str, index: int, *, text: str = "", action: str,
             provider: str = "", req: str = "", status=None) -> bool:
        """Изменить правило по порядковому номеру (со страницы)."""
        known = RESPONSE_ACTIONS if where == "response" else REQUEST_ACTIONS
        if action not in known:
            return False
        text = str(text or "").strip()
        req = str(req or "").strip()
        parsed_status = _parse_status(status)
        if where == "response":
            if not str(provider or "").strip():
                return False
            if not text and parsed_status is None:
                return False
            updated: dict = {"provider": str(provider).strip().lower(), "action": action}
            if req:
                updated["req"] = req
            if text:
                updated["text"] = text
            if parsed_status is not None:
                updated["status"] = parsed_status
        else:
            if not text:
                return False
            updated = {"text": text, "action": action}
        with self._lock:
            data = self.all()
            rules = data.get(where) or []
            if not 0 <= index < len(rules):
                return False
            rules[index] = updated
            self._write(data)
        return True

    def drop(self, where: str, index: int) -> bool:
        """Убрать правило по порядковому номеру (со страницы). False — не вышло."""
        with self._lock:
            data = self.all()
            rules = data.get(where) or []
            if not 0 <= index < len(rules):
                return False
            rules.pop(index)
            self._write(data)
        return True

    def _write(self, data: dict) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # не сохранилось — страница скажет, что не вышло


def _parse_status(value) -> int | None:
    """Статус из формы: пусто — None, мусор — None, иначе целое."""
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def match_response(rule: dict, *, req_text: str, resp_text: str,
                   status: int) -> bool:
    """Совпало ли правило ответа с этим случаем (все части разом).

    ``req_text`` — текст запроса (нижний регистр), ``resp_text`` —
    текст ответа (нижний регистр), ``status`` — код ответа. Части,
    которых в правиле нет, не проверяются.
    """
    needle = str(rule.get("req") or "").strip().lower()
    if needle and needle not in req_text:
        return False
    needle = str(rule.get("text") or "").strip().lower()
    if needle and needle not in resp_text:
        return False
    wanted = _parse_status(rule.get("status"))
    if wanted is not None and wanted != int(status):
        return False
    return True


def describe(rule: dict) -> str:
    """Человекочитаемое условие правила — для журнала и страницы."""
    parts: list[str] = []
    if rule.get("req"):
        parts.append(f"в запросе «{rule['req']}»")
    if rule.get("text"):
        parts.append(f"в ответе «{rule['text']}»")
    wanted = _parse_status(rule.get("status"))
    if wanted is not None:
        parts.append(f"статус {wanted}")
    return " и ".join(parts) or "без условий"
