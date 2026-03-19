"""Debug tests for Gateway tool invocation failure.

These tests reproduce the issue where tools/list works through the Gateway
but tools/call returns "McpException - Tool invocation failed".

Hypotheses tested:
1. Response size — large responses fail, small ones succeed
2. structuredContent — FastMCP 3.x adds this field, Gateway may not handle it
3. Unicode — special chars in KB content break SSE framing
4. Protocol version mismatch — Runtime uses newer MCP version than Gateway supports

Run with: make e2e (requires deployed infrastructure)
"""

import json
import os
import urllib.request

import pytest

MCP_ENDPOINT = os.environ.get(
    "MCP_ENDPOINT", "https://knowledge-packs.example.com/mcp"
)

# Skip all tests if endpoint is not reachable
try:
    req = urllib.request.Request(
        MCP_ENDPOINT,
        data=json.dumps({"jsonrpc": "2.0", "id": 0, "method": "tools/list"}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        _tools_response = json.loads(resp.read().decode())
    ENDPOINT_AVAILABLE = "result" in _tools_response
except Exception:
    ENDPOINT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not ENDPOINT_AVAILABLE, reason="MCP endpoint not reachable"
)


def _call_tool(query: str, num_results: int = 5) -> dict:
    """Call query_docs through the Gateway and return the parsed JSON response."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {
            "name": "docs-kb-mcp-target___query_docs",
            "arguments": {"query": query, "num_results": num_results},
        },
    }
    req = urllib.request.Request(
        MCP_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


class TestGatewayToolCall:
    """Reproduce and isolate the tools/call failure."""

    def test_tools_list_works(self):
        """Baseline: tools/list always works through the Gateway."""
        payload = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        req = urllib.request.Request(
            MCP_ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        assert "result" in data
        tools = data["result"]["tools"]
        assert any("query_docs" in t["name"] for t in tools)

    def test_short_query_succeeds(self):
        """Hypothesis 1: short responses work."""
        result = _call_tool("test", num_results=1)
        assert "result" in result, f"Expected success, got: {result}"
        assert result["result"].get("isError") is not True, f"Tool returned error: {result}"

    def test_long_query_oryx(self):
        """Hypothesis 1: the 'What is Oryx?' query that fails in production."""
        result = _call_tool("What is Oryx?", num_results=1)
        assert "result" in result, f"Expected result, got: {result}"
        is_error = result["result"].get("isError", False)
        content_text = result["result"].get("content", [{}])[0].get("text", "")
        if is_error:
            pytest.fail(
                f"Tool call failed for 'What is Oryx?' (num_results=1). "
                f"Error: {content_text}"
            )

    def test_default_num_results(self):
        """Hypothesis 1: default num_results=5 with a query that returns large chunks."""
        result = _call_tool("What is Oryx?", num_results=5)
        assert "result" in result, f"Expected result, got: {result}"
        is_error = result["result"].get("isError", False)
        content_text = result["result"].get("content", [{}])[0].get("text", "")
        if is_error:
            pytest.fail(
                f"Tool call failed for 'What is Oryx?' (num_results=5). "
                f"Error: {content_text}"
            )

    @pytest.mark.parametrize("num_results", [1, 2, 3, 5, 10])
    def test_increasing_result_counts(self, num_results):
        """Hypothesis 1: find the threshold where response size causes failure."""
        result = _call_tool("Spryker architecture", num_results=num_results)
        assert "result" in result, f"Expected result, got: {result}"
        is_error = result["result"].get("isError", False)
        content_text = result["result"].get("content", [{}])[0].get("text", "")
        if is_error:
            pytest.fail(
                f"Failed at num_results={num_results}. Error: {content_text}"
            )

    def test_structured_content_present(self):
        """Hypothesis 2: check if structuredContent is in the response."""
        result = _call_tool("test", num_results=1)
        assert "result" in result
        # If structuredContent is present, FastMCP 3.x is adding it
        has_structured = "structuredContent" in result.get("result", {})
        if has_structured:
            print("structuredContent IS present in response (FastMCP 3.x behavior)")
        else:
            print("structuredContent NOT present in response")

    def test_unicode_content(self):
        """Hypothesis 3: queries returning Unicode-heavy content."""
        # Oryx docs contain U+2028 LINE SEPARATOR
        result = _call_tool("Oryx technology CSS JavaScript", num_results=1)
        assert "result" in result, f"Expected result, got: {result}"
        is_error = result["result"].get("isError", False)
        if is_error:
            pytest.fail(f"Unicode content caused failure: {result}")

    @pytest.mark.parametrize("query", [
        "Oryx",
        "Oryx framework",
        "What is Oryx?",
        "oryx frontend",
        "oryx components",
        "Spryker Oryx",
        "frontend framework Spryker",
    ])
    def test_oryx_queries(self, query):
        """Narrow down: which Oryx-related queries fail?"""
        result = _call_tool(query, num_results=1)
        assert "result" in result, f"Expected result, got: {result}"
        is_error = result["result"].get("isError", False)
        content_text = result["result"].get("content", [{}])[0].get("text", "")
        if is_error:
            pytest.fail(f"Query '{query}' failed: {content_text}")
