"""Чистое ядро приложения.

Здесь живут функции, которые принимают данные и возвращают результат —
без сети, без файлов, без времени. Благодаря этому каждую проверку можно
запустить отдельно за миллисекунды:

    uv run pytest core/../../tests/test_catalogue.py
"""

from .catalogue import (
    STATUS_ACTIVE,
    STATUS_DEAD,
    STATUS_DISABLED,
    normalize,
    reconcile,
    view,
)
from .models import extract_models, offers_model
from .progress import ProgressEvent, parse_line
from .stats import format_stats

__all__ = [
    "STATUS_ACTIVE",
    "STATUS_DEAD",
    "STATUS_DISABLED",
    "normalize",
    "reconcile",
    "view",
    "extract_models",
    "offers_model",
    "ProgressEvent",
    "parse_line",
    "format_stats",
]
