"""Три списка моделей: белый (по группам способностей), серый, чёрный.

`whitelist-*.txt` — подтверждённые успехом, разложенные по **группам**:
text (только текст), vision (видят картинки), tools (владеют
инструментами), vision-tools (и то и другое). Группа — подпись модели;
источник правды один — `capabilities.json` (выученное) плюс рукописная
справка. Успех кладёт модель в файл её группы и **перекладывает**, когда
модель доказала новую способность. Текстовый запрос идёт к дешёвым
текстовым моделям, картинка никогда не достаётся текстовой.

`blacklist.txt` — после жёсткого отказа; «почти» навсегда: если больше
некому служить, приставка берёт и чёрный — лучше чужой ответ, чем
мёртвая ошибка. `greylist.txt` — **персональный** список человека
(`users/<id>/greylist.txt`, у хозяина — в корне): мягкий отказ
(402/429 или спрятанное «мало кредитов / попробуй позже» в 200)
отстраняет **провайдера целиком** (со всеми моделями) на сутки; через
сутки он просыпается; после трёх циклов сна подряд уезжает в чёрный.

Один экземпляр ModelLists обслуживает один каталог. Формат строк
одинаков: имя на строке, всё после `#` — комментарий; в сером
комментарий несёт срок и цикл:
``ollama  # до 2026-10-21T18:00:00Z: лимит запросов · цикл 1``.
Старые строки «провайдер/модель» читаются как провайдер и сворачиваются
при старте (`migrate_old_grey`). Правка файла вступает в силу сразу —
файлы перечитаются на каждое обращение (они крошечные).
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage.keys import default_dir

#: Файлы белого списка: группа → имя файла.
WHITELIST_FILES = {
    "text": "whitelist-text.txt",
    "vision": "whitelist-vision.txt",
    "tools": "whitelist-tools.txt",
    "vision-tools": "whitelist-vision-tools.txt",
}

#: Старый плоский список (до групп) — мигрируется при первом запуске.
OLD_WHITELIST = "whitelist.txt"

BLACKLIST = "blacklist.txt"
GREYLIST = "greylist.txt"

#: Списки доказанных отсутствий способности: модель в них «не умеет»
#: vision/tools — приставка больше не кормит её картинками/инструментами.
REFUSED_VISION = "novision.txt"
REFUSED_TOOLS = "notools.txt"

#: Список доказанных платных моделей: при маршрутизации они идут
#: последними (бесплатные предпочтительнее), но не исключаются — если
#: бесплатных не осталось, платные тоже служат.
PAID = "paid.txt"

#: Сколько спит серая модель (часы).
GREY_HOURS = 24

#: После скольких циклов сна подряд мягкий отказ становится чёрным.
GREY_CYCLE_LIMIT = 3

_UNTIL_RE = re.compile(r"до\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)")
_CYCLE_RE = re.compile(r"цикл\s+(\d+)")


def _parse_names(text: str) -> set[str]:
    """Строки файла → множество имён (без комментариев), в нижнем регистре."""
    names: set[str] = set()
    for line in text.splitlines():
        name = line.split("#", 1)[0].strip().lower()
        if name:
            names.add(name)
    return names


def _grey_key(name: str) -> str:
    """Ключ серого списка — провайдер.

    Старые строки имели вид «провайдер/модель»; спит теперь провайдер
    целиком, поэтому всё сворачивается к провайдеру. Заодно чистится
    регистр и пробелы.
    """
    return name.split("/", 1)[0].strip().lower()


def _parse_until(comment: str) -> datetime | None:
    """Срок сна из комментария («до <ISO>»); None — срока нет.

    Всегда aware (UTC): naive-время нельзя сравнивать с `now()`.
    """
    match = _UNTIL_RE.search(comment)
    if not match:
        return None
    try:
        parsed = datetime.fromisoformat(
            match.group(1).strip().replace("Z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_cycle(comment: str) -> int:
    """Номер цикла сна из комментария; без него — 1."""
    match = _CYCLE_RE.search(comment)
    return int(match.group(1)) if match else 1


def _parse_reason(comment: str) -> str:
    """Причина сна из комментария: всё между «до <дата>:» и «· цикл N»."""
    text = _UNTIL_RE.sub("", comment).strip()
    text = _CYCLE_RE.sub("", text).strip()
    return text.strip(":· ").strip()


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


class ModelLists:
    """Три списка в одном каталоге; файлы перечитаются на каждый запрос."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self._lock = threading.Lock()

    # ------------------------------------------------- кодовые слова
    # Пользовательские списки: имя файла = кодовое слово (латиница,
    # цифры, дефис/подчёркивание). Живут в отдельной папке lists/ и не
    # мешают автоматическим спискам выше. Имя — то, что клиент видит как
    # «модель»; запрос на него раскрывается в цепочку моделей списка.

    _CODE_DIR = "lists"
    _CODE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

    def _code_path(self, name: str) -> Path:
        return self.directory / self._CODE_DIR / f"{name.strip().lower()}.txt"

    def code_valid(self, name: str) -> bool:
        """Годится ли имя как кодовое слово: латиница, цифры, - и _."""
        return bool(self._CODE_NAME_RE.match(str(name or "").strip().lower()))

    def code_names(self) -> list[str]:
        """Все кодовые слова (имена файлов в lists/), по алфавиту."""
        folder = self.directory / self._CODE_DIR
        try:
            names = sorted(
                path.stem for path in folder.glob("*.txt")
            )
        except OSError:
            return []
        return names

    def code_models(self, name: str) -> list[str]:
        """Модели списка в порядке строк (приоритет сверху вниз).

        Пустое имя или невалидное кодовое слово — пусто. Формат строк —
        как в остальных списках: имя на строке, всё после # — комментарий.
        """
        if not self.code_valid(name):
            return []
        lines = _read_lines(self._code_path(name))
        models: list[str] = []
        for line in lines:
            model = line.split("#", 1)[0].strip()
            if model:
                models.append(model)
        return models

    def code_text(self, name: str) -> str:
        """Сырое содержимое файла списка — для textarea на странице."""
        if not self.code_valid(name):
            return ""
        try:
            return self._code_path(name).read_text(encoding="utf-8")
        except OSError:
            return ""

    def code_write(self, name: str, text: str) -> bool:
        """Записать модели списка (правка со страницы админа).

        False — невалидное имя (защита от записи куда попало).
        """
        if not self.code_valid(name):
            return False
        try:
            path = self._code_path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
            return True
        except OSError:
            return False

    def code_drop(self, name: str) -> bool:
        """Удалить весь список (кодовое слово). True — был и удалён."""
        if not self.code_valid(name):
            return False
        try:
            path = self._code_path(name)
            if path.exists():
                path.unlink()
                return True
        except OSError:
            pass
        return False

    # ------------------------------------------------------------ чтение

    def _path(self, name: str) -> Path:
        return self.directory / name

    def _read(self, name: str) -> str:
        try:
            return self._path(name).read_text(encoding="utf-8")
        except OSError:
            return ""

    def whitelist(self) -> set[str]:
        """Все подтверждённые модели (union четырёх групп)."""
        names: set[str] = set()
        for filename in WHITELIST_FILES.values():
            names |= _parse_names(self._read(filename))
        return names

    def whitelist_group(self, name: str) -> str:
        """В какой группе лежит модель; "" — не подтверждена."""
        name = name.strip().lower()
        for group, filename in WHITELIST_FILES.items():
            if name in _parse_names(self._read(filename)):
                return group
        return ""

    def whitelist_groups(self) -> dict[str, set[str]]:
        """Все группы: {группа: {имя, ...}} — для страницы статистики."""
        return {
            group: _parse_names(self._read(filename))
            for group, filename in WHITELIST_FILES.items()
        }

    def blacklist(self) -> set[str]:
        """Запрещённые модели; пусто — запретов нет."""
        return _parse_names(self._read(BLACKLIST))

    def grey(self) -> dict[str, dict]:
        """Серый список: {провайдер: {"until": datetime, "cycle": int, "reason": str}}.

        Ключ — провайдер: спит он целиком, со всеми моделями. Старые
        строки «провайдер/модель» сворачиваются к провайдеру (берётся
        самый поздний срок и самый большой цикл).
        """
        result: dict[str, dict] = {}
        for line in self._read(GREYLIST).splitlines():
            name_part, _, comment = line.partition("#")
            key = _grey_key(name_part)
            if not key:
                continue
            until = _parse_until(comment) or datetime.min.replace(tzinfo=timezone.utc)
            cycle = _parse_cycle(comment)
            old = result.get(key)
            if old is None or (until, cycle) > (old["until"], old["cycle"]):
                result[key] = {"until": until, "cycle": cycle, "reason": _parse_reason(comment)}
        return result

    def text(self, name: str) -> str:
        """Содержимое файла для страницы; пустая строка — файла нет."""
        return self._read(name)

    # ------------------------------------------------------------ запись

    def _append_unique(self, name: str, filename: str, comment: str) -> bool:
        """Дописать строку, если имени ещё нет. True — строка добавлена."""
        name = name.strip().lower()
        if not name:
            return False
        path = self._path(filename)
        with self._lock:
            if name in _parse_names(self._read(filename)):
                return False
            suffix = f"  # {comment}" if comment else ""
            self._write_lines(filename, _read_lines(path) + [f"{name}{suffix}"])
        return True

    def _write_lines(self, filename: str, lines: list[str]) -> None:
        try:
            path = self._path(filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
            )
        except OSError:
            pass  # списки — не повод ронять запрос

    def _drop(self, name: str, filename: str) -> bool:
        """Удалить строку с именем. True — строка была и удалена."""
        with self._lock:
            return self._drop_locked(name, filename)

    def _drop_locked(self, name: str, filename: str) -> bool:
        """Удалить строку; вызывать при уже захваченном self._lock."""
        name = name.strip().lower()
        if not name:
            return False
        lines = _read_lines(self._path(filename))
        kept = [
            line for line in lines
            if line.split("#", 1)[0].strip().lower() != name
        ]
        if len(kept) == len(lines):
            return False
        self._write_lines(filename, kept)
        return True

    # ------------------------------------------------------------ действия

    def whitelist_place(self, name: str, group: str, comment: str = "") -> bool:
        """Положить модель в файл её группы; из остальных групп — убрать.

        True — модель попала в белый список (или переложена). Группа
        неизвестна (нет в WHITELIST_FILES) — берётся text.
        """
        group = group if group in WHITELIST_FILES else "text"
        name = name.strip().lower()
        if not name:
            return False
        with self._lock:
            changed = False
            for other_group, filename in WHITELIST_FILES.items():
                if other_group != group:
                    self._drop_locked(name, filename)
            if name not in _parse_names(self._read(WHITELIST_FILES[group])):
                suffix = f"  # {comment}" if comment else ""
                self._write_lines(
                    WHITELIST_FILES[group],
                    _read_lines(self._path(WHITELIST_FILES[group])) + [f"{name}{suffix}"],
                )
                changed = True
            return changed

    def whitelist_add(self, name: str, comment: str = "") -> bool:
        """Совместимость: подтвердить в группе text (устарело — см. place)."""
        return self.whitelist_place(name, "text", comment)

    def blacklist_add(self, name: str, reason: str = "") -> bool:
        """Запретить модель: в чёрный список с причиной-комментарием."""
        return self._append_unique(name, BLACKLIST, f"причина: {reason}" if reason else "")

    def grey_add(self, name: str, reason: str, *, cycle: int = 1,
                 hours: int = GREY_HOURS) -> datetime:
        """Отстранить провайдера на сутки: срок и цикл — в комментарии.

        Ключ — провайдер («провайдер/модель» старого формата тоже
        сворачивается и заменяется). Возвращает срок пробуждения.
        """
        name = _grey_key(name)
        until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=hours)
        comment = (
            f"до {until.isoformat().replace('+00:00', 'Z')}: {reason} · цикл {cycle}"
        )
        with self._lock:
            lines = [
                line for line in _read_lines(self._path(GREYLIST))
                if _grey_key(line.split("#", 1)[0]) != name
            ]
            lines.append(f"{name}  # {comment}")
            self._write_lines(GREYLIST, lines)
        return until

    def grey_drop(self, name: str) -> None:
        """Пробудить провайдера: убрать его из серого списка.

        Убираются и старые строки «провайдер/модель» — ключ теперь
        провайдер.
        """
        name = _grey_key(name)
        with self._lock:
            lines = [
                line for line in _read_lines(self._path(GREYLIST))
                if _grey_key(line.split("#", 1)[0]) != name
            ]
            self._write_lines(GREYLIST, lines)

    def migrate_old_grey(self) -> list[str]:
        """Свернуть старые строки «провайдер/модель» в «провайдер».

        Спит теперь провайдер целиком; файл приводится к новому виду
        один раз при старте. Возвращает свёрнутые имена; повторный
        вызов безопасен (строк с «/» уже нет).
        """
        lines = _read_lines(self._path(GREYLIST))
        old = [
            line for line in lines
            if "/" in line.split("#", 1)[0]
        ]
        if not old:
            return []
        merged: dict[str, dict] = {}
        for line in lines:
            name_part, _, comment = line.partition("#")
            key = _grey_key(name_part)
            if not key:
                continue
            until = _parse_until(comment) or datetime.min.replace(tzinfo=timezone.utc)
            cycle = _parse_cycle(comment)
            cell = merged.get(key)
            if cell is None or (until, cycle) > (cell["until"], cell["cycle"]):
                merged[key] = {"until": until, "cycle": cycle}
        out = []
        minimum = datetime.min.replace(tzinfo=timezone.utc)
        for key, cell in merged.items():
            until = cell["until"]
            if until == minimum:
                comment = f"перенесено из старого формата · цикл {cell['cycle']}"
            else:
                when = until.isoformat().replace("+00:00", "Z")
                comment = f"до {when}: перенесено из старого формата · цикл {cell['cycle']}"
            out.append(f"{key}  # {comment}")
        self._write_lines(GREYLIST, out)
        return sorted(line.split("#", 1)[0].strip() for line in old)

    def whitelist_drop(self, name: str) -> None:
        """Отозвать подтверждение — из всех четырёх групп."""
        for filename in WHITELIST_FILES.values():
            self._drop(name, filename)

    def migrate_old_whitelist(self, group_of=None) -> list[str]:
        """Разложить старый плоский whitelist.txt по группам.

        ``group_of(name) -> группа`` решает, куда положить; без него —
        в text. Старый файл уходит в ``whitelist.txt.migrated`` (не
        удаляется). Возвращает переложенные имена; вызывается один раз
        при старте. Повторный вызов безопасен (файла уже нет).
        """
        if not self._path(OLD_WHITELIST).exists():
            return []
        names = sorted(_parse_names(self._read(OLD_WHITELIST)))
        for name in names:
            try:
                group = group_of(name) if group_of else "text"
            except Exception:  # noqa: BLE001 — миграция не должна падать
                group = "text"
            self.whitelist_place(name, group, "перенесена при миграции")
        try:
            path = self._path(OLD_WHITELIST)
            path.replace(path.with_name(path.name + ".migrated"))
        except OSError:
            pass  # оставить старый файл — хуже не станет
        return names

    def blacklist_drop(self, name: str) -> None:
        """Снять запрет (ручное прощение админом)."""
        self._drop(name, BLACKLIST)

    # ------------------------------------------------------------ списки
    # «доказано без способности»: провайдер/модель, доказавшая, что не
    # умеет vision/tools. Глобальные (как blacklist).

    def refused_vision(self) -> set[str]:
        """Модели, доказавшие отсутствие vision."""
        return _parse_names(self._read(REFUSED_VISION))

    def refused_tools(self) -> set[str]:
        """Модели, доказавшие отсутствие tools."""
        return _parse_names(self._read(REFUSED_TOOLS))

    def refused_vision_add(self, name: str) -> bool:
        """Запомнить: модель не умеет vision."""
        return self._append_unique(name, REFUSED_VISION, "доказано: не умеет vision")

    def refused_tools_add(self, name: str) -> bool:
        """Запомнить: модель не умеет tools."""
        return self._append_unique(name, REFUSED_TOOLS, "доказано: не умеет tools")

    def refused_vision_drop(self, name: str) -> bool:
        """Снять отметку «не умеет vision» (модель научилась)."""
        return self._drop(name, REFUSED_VISION)

    def refused_tools_drop(self, name: str) -> bool:
        """Снять отметку «не умеет tools» (модель научилась)."""
        return self._drop(name, REFUSED_TOOLS)

    # ------------------------------------------------------------ список
    # «доказано платные»: модели, вернувшие 402 при тесте. Глобальный
    # (как blacklist). При маршрутизации платные идут последними.

    def paid(self) -> set[str]:
        """Модели, доказавшие платность (вернули 402)."""
        return _parse_names(self._read(PAID))

    def paid_add(self, name: str) -> bool:
        """Запомнить: модель платная (вернула 402)."""
        return self._append_unique(name, PAID, "доказано: платная (402)")

    def paid_drop(self, name: str) -> bool:
        """Снять отметку «платная» (модель стала бесплатной)."""
        return self._drop(name, PAID)

    def write_text(self, name: str, text: str) -> bool:
        """Записать содержимое файла списка (правка со страницы админа).

        Имя файла проверяется по белому списку имён — запись куда попало
        невозможна. Возвращаем False при отказе.
        """
        allowed = set(WHITELIST_FILES.values()) | {
            BLACKLIST, GREYLIST, REFUSED_VISION, REFUSED_TOOLS, PAID,
        }
        if name not in allowed:
            return False
        try:
            path = self._path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text.replace("\r\n", "\n"), encoding="utf-8")
            return True
        except OSError:
            return False
