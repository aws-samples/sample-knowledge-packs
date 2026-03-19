"""Unit tests for scripts/validate_kb.py — query loading and validation logic."""

import json
from unittest.mock import MagicMock, patch

import pytest

from scripts.validate_kb import load_queries, run_validation


# --- load_queries ---


class TestLoadQueries:
    """Tests for load_queries function."""

    def test_loads_valid_queries_file(self, tmp_path):
        queries = [
            {"question": "What is X?", "expected_topic": "X"},
            {"question": "How does Y work?", "expected_topic": "Y"},
        ]
        f = tmp_path / "queries.json"
        f.write_text(json.dumps(queries))

        result = load_queries(str(f))
        assert result == queries

    def test_missing_file_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            load_queries(str(tmp_path / "nonexistent.json"))

    def test_invalid_json_exits(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not valid json")

        with pytest.raises(SystemExit):
            load_queries(str(f))

    def test_non_array_json_exits(self, tmp_path):
        f = tmp_path / "obj.json"
        f.write_text('{"key": "value"}')

        with pytest.raises(SystemExit):
            load_queries(str(f))

    def test_empty_array_returns_empty_list(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("[]")

        result = load_queries(str(f))
        assert result == []


# --- run_validation ---


class TestRunValidation:
    """Tests for run_validation function."""

    @patch("scripts.validate_kb.KBClient")
    def test_all_queries_return_results(self, mock_kb_cls, tmp_path):
        queries = [
            {"question": "Q1?", "expected_topic": "T1"},
            {"question": "Q2?", "expected_topic": "T2"},
        ]
        f = tmp_path / "queries.json"
        f.write_text(json.dumps(queries))

        mock_client = MagicMock()
        mock_client.retrieve.return_value = [
            {
                "content": {"text": "Some answer text"},
                "location": {"s3Location": {"uri": "s3://bucket/doc.md"}},
                "score": 0.85,
            }
        ]
        mock_kb_cls.return_value = mock_client

        result = run_validation("test-kb-id", str(f))
        assert result is True
        assert mock_client.retrieve.call_count == 2

    @patch("scripts.validate_kb.KBClient")
    def test_query_with_no_results_returns_false(self, mock_kb_cls, tmp_path):
        queries = [{"question": "Q1?", "expected_topic": "T1"}]
        f = tmp_path / "queries.json"
        f.write_text(json.dumps(queries))

        mock_client = MagicMock()
        mock_client.retrieve.return_value = []
        mock_kb_cls.return_value = mock_client

        result = run_validation("test-kb-id", str(f))
        assert result is False

    @patch("scripts.validate_kb.KBClient")
    def test_empty_queries_returns_true(self, mock_kb_cls, tmp_path):
        f = tmp_path / "queries.json"
        f.write_text("[]")

        result = run_validation("test-kb-id", str(f))
        assert result is True
        mock_kb_cls.return_value.retrieve.assert_not_called()
