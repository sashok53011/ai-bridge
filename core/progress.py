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

    fraction = None
    total = data.get("total")
    completed = data.get("completed")
    if (
        isinstance(total, (int, float))
        and isinstance(completed, (int, float))
        and total > 0
    ):
        fraction = min(1.0, max(0.0, completed / total))

    raw_error = data.get("error")
    error = str(raw_error).strip() if raw_error else None

    return ProgressEvent(
        status=status,
        fraction=fraction,
        error=error,
        done=status == "success" and not error,
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
