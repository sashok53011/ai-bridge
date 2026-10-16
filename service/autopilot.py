"""Фоновая проверка внутри службы: статистика сообщества растёт сама.

`--serve` живёт круглосуточно (на FreedomBox — как системная служба),
поэтому периодическая проверка включённых провайдеров встроена сюда:
человек включает нужное, приставка сама отчитывается, что может
пользоваться. Правила — в core/autopilot; здесь только сеть и поток.
"""

from __future__ import annotations

import threading
import time

from core.autopilot import checkable, report_line
from providers.client import Provider
from service import ops

#: Как часто проверяет фон по умолчанию (секунды). Час — компромисс:
#: статистика свежая, а лишних запросов к провайдерам почти нет.
DEFAULT_INTERVAL = 3600.0


def check_round(
    server,
    entries: list[dict],
    keys,
    *,
    make_provider=Provider,
) -> list[dict]:
    """Проверяет всех включённых по очереди; итоги — списком, без печати.

    Провайдер без ключа и недоступный провайдер — не исключение, а
    итог: раунд проверяет всех до конца, чтобы один плохой не остановил
    остальных.
    """
    results: list[dict] = []
    for entry in checkable(entries):
        result = ops.check(
            server,
            entry,
            keys.get(entry["id"]),
            make_provider=make_provider,
            counter=False,
        )
        results.append(
            {
                "name": entry["name"],
                "code": result["code"],
                "error": (result["outcome"] or {}).get("error", "") or result["dead"] or "",
            }
        )
    return results


def start(
    server,
    entries_provider,
    keys,
    *,
    interval: float = DEFAULT_INTERVAL,
    log=print,
    make_provider=Provider,
    rounds: int | None = None,
    sleep=time.sleep,
) -> threading.Thread:
    """Вечный поток: сразу один раунд, потом спит и проверяет снова.

    ``rounds`` ограничивает число раундов (в тестах); ``sleep``
    подменяется, чтобы тесты не ждали. Ошибка раунда — одна строка
    в журнал, поток не умирает.
    """

    def loop() -> None:
        done = 0
        while rounds is None or done < rounds:
            try:
                line = report_line(
                    check_round(server, entries_provider(), keys, make_provider=make_provider)
                )
                if line:
                    log(f"автопроверка: {line}")
            except Exception as exc:  # noqa: BLE001 — фон не должен умирать
                log(f"автопроверка: не вышла ({exc})")
            done += 1
            if rounds is not None and done >= rounds:
                return
            sleep(interval)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread
