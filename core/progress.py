"""Прогресс загрузки локальной модели.

Ollama отвечает на ``POST /api/pull`` потоком небольших JSON-объектов:
сначала статусы («pulling manifest»), затем по объекту на каждый кусок
с ``total`` и ``completed``, и в конце ``{"status": "success"}``.
Большая модель грузится минутами, поэтому проценты и отмена обязательны.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    """Одно событие из потока сервера."""

    status: str

    #: доля скачанного (0..1), если сервер её назвал
    fraction: float | None = None

    #: сообщение сервера, если что-то пошло не так
    error: str | None = None

    #: сервер подтвердил успех
    done: bool = False

    #: слой, к которому относятся байты (у Ollama — digest); бывает пустым
    digest: str | None = None

    #: байты слоя, если сервер их назвал
    total: float | None = None
    completed: float | None = None


@dataclass(frozen=True)
class Snapshot:
    """Общий вид загрузки: сколько скачано всего и что сейчас происходит."""

    status: str = ""

    #: доля по всем слоям (0..1); 1.0, когда сервер сказал «успех»
    fraction: float | None = None

    completed_bytes: float | None = None
    total_bytes: float | None = None

    error: str | None = None
    done: bool = False


class PullTracker:
    """Собирает поток событий в общий прогресс по всем слоям.

    Ollama шлёт байты по слоям и часто перемежает их; у события есть
    ``digest`` слоя. Поэтому суммируем **по слоям**, а не по событиям —
    иначе проценты прыгают туда-сюда и человек не понимает, сколько ждать.
    """

    def __init__(self) -> None:
        self._layers: dict[str, tuple[float, float]] = {}
        self._status = ""
        self._error: str | None = None
        self._done = False

    def add(self, event: ProgressEvent) -> Snapshot:
        """Принимает событие и отдаёт свежий общий вид."""
        if event.status:
            self._status = event.status
        if event.error:
            self._error = event.error
        if event.done:
            self._done = True

        if event.total is not None and event.completed is not None:
            # Последнее известное состояние слоя; digest склеивает события
            # одного слоя, а без него сервер считается по одному слою.
            self._layers[event.digest or ""] = (event.total, event.completed)

        return self.snapshot()

    def snapshot(self) -> Snapshot:
        total = sum(total for total, _ in self._layers.values()) or None
        completed = sum(done for _, done in self._layers.values()) or None

        fraction: float | None = None
        if self._done:
            fraction = 1.0
        elif total:
            fraction = min(1.0, max(0.0, (completed or 0.0) / total))

        return Snapshot(
            status=self._status,
            fraction=fraction,
            completed_bytes=completed,
            total_bytes=total,
            error=self._error,
            done=self._done,
        )


_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ")


def human_bytes(count: float) -> str:
    """«5.2 ГБ» или «312 МБ» — байты сами по себе человеку ни о чём не говорят."""
    value = float(count)
    for unit in _UNITS:
        if abs(value) < 1024 or unit == _UNITS[-1]:
            if unit == "Б":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {_UNITS[-1]}"  # недостижимо, но типchecker должен быть спокоен


def describe(snapshot: Snapshot) -> str:
    """Одна строка прогресса для человека: «45% · 5.2/14.0 ГБ · downloading»."""
    parts: list[str] = []
    if snapshot.fraction is not None:
        parts.append(f"{snapshot.fraction * 100:.0f}%")
    if snapshot.total_bytes and snapshot.completed_bytes is not None:
        parts.append(f"{human_bytes(snapshot.completed_bytes)}/{human_bytes(snapshot.total_bytes)}")
    if snapshot.status:
        parts.append(snapshot.status)
    return " · ".join(parts)


def parse_line(line: str) -> ProgressEvent | None:
    """Разбирает одну строку потока; пустота и мусор пропускаются (None)."""
    text = line.strip()
    if not text:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    status = str(data.get("status") or "").strip()

    raw_total = data.get("total")
    raw_completed = data.get("completed")
    total = float(raw_total) if isinstance(raw_total, (int, float)) else None
    completed = float(raw_completed) if isinstance(raw_completed, (int, float)) else None

    fraction = None
    if total is not None and completed is not None and total > 0:
        fraction = min(1.0, max(0.0, completed / total))

    raw_error = data.get("error")
    error = str(raw_error).strip() if raw_error else None

    raw_digest = data.get("digest")
    digest = str(raw_digest).strip() if raw_digest else None

    return ProgressEvent(
        status=status,
        fraction=fraction,
        error=error,
        done=status == "success" and not error,
        digest=digest,
        total=total,
        completed=completed,
    )


def split_lines(buffer: bytes) -> tuple[list[str], bytes]:
    """Достаёт целые строки из куска байтов и возвращает остаток.

    Поток приходит кусками, и строка может разорваться посередине —
    поэтому неполный хвост возвращается обратно в буфер.
    """
    text = buffer.replace(b"\r\n", b"\n")
    *complete, rest = text.split(b"\n")
    lines = [chunk.decode("utf-8", errors="replace") for chunk in complete]
    return lines, rest
