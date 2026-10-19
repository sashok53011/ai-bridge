"""Репутация моделей: штрафы за поломки, паузы и прощение."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from storage.reputation import FAULT_LIMIT, ReputationStore


def _at(minute: int) -> datetime:
    return datetime(2026, 10, 19, 12, minute, tzinfo=timezone.utc)


class TestReputationStore:
    def test_faults_accumulate_and_pause_after_limit(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)

        paused = [store.fault("ollama/qwen3", now=_at(0)) for _ in range(FAULT_LIMIT)]

        assert paused == [False, False, True]  # на паузу — с третьей
        assert store.remaining("ollama/qwen3", now=_at(1)) > 0

    def test_old_faults_burn_out(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)
        store.fault("ollama/qwen3", now=_at(0))
        store.fault("ollama/qwen3", now=_at(1))

        # Поломки старше окна не складываются в третью.
        assert store.fault("ollama/qwen3", now=_at(30)) is False
        assert store.remaining("ollama/qwen3", now=_at(31)) == 0

    def test_a_success_forgives_everything(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)
        for _ in range(FAULT_LIMIT):
            store.fault("ollama/qwen3", now=_at(0))
        assert store.remaining("ollama/qwen3", now=_at(1)) > 0

        store.ok("ollama/qwen3")

        assert store.remaining("ollama/qwen3", now=_at(1)) == 0
        assert store.penalized(now=_at(1)) == []

    def test_a_forgiven_model_starts_over(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)

        def pause_length() -> int:
            for _ in range(FAULT_LIMIT):
                store.fault("ollama/qwen3")
            return store.remaining("ollama/qwen3")

        first = pause_length()
        store.ok("ollama/qwen3")  # прощён — но счётчик блокировок хранится?
        second = pause_length()

        # После прощения всё обнулилось — паузы начинаются заново с базовой.
        assert first > 0
        assert second == first

    def test_pause_grows_across_blocks_without_forgiveness(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)
        now = _at(0)
        for _ in range(FAULT_LIMIT):
            store.fault("ollama/qwen3", now=now)
        first = store.remaining("ollama/qwen3", now=now)
        # Ждём конца паузы и ломаем снова — блокировок уже больше.
        later = now + timedelta(seconds=first + 1)
        for _ in range(FAULT_LIMIT):
            store.fault("ollama/qwen3", now=later)
        second = store.remaining("ollama/qwen3", now=later)

        assert second > first

    def test_an_expired_pause_lets_the_model_back(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)
        for _ in range(FAULT_LIMIT):
            store.fault("ollama/qwen3", now=_at(0))

        assert store.remaining("ollama/qwen3", now=_at(0) + timedelta(hours=2)) == 0

    def test_release_is_an_admin_forgiveness(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)
        for _ in range(FAULT_LIMIT):
            store.fault("ollama/qwen3", now=_at(0))

        store.release("ollama/qwen3")

        assert store.remaining("ollama/qwen3", now=_at(1)) == 0
        assert store.penalized(now=_at(1)) == []

    def test_penalized_lists_with_counts(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)
        store.fault("ollama/qwen3", now=_at(0))
        store.fault("ollama/qwen3", now=_at(0))

        rows = store.penalized(now=_at(1))

        assert rows == [
            {"key": "ollama/qwen3", "fails": 2, "blocks": 0, "until": 0}
        ]

    def test_survives_restart(self, tmp_path) -> None:
        ReputationStore(tmp_path).fault("ollama/qwen3")

        again = ReputationStore(tmp_path)
        assert again.penalized()  # штрафы не потерялись

    def test_a_broken_file_reads_as_empty(self, tmp_path) -> None:
        (tmp_path / "reputation.json").write_text("{сломано", encoding="utf-8")
        store = ReputationStore(tmp_path)
        assert store.penalized() == []
        assert store.remaining("ollama/qwen3") == 0

    def test_empty_key_is_ignored(self, tmp_path) -> None:
        store = ReputationStore(tmp_path)
        assert store.fault("") is False
        store.ok("")
        store.release("")
        assert store.penalized() == []
