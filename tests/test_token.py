"""Токен приставки: создаётся один раз и дальше просто читается."""

from storage.token import get_or_create, resolve


class TestGetOrCreate:
    def test_a_first_run_creates_the_token(self, tmp_path) -> None:
        token = get_or_create(tmp_path)
        assert len(token) >= 32
        assert (tmp_path / "token").read_text(encoding="utf-8") == token

    def test_a_second_run_reuses_the_same_token(self, tmp_path) -> None:
        first = get_or_create(tmp_path)
        second = get_or_create(tmp_path)
        assert first == second

    def test_an_empty_file_gets_a_fresh_token(self, tmp_path) -> None:
        (tmp_path / "token").write_text("  \n", encoding="utf-8")
        token = get_or_create(tmp_path)
        assert token != "  "


class TestResolve:
    def test_env_wins_over_everything(self, tmp_path) -> None:
        token, source = resolve(from_env="from-env", from_config="from-config", directory=tmp_path)
        assert token == "from-env"
        assert "переменная" in source

    def test_config_beats_the_file(self, tmp_path) -> None:
        token, source = resolve(from_env="", from_config="from-config", directory=tmp_path)
        assert token == "from-config"
        assert "config.toml" in source

    def test_falls_back_to_the_device_file(self, tmp_path) -> None:
        token, source = resolve(from_env="", from_config="", directory=tmp_path)
        assert token == get_or_create(tmp_path)
        assert "файл" in source
