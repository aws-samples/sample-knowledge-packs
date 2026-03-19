"""Unit tests for scripts/kb_server.py — result formatting and query_docs tool."""

import asyncio
import json
from unittest.mock import MagicMock, patch

from scripts.kb_server import create_server, format_results, sanitize_for_sse


class TestFormatResults:
    """Tests for format_results function."""

    def test_empty_results_returns_no_results_message(self):
        assert format_results([]) == "No results found for your query."

    def test_single_result_with_all_fields(self):
        results = [
            {
                "content": {"text": "The system uses a modular architecture."},
                "location": {
                    "s3Location": {"uri": "s3://bucket/docs/dg/dev/arch.md"}
                },
                "score": 0.92,
            }
        ]
        output = format_results(results)

        assert "Result 1" in output
        assert "score: 0.92" in output
        assert "s3://bucket/docs/dg/dev/arch.md" in output
        assert "The system uses a modular architecture." in output

    def test_multiple_results(self):
        results = [
            {
                "content": {"text": "First chunk."},
                "location": {"s3Location": {"uri": "s3://b/first.md"}},
                "score": 0.9,
            },
            {
                "content": {"text": "Second chunk."},
                "location": {"s3Location": {"uri": "s3://b/second.md"}},
                "score": 0.7,
            },
        ]
        output = format_results(results)

        assert "Result 1" in output
        assert "Result 2" in output
        assert "First chunk." in output
        assert "Second chunk." in output
        assert "s3://b/first.md" in output
        assert "s3://b/second.md" in output

    def test_result_with_missing_content(self):
        results = [
            {
                "location": {"s3Location": {"uri": "s3://b/doc.md"}},
                "score": 0.5,
            }
        ]
        output = format_results(results)

        assert "N/A" in output
        assert "s3://b/doc.md" in output

    def test_result_with_missing_source_location(self):
        results = [
            {
                "content": {"text": "Some text."},
                "score": 0.8,
            }
        ]
        output = format_results(results)

        assert "Unknown source" in output
        assert "Some text." in output

    def test_result_with_missing_score(self):
        results = [
            {
                "content": {"text": "Content here."},
                "location": {"s3Location": {"uri": "s3://b/doc.md"}},
            }
        ]
        output = format_results(results)

        assert "score: N/A" in output
        assert "Content here." in output

    def test_unicode_line_separator_stripped(self):
        """U+2028 LINE SEPARATOR breaks SSE framing — must be replaced with space."""
        results = [
            {
                "content": {"text": "Before\u2028After"},
                "location": {"s3Location": {"uri": "s3://b/doc.md"}},
                "score": 0.9,
            }
        ]
        output = format_results(results)
        assert "\u2028" not in output
        assert "Before After" in output

    def test_unicode_paragraph_separator_stripped(self):
        """U+2029 PARAGRAPH SEPARATOR also breaks SSE framing."""
        results = [
            {
                "content": {"text": "Para1\u2029Para2"},
                "location": {"s3Location": {"uri": "s3://b/doc.md"}},
                "score": 0.9,
            }
        ]
        output = format_results(results)
        assert "\u2029" not in output
        assert "Para1 Para2" in output

    def test_multiple_unicode_separators_stripped(self):
        """Multiple U+2028/U+2029 in one result are all replaced."""
        results = [
            {
                "content": {"text": "A\u2028B\u2029C\u2028D"},
                "location": {"s3Location": {"uri": "s3://b/doc.md"}},
                "score": 0.9,
            }
        ]
        output = format_results(results)
        assert "\u2028" not in output
        assert "\u2029" not in output
        assert "A B C D" in output


# --- sanitize_for_sse tests ---


class TestSanitizeForSse:
    """Tests for sanitize_for_sse — ensures SSE-unsafe chars are replaced."""

    def test_u2028_replaced(self):
        assert sanitize_for_sse("a\u2028b") == "a b"

    def test_u2029_replaced(self):
        assert sanitize_for_sse("a\u2029b") == "a b"

    def test_null_replaced(self):
        assert sanitize_for_sse("a\x00b") == "a b"

    def test_c0_controls_replaced(self):
        """C0 control chars (except tab, LF, CR) are replaced."""
        for cp in list(range(1, 9)) + [0x0B, 0x0C] + list(range(14, 32)):
            ch = chr(cp)
            result = sanitize_for_sse(f"a{ch}b")
            assert result == "a b", f"U+{cp:04X} was not replaced"

    def test_tab_preserved(self):
        assert sanitize_for_sse("a\tb") == "a\tb"

    def test_lf_preserved(self):
        assert sanitize_for_sse("a\nb") == "a\nb"

    def test_cr_preserved(self):
        assert sanitize_for_sse("a\rb") == "a\rb"

    def test_normal_text_unchanged(self):
        text = "Hello, world! This is normal text with numbers 123 and symbols @#$."
        assert sanitize_for_sse(text) == text

    def test_empty_string(self):
        assert sanitize_for_sse("") == ""

    def test_multiple_unsafe_chars(self):
        assert sanitize_for_sse("\x00\u2028\u2029\x01") == "    "

    def test_mixed_safe_and_unsafe(self):
        assert sanitize_for_sse("ok\u2028fine\x00done") == "ok fine done"


# --- Helpers for query_docs tests ---

_ALLOWLIST = {"example": "KB_EXAMPLE", "internal": "KB_INTERNAL"}
_DEFAULT = "example"


def _make_server(monkeypatch, allowlist=None, default=None):
    """Create a server with the given allowlist and default, returning (server, tools dict)."""
    monkeypatch.setenv("KB_ALLOWLIST", json.dumps(allowlist or _ALLOWLIST))
    monkeypatch.setenv("DEFAULT_KB", default or _DEFAULT)
    server = create_server()
    tools_list = asyncio.run(server.list_tools())
    tools = {t.name: t for t in tools_list}
    return server, tools


class TestQueryDocsValidAlias:
    """query_docs with a valid alias calls KBClient with the correct KB ID."""

    def test_valid_alias_resolves_to_correct_kb_id(self, monkeypatch):
        mock_results = [
            {
                "content": {"text": "Some result."},
                "location": {"s3Location": {"uri": "s3://b/doc.md"}},
                "score": 0.9,
            }
        ]

        with patch("scripts.kb_server.KBClient") as MockKBClient:
            mock_clients = {}
            def make_client(kb_id, region=None):
                m = MagicMock()
                m.retrieve.return_value = mock_results
                mock_clients[kb_id] = m
                return m
            MockKBClient.side_effect = make_client

            server, _ = _make_server(monkeypatch)
            result = asyncio.run(server.call_tool("query_docs", {"query": "test query", "kb": "internal", "num_results": 3}))

            # KBClient created at server init for all aliases
            assert MockKBClient.call_count == 2
            mock_clients["KB_INTERNAL"].retrieve.assert_called_once_with(
                "test query", num_results=3
            )
            assert "Some result." in result.content[0].text


class TestQueryDocsDefaultAlias:
    """query_docs without kb param uses the default alias's KB ID."""

    def test_omitted_kb_uses_default(self, monkeypatch):
        with patch("scripts.kb_server.KBClient") as MockKBClient:
            mock_clients = {}
            def make_client(kb_id, region=None):
                m = MagicMock()
                m.retrieve.return_value = []
                mock_clients[kb_id] = m
                return m
            MockKBClient.side_effect = make_client

            server, _ = _make_server(monkeypatch)
            asyncio.run(server.call_tool("query_docs", {"query": "test query"}))

            mock_clients["KB_EXAMPLE"].retrieve.assert_called_once()

    def test_explicit_none_kb_uses_default(self, monkeypatch):
        with patch("scripts.kb_server.KBClient") as MockKBClient:
            mock_clients = {}
            def make_client(kb_id, region=None):
                m = MagicMock()
                m.retrieve.return_value = []
                mock_clients[kb_id] = m
                return m
            MockKBClient.side_effect = make_client

            server, _ = _make_server(monkeypatch)
            asyncio.run(server.call_tool("query_docs", {"query": "test query", "kb": None}))

            mock_clients["KB_EXAMPLE"].retrieve.assert_called_once()


class TestQueryDocsUnknownAlias:
    """query_docs with an unknown alias returns generic error without listing valid aliases."""

    def test_unknown_alias_returns_error(self, monkeypatch):
        server, _ = _make_server(monkeypatch)
        result = asyncio.run(server.call_tool("query_docs", {"query": "test query", "kb": "nonexistent"}))

        assert "Unknown KB alias" in result.content[0].text
        assert "nonexistent" in result.content[0].text
        # M12: error must NOT enumerate valid aliases (information disclosure)
        assert "internal" not in result.content[0].text
        assert "example" not in result.content[0].text

    def test_raw_kb_id_rejected(self, monkeypatch):
        """Raw KB IDs are not valid aliases — they get rejected."""
        server, _ = _make_server(monkeypatch)
        result = asyncio.run(server.call_tool("query_docs", {"query": "test query", "kb": "KB_EXAMPLE"}))

        assert "Unknown KB alias" in result.content[0].text


class TestQueryDocsToolDescription:
    """query_docs tool description contains all aliases and mentions query_example_docs."""

    def test_description_contains_all_aliases(self, monkeypatch):
        _, tools = _make_server(monkeypatch)
        description = tools["query_docs"].description

        assert "internal" in description
        assert "example" in description

    def test_description_is_domain_aware(self, monkeypatch):
        _, tools = _make_server(monkeypatch)
        description = tools["query_docs"].description

        assert "documentation" in description
        assert "natural language" in description

    def test_description_mentions_default(self, monkeypatch):
        _, tools = _make_server(monkeypatch)
        description = tools["query_docs"].description

        assert "example" in description


class TestOldToolNotRegistered:
    """The old query_example_docs tool is NOT registered on the server."""

    def test_query_example_docs_not_in_tools(self, monkeypatch):
        _, tools = _make_server(monkeypatch)

        assert "query_example_docs" not in tools, (
            "query_example_docs should have been removed"
        )

    def test_only_query_docs_registered(self, monkeypatch):
        _, tools = _make_server(monkeypatch)

        assert "query_docs" in tools
