"""Unit tests for load_kb_config() validation in scripts/kb_server.py."""

import json

import pytest

from scripts.kb_server import load_kb_config


class TestLoadKbConfigValid:
    """Tests for valid configuration."""

    def test_valid_single_entry(self, monkeypatch):
        monkeypatch.setenv("KB_ALLOWLIST", '{"example": "TESTKB00001"}')
        monkeypatch.setenv("DEFAULT_KB", "example")

        allowlist, default = load_kb_config()

        assert allowlist == {"example": "TESTKB00001"}
        assert default == "example"

    def test_valid_multiple_entries(self, monkeypatch):
        config = {"example": "KB1", "internal": "KB2", "other": "KB3"}
        monkeypatch.setenv("KB_ALLOWLIST", json.dumps(config))
        monkeypatch.setenv("DEFAULT_KB", "internal")

        allowlist, default = load_kb_config()

        assert allowlist == config
        assert default == "internal"


class TestLoadKbConfigMissingAllowlist:
    """Tests for missing KB_ALLOWLIST."""

    def test_missing_kb_allowlist_exits(self, monkeypatch, capsys):
        monkeypatch.delenv("KB_ALLOWLIST", raising=False)
        monkeypatch.setenv("DEFAULT_KB", "example")

        with pytest.raises(SystemExit) as exc_info:
            load_kb_config()

        assert exc_info.value.code == 1
        assert "KB_ALLOWLIST" in capsys.readouterr().err

    def test_empty_kb_allowlist_exits(self, monkeypatch, capsys):
        monkeypatch.setenv("KB_ALLOWLIST", "")
        monkeypatch.setenv("DEFAULT_KB", "example")

        with pytest.raises(SystemExit) as exc_info:
            load_kb_config()

        assert exc_info.value.code == 1
        assert "KB_ALLOWLIST" in capsys.readouterr().err


class TestLoadKbConfigInvalidJson:
    """Tests for invalid JSON in KB_ALLOWLIST."""

    def test_invalid_json_exits(self, monkeypatch, capsys):
        monkeypatch.setenv("KB_ALLOWLIST", "not-json{")
        monkeypatch.setenv("DEFAULT_KB", "example")

        with pytest.raises(SystemExit) as exc_info:
            load_kb_config()

        assert exc_info.value.code == 1
        assert "not valid JSON" in capsys.readouterr().err


class TestLoadKbConfigEmptyObject:
    """Tests for empty object in KB_ALLOWLIST."""

    def test_empty_object_exits(self, monkeypatch, capsys):
        monkeypatch.setenv("KB_ALLOWLIST", "{}")
        monkeypatch.setenv("DEFAULT_KB", "example")

        with pytest.raises(SystemExit) as exc_info:
            load_kb_config()

        assert exc_info.value.code == 1
        assert "non-empty" in capsys.readouterr().err


class TestLoadKbConfigMissingDefault:
    """Tests for missing DEFAULT_KB."""

    def test_missing_default_kb_exits(self, monkeypatch, capsys):
        monkeypatch.setenv("KB_ALLOWLIST", '{"example": "TESTKB00001"}')
        monkeypatch.delenv("DEFAULT_KB", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            load_kb_config()

        assert exc_info.value.code == 1
        assert "DEFAULT_KB" in capsys.readouterr().err

    def test_empty_default_kb_exits(self, monkeypatch, capsys):
        monkeypatch.setenv("KB_ALLOWLIST", '{"example": "TESTKB00001"}')
        monkeypatch.setenv("DEFAULT_KB", "")

        with pytest.raises(SystemExit) as exc_info:
            load_kb_config()

        assert exc_info.value.code == 1
        assert "DEFAULT_KB" in capsys.readouterr().err


class TestLoadKbConfigDefaultNotInAllowlist:
    """Tests for DEFAULT_KB not in allowlist."""

    def test_default_not_in_allowlist_exits(self, monkeypatch, capsys):
        monkeypatch.setenv(
            "KB_ALLOWLIST", '{"example": "KB1", "internal": "KB2"}'
        )
        monkeypatch.setenv("DEFAULT_KB", "nonexistent")

        with pytest.raises(SystemExit) as exc_info:
            load_kb_config()

        assert exc_info.value.code == 1
        stderr = capsys.readouterr().err
        assert "nonexistent" in stderr
        assert "internal" in stderr
        assert "example" in stderr
