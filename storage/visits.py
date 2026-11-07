"""Хранилище счетчиков посещений страниц.

Файл visits.json хранит агрегаты: {path: count}.
"""

from __future__ import annotations

import json
from pathlib import Path

def _get_path() -> Path:
    import os
    home = os.environ.get("AI_BRIDGE_HOME")
    base = Path(home) if home else Path.home() / ".ai-bridge"
    return base / "visits.json"

def increment(path: str) -> int:
    """Увеличить счетчик посещений для пути и вернуть новое значение."""
    p = _get_path()
    data: dict[str, int] = {}
    
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    count = data.get(path, 0) + 1
    data[path] = count
    
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    
    return count
