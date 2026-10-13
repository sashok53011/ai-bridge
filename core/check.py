"""Проверка провайдера: что сказать человеку и о чём отчитаться серверу.

Смысл проверки — «работает ли провайдер и какая у него модель», поэтому
достаточно списка моделей: платные запросы не нужны, ключ в отчёт не
попадает, серверу нужно знать только «получилось / не получилось».

Три исхода:

  * список моделей получен — отчитываемся об успехе;
  * провайдер ответил отказом или недоступен — отчитываемся о поломке;
  * без ключа проверить нельзя — это не поломка, а просьба ввести ключ,
    и сообществу об этом отчитываться не нужно.
"""

from __future__ import annotations

from core.models import offers_model


def judge(
    models: list[str] | None,
    *,
    configured: str = "",
    error: str = "",
    needs_key: bool = False,
) -> dict:
    """Итог проверки провайдера.

    Возвращает словарь с полями:

    ``ok``     — получилось ли;
    ``report`` — отчитываться ли на сервер (нет — только при «нужен ключ»);
    ``model``  — какую модель отметить в отчёте;
    ``error``  — причина отказа, пустая строка когда получилось.
    """
    if needs_key:
        return {
            "ok": False,
            "report": False,
            "model": "",
            "error": "без ключа проверить нельзя — введите ключ",
        }

    if error:
        return {"ok": False, "report": True, "model": configured, "error": error}

    assert models is not None

    if not models:
        return {
            "ok": False,
            "report": True,
            "model": configured,
            "error": "провайдер не вернул ни одной модели",
        }

    if configured and not offers_model(configured, models):
        return {
            "ok": False,
            "report": True,
            "model": configured,
            "error": f"настроенной модели {configured} нет в списке",
        }

    return {"ok": True, "report": True, "model": configured or models[0], "error": ""}
