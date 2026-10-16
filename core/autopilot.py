"""Фоновая проверка: кого проверять и что сказать в журнал.

Служба на FreedomBox работает круглосуточно, поэтому отчитываться
за сообщество может сама — счётчик (§6 CONTEXT.md) растёт от того,
что приставка **может** пользоваться провайдером, а не только от
нажатия кнопки человеком.

Правила те же, что у ручной проверки (§5, §9 CONTEXT.md):

  * помеченную неработающей проверять нельзя;
  * выключенные не трогаем — их человек выключил сам;
  * провайдер без ключа — не поломка, а просьба ввести ключ: молчим.
"""

from __future__ import annotations

from core.catalogue import STATUS_DEAD


def checkable(entries: list[dict]) -> list[dict]:
    """Провайдеры, которых имеет смысл проверять фоном."""
    return [
        entry
        for entry in entries
        if entry.get("enabled") and entry.get("status") != STATUS_DEAD
    ]


def report_line(results: list[dict]) -> str:
    """Одна строка журнала о раунде; пусто — проверять было нечего.

    Каждый итог — то, что вернул service.ops.check: ``name``, ``code``
    (0 — успех, 1 — поломка, 3 — нужен ключ) и ``error`` (причина).
    """
    if not results:
        return ""

    ok = sum(1 for result in results if result["code"] == 0)
    failed = [
        f"{result['name']} ({result['error']})"
        for result in results
        if result["code"] == 1
    ]
    keyless = [result["name"] for result in results if result["code"] == 3]

    parts = [f"проверено {len(results)}, в порядке {ok}"]
    if failed:
        parts.append(f"сломаны: {', '.join(failed)}")
    if keyless:
        parts.append(f"без ключа: {', '.join(keyless)}")
    return " · ".join(parts)
