"""Изученные способности: приставка помнит, что доказала практика."""

from __future__ import annotations

from storage.capabilities import LEARNABLE, CapabilityStore


class TestCapabilityStore:
    def test_learned_facts_are_remembered(self, tmp_path) -> None:
        store = CapabilityStore(tmp_path)
        store.learn("lmstudio/qwen3-0.6b", "vision", True)
        store.learn("lmstudio/qwen3-0.6b", "tools", False)

        assert store.known("lmstudio/qwen3-0.6b") == {"vision": True, "tools": False}

    def test_an_unknown_model_has_no_facts(self, tmp_path) -> None:
        store = CapabilityStore(tmp_path)
        assert store.known("groq/llama-3.3-70b") == {}
        assert store.known("") == {}

    def test_the_last_observation_wins_in_both_directions(self, tmp_path) -> None:
        store = CapabilityStore(tmp_path)
        # Отказ провайдера опровергает ручную справку…
        store.learn("openrouter/qwen3-coder", "tools", False)
        # …а позже успех переворачивает выученный запрет.
        store.learn("openrouter/qwen3-coder", "tools", True)

        assert store.known("openrouter/qwen3-coder") == {"tools": True}

    def test_facts_survive_a_restart(self, tmp_path) -> None:
        CapabilityStore(tmp_path).learn("ollama/qwen3", "vision", True)

        assert CapabilityStore(tmp_path).known("ollama/qwen3") == {"vision": True}

    def test_nonsense_is_not_learned(self, tmp_path) -> None:
        store = CapabilityStore(tmp_path)
        store.learn("ollama/qwen3", "умеет петь", True)
        store.learn("", "vision", True)

        assert store.known("ollama/qwen3") == {}
        assert set(LEARNABLE) == {"vision", "tools"}

    def test_release_forgets_everything(self, tmp_path) -> None:
        store = CapabilityStore(tmp_path)
        store.learn("ollama/qwen3", "vision", False)

        store.release("ollama/qwen3")

        assert store.known("ollama/qwen3") == {}


class TestMergeCapabilities:
    @staticmethod
    def _merge(configured, known):
        from service.gateway import _merge_capabilities

        return _merge_capabilities(configured, known)

    def test_learned_yes_adds_the_word(self) -> None:
        assert self._merge("", {"vision": True}) == "vision"

    def test_learned_no_erases_the_handwritten_word(self) -> None:
        # Кто последний, тот и прав: практика важнее ручной справки.
        assert self._merge("vision, tools", {"tools": False}) == "vision"

    def test_handwritten_text_is_preserved(self) -> None:
        assert self._merge("thinking, tools", {"vision": True}) == "thinking, tools, vision"

    def test_nothing_to_merge_is_a_pass_through(self) -> None:
        assert self._merge("vision", {}) == "vision"
        assert self._merge(None, {}) == ""
