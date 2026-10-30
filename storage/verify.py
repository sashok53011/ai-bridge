"""Хранение последнего прогона «Проверки» (vision-проверка на картинках).

Один файл ``verify.json`` в состоянии приставки: кто (провайдер + модель),
когда и по каким картинкам прошли/упали. Новый прогон удаляет старый;
ход текущего живёт в памяти (страница опрашивает раз в секунду).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from storage.keys import default_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class VerifyStore:
    """Прогресс текущего и результаты последнего прогона «Проверки»."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_dir()
        self.path = self.directory / "verify.json"
        self._lock = threading.Lock()
        self._progress: dict = {
            "running": False,
            "provider": "",
            "done": 0,
            "total": 0,
            "model": "",
            "test": "",
            "started_at": "",
            "finished_at": "",
            "error": "",
            "stop": False,
            "prompt": "",
            "system_prompt": "",
            "image_count": 0,
        }
        self._partial: list[dict] = []
        self._images: list[str] = []

    def begin(self, provider: str, prompt: str = "", *,
              system_prompt: str = "", image_count: int = 0,
              images: list[str] | None = None) -> dict:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self._partial = []
            self._images = list(images or [])
            self._progress = {
                "running": True,
                "provider": str(provider or ""),
                "done": 0,
                "total": 0,
                "model": "",
                "test": "",
                "started_at": _now(),
                "finished_at": "",
                "error": "",
                "stop": False,
                "prompt": str(prompt or ""),
                "system_prompt": str(system_prompt or ""),
                "image_count": int(image_count or len(self._images)),
            }
            return dict(self._progress)

    def record(self, result: dict) -> None:
        with self._lock:
            self._partial.append(result)

    def partial(self) -> list[dict]:
        with self._lock:
            return list(self._partial)

    def partial_images(self) -> list[str]:
        with self._lock:
            return list(self._images)

    def request_stop(self) -> dict:
        with self._lock:
            if self._progress.get("running"):
                self._progress["stop"] = True
            return dict(self._progress)

    def stop_requested(self) -> bool:
        with self._lock:
            return bool(self._progress.get("stop"))

    def plan(self, total: int) -> dict:
        with self._lock:
            self._progress["total"] = int(total)
            return dict(self._progress)

    def tick(self, model: str, test: str) -> dict:
        with self._lock:
            self._progress["model"] = str(model or "")
            self._progress["test"] = str(test or "")
            return dict(self._progress)

    def advance(self) -> dict:
        with self._lock:
            self._progress["done"] = int(self._progress.get("done") or 0) + 1
            return dict(self._progress)

    def status(self) -> dict:
        with self._lock:
            return dict(self._progress)

    def finish(self, provider: str, results: list[dict], *, prompt: str = "",
              system_prompt: str = "", images: list[str] | None = None) -> dict:
        data = {
            "provider": str(provider or ""),
            "timestamp": _now(),
            "prompt": str(prompt or ""),
            "system_prompt": str(system_prompt or ""),
            "images": list(images or []),
            "results": list(results),
        }
        with self._lock:
            self._progress.update(
                running=False,
                finished_at=data["timestamp"],
                model="",
                test="",
            )
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except OSError:
                pass
        return data

    def fail(self, error: str) -> None:
        with self._lock:
            self._progress.update(running=False, error=str(error or ""), finished_at=_now())

    def load(self) -> dict:
        empty = {"provider": "", "timestamp": "", "prompt": "", "system_prompt": "",
                 "images": [], "results": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(data, dict):
            return empty
        return {
            "provider": str(data.get("provider") or ""),
            "timestamp": str(data.get("timestamp") or ""),
            "prompt": str(data.get("prompt") or ""),
            "system_prompt": str(data.get("system_prompt") or ""),
            "images": [str(x) for x in data.get("images", []) if isinstance(x, str)],
            "results": [r for r in data.get("results", []) if isinstance(r, dict)],
        }

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass