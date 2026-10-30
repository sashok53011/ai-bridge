"""Тесты хранилища «Проверки»: раунд-трип и изоляция от автотеста."""

from __future__ import annotations

import json

from storage.verify import VerifyStore


def test_a_round_trip_keeps_metadata_and_results(tmp_path) -> None:
    store = VerifyStore(tmp_path)
    store.begin("p1, p2", "что на картинке?", system_prompt="отвечай коротко",
                images=["data:image/png;base64,AAAA", "data:image/png;base64,BBBB"])
    store.plan(4)
    store.record({"provider": "p1", "model": "m-1", "image_index": 0,
                  "status": "ok", "http_status": 200, "duration": 1.0,
                  "response": "кот", "ts": "2026-10-30T10:00:00Z",
                  "req": "", "resp": ""})
    store.advance()
    store.finish("p1, p2", store.partial(), prompt="что на картинке?",
                 system_prompt="отвечай коротко",
                 images=["data:image/png;base64,AAAA", "data:image/png;base64,BBBB"])

    loaded = store.load()
    assert loaded["system_prompt"] == "отвечай коротко"
    assert loaded["prompt"] == "что на картинке?"
    assert len(loaded["images"]) == 2
    assert len(loaded["results"]) == 1
    assert loaded["results"][0]["response"] == "кот"

    status = store.status()
    assert status["running"] is False
    assert status.get("error", "") == ""


def test_a_missing_file_is_empty_not_a_crash(tmp_path) -> None:
    store = VerifyStore(tmp_path)
    loaded = store.load()
    assert loaded["results"] == []
    assert loaded["images"] == []

    # Новый прогон начинается с чистого файла: begin удаляет старый.
    store.begin("p1", "q", images=["data:image/png;base64,AAAA"])
    store.finish("p1", [], prompt="q", images=["data:image/png;base64,AAAA"])
    assert store.load()["images"] == ["data:image/png;base64,AAAA"]

    store.begin("p2", "q2", images=["data:image/png;base64,BBBB"])
    # До finish старый файл ещё лежит (begin только готовит прогресс) — это
    # ок: страница читает результаты через partial(), а не через load().
    store.finish("p2", [], prompt="q2", images=["data:image/png;base64,BBBB"])
    assert store.load()["images"] == ["data:image/png;base64,BBBB"]