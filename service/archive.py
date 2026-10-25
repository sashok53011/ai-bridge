"""Ежедневные автосохранения журнала и статистики: копии по датам.

Раз в сутки (и при каждом старте службы) приставка кладёт снимки
`events.jsonl` и `usage.json` в подпапку `archive/` с датой в имени:
`archive/2026-10-20-events.jsonl`. Старые файлы живут вечно — ротация
по размеру их не заменяет, история остаётся на диске.

Уже сохранённый день не перезаписывается; пропущенный (служба не
работала) догоняется при ближайшем запуске — датой фактического
запуска, чужие дни не подменяются. Ошибка копирования не роняет
службу: автосохранение — приятное бонус, а не условие работы.
"""

from __future__ import annotations

import shutil
import threading
import time
from datetime import date
from pathlib import Path

from storage.keys import default_dir

#: Что автосохраняем: файл состояния в корне приставки.
SOURCES = ("events.jsonl", "usage.json")

#: Как часто проверяем наступление нового дня (секунды).
CHECK_INTERVAL = 60.0


def archive_dir(directory: Path) -> Path:
    """Каталог автосохранений рядом с состоянием приставки."""
    return directory / "archive"


def _target_name(day: str, source: str) -> str:
    """Имя снимка: 2026-10-20-events.jsonl."""
    return f"{day}-{source}"


def archive_daily(directory: Path | None = None, *, today: str = "") -> list[Path]:
    """Сделать сегодняшние снимки, если их ещё нет. Возвращает созданные."""
    directory = directory or default_dir()
    day = today or date.today().isoformat()
    destination = archive_dir(directory)
    made: list[Path] = []
    for source in SOURCES:
        src = directory / source
        dst = destination / _target_name(day, source)
        if dst.exists() or not src.exists():
            continue
        try:
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            made.append(dst)
        except OSError:
            continue  # не удалось — попробуем в следующий заход
    return made


def start(directory: Path | None = None, *, interval: float = CHECK_INTERVAL) -> None:
    """Фоновый поток: снимок при старте и с наступлением каждого нового дня."""

    def loop() -> None:
        last_day = ""
        while True:
            try:
                today = date.today().isoformat()
                if today != last_day:
                    archive_daily(directory, today=today)
                    last_day = today
            except Exception:  # noqa: BLE001 — фон не должен умирать
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()
