"""End-to-end tests against the live deployed Knowledge Base.

These tests require a deployed KB and the KNOWLEDGE_BASE_ID env var set.
They validate the full retrieval pipeline against real AWS infrastructure.
"""

import os
from unittest.mock import patch

import pytest

from scripts.kb_client import KBClient
from scripts.kb_server import create_server
from scripts.validate_kb import load_queries, run_validation

# Skip entire module if KNOWLEDGE_BASE_ID is not set and can't be auto-detected
KB_ID = os.environ.get("KNOWLEDGE_BASE_ID")

if not KB_ID:
    # Try to auto-detect from deployed KnowledgePackStack
    try:
        import subprocess
        result = subprocess.run(
            ["aws", "cloudformation", "describe-stacks",
             "--stack-name", "KnowledgePackStack",
             "--region", "eu-west-1",
             "--query", "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue",
             "--output", "text"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            KB_ID = result.stdout.strip()
            os.environ["KNOWLEDGE_BASE_ID"] = KB_ID
    except Exception:
        pass

pytestmark = pytest.mark.skipif(
    not KB_ID,
    reason="KNOWLEDGE_BASE_ID env var not set and KnowledgePackStack not deployed — skipping e2e tests",
)

# Example queries — update these for your documentation corpus
KNOWN_QUERIES = [
    "system architecture",
    "Glue API",
    "the cloud platform deployment",
]


@pytest.fixture(scope="module")
def kb_client():
    """Create a KBClient pointing at the live KB."""
    return KBClient(knowledge_base_id=KB_ID, region="eu-west-1")


class TestRetrievalResultCompleteness:
    """Property 2: Retrieval results always include content and source location.

    For any non-empty list of retrieval results returned by KBClient.retrieve(),
    every result dict SHALL have a non-empty content.text field and a non-empty
    source location field.

    Feature: knowledge-pack, Property 2: Retrieval results always include content and source location

    **Validates: Requirements 4.1, 4.2**
    """

    @pytest.mark.parametrize("query", KNOWN_QUERIES)
    def test_results_have_content_and_source(self, kb_client, query):
        """Every retrieval result must have non-empty content.text and location.s3Location.uri."""
        results = kb_client.retrieve(query, num_results=5)

        # We expect results for these known queries
        assert len(results) > 0, f"Expected results for query '{query}', got none"

        for i, result in enumerate(results):
            # content.text must exist and be non-empty
            content_text = result.get("content", {}).get("text", "")
            assert content_text and content_text.strip(), (
                f"Result {i} for query '{query}' has empty content.text"
            )

            # location.s3Location.uri must exist and be non-empty
            s3_uri = (
                result.get("location", {})
                .get("s3Location", {})
                .get("uri", "")
            )
            assert s3_uri and s3_uri.strip(), (
                f"Result {i} for query '{query}' has empty location.s3Location.uri"
            )


class TestValidationScriptCoverage:
    """Property 3: Validation script executes all queries from config.

    For any list of query objects loaded from the config file, the validation
    script SHALL execute exactly one Retrieve API call per query and include
    all queries in the results.

    Feature: knowledge-pack, Property 3: Validation script executes all queries from config

    **Validates: Requirements 5.4**
    """

    def test_all_config_queries_produce_results(self):
        """run_validation returns True when all queries from config produce results."""
        passed = run_validation(KB_ID, "validation_queries.json")
        assert passed, "Validation failed — at least one query returned no results"

    def test_query_count_matches_config(self):
        """The number of queries loaded from config matches the expected count."""
        queries = load_queries("validation_queries.json")
        assert len(queries) == 3, f"Expected 3 queries in config, got {len(queries)}"

    def test_each_config_query_returns_results(self, kb_client):
        """Every individual query from the config file returns at least one result."""
        queries = load_queries("validation_queries.json")

        for query_obj in queries:
            question = query_obj["question"]
            results = kb_client.retrieve(question, num_results=5)
            assert len(results) > 0, (
                f"Query '{question}' returned no results from the live KB"
            )



class TestMCPQueryForwarding:
    """Property 4: MCP server forwards all queries to Knowledge Base.

    For any natural language query string received by the MCP server's
    query_example_docs tool, the server SHALL invoke KBClient.retrieve()
    with that exact query string.

    Feature: knowledge-pack, Property 4: MCP server forwards all queries to Knowledge Base

    **Validates: Requirements 6.1**
    """

    @pytest.mark.parametrize("query", KNOWN_QUERIES)
    def test_mcp_tool_forwards_query_to_kb(self, query):
        """The MCP tool calls KBClient.retrieve() with the exact query string and returns live results."""
        original_retrieve = KBClient.retrieve
        captured_calls = []

        def spy_retrieve(self_arg, query_arg, num_results=5):
            captured_calls.append(query_arg)
            return original_retrieve(self_arg, query_arg, num_results=num_results)

        with patch.object(KBClient, "retrieve", side_effect=spy_retrieve, autospec=True):
            with patch.dict(os.environ, {
                "KB_ALLOWLIST": f'{{"example": "{KB_ID}"}}',
                "DEFAULT_KB": "example",
            }):
                server = create_server()
                import asyncio
                result = asyncio.run(server.call_tool("query_docs", {"query": query, "num_results": 5}))

        # Verify retrieve was called exactly once with the exact query string
        assert len(captured_calls) == 1, (
            f"Expected exactly 1 call to KBClient.retrieve(), got {len(captured_calls)}"
        )
        assert captured_calls[0] == query

        # Verify the MCP tool returned non-empty formatted output from the live KB
        text = result.content[0].text
        assert len(text) > 0
        assert "No results found" not in text, (
            f"MCP tool returned no results for query '{query}'"
        )


class TestMCPConfigConsistency:
    """Property 4: MCP config KB ID matches deployed stack.

    The KNOWLEDGE_BASE_ID in .kiro/settings/mcp.json (example-docs server env)
    SHALL equal the KnowledgeBaseId output of the deployed KnowledgePackStack.
    This catches stale KB IDs that cause silent MCP server failures in the IDE.

    Feature: knowledge-pack, Property 4: MCP config KB ID matches deployed stack

    **Validates: Requirements 6.1, 6.2, 6.3**
    """

    def test_mcp_config_kb_id_matches_deployed_stack(self):
        """The KNOWLEDGE_BASE_ID in mcp.json must match the deployed stack output."""
        import json
        from pathlib import Path

        mcp_config_path = Path(".kiro/settings/mcp.json")
        if not mcp_config_path.exists():
            pytest.skip("No .kiro/settings/mcp.json found")

        config = json.loads(mcp_config_path.read_text())
        config_kb_id = (
            config.get("mcpServers", {})
            .get("example-docs", {})
            .get("env", {})
            .get("KNOWLEDGE_BASE_ID")
        )
        if not config_kb_id:
            pytest.skip(
                "No KNOWLEDGE_BASE_ID in mcp.json — "
                "local stdio MCP server not configured (using remote Gateway instead)"
            )

        # KB_ID is auto-detected from the deployed stack at module level
        assert config_kb_id == KB_ID, (
            f"MCP config KB ID mismatch: "
            f".kiro/settings/mcp.json has '{config_kb_id}' "
            f"but deployed KnowledgePackStack has '{KB_ID}'. "
            f"Update the MCP config to match the deployed stack."
        )


class TestPredefinedValidationQueries:
    """E2E test for the three predefined validation queries.

    Each query is submitted to the live KB and verified to return relevant
    results by checking for expected keywords in the retrieved content.

    **Validates: Requirements 5.1, 5.2, 5.3**
    """

    def test_architecture_query_returns_relevant_results(self, kb_client):
        """Req 5.1: 'What is the system architecture?' returns architecture-related content."""
        results = kb_client.retrieve("What is the system architecture?", num_results=5)

        assert len(results) > 0, "Architecture query returned no results"

        combined_text = " ".join(
            r.get("content", {}).get("text", "") for r in results
        ).lower()

        assert any(
            kw in combined_text for kw in ["architecture", "module", "layer", "component"]
        ), f"Architecture query results lack relevant keywords. Got: {combined_text[:300]}"

    def test_deployment_query_returns_relevant_results(self, kb_client):
        """Req 5.2: Cloud deployment query returns deployment-related content."""
        results = kb_client.retrieve(
            "How do I deploy to the cloud?",
            num_results=5,
        )

        assert len(results) > 0, "Deployment query returned no results"

        combined_text = " ".join(
            r.get("content", {}).get("text", "") for r in results
        ).lower()

        assert any(
            kw in combined_text for kw in ["deploy", "cloud", "example cloud commerce os", "sccos"]
        ), f"Deployment query results lack relevant keywords. Got: {combined_text[:300]}"

    def test_glue_api_query_returns_relevant_results(self, kb_client):
        """Req 5.3: 'What are Glue APIs?' returns Glue API-related content."""
        results = kb_client.retrieve("What are Glue APIs?", num_results=5)

        assert len(results) > 0, "Glue API query returned no results"

        combined_text = " ".join(
            r.get("content", {}).get("text", "") for r in results
        ).lower()

        assert any(
            kw in combined_text for kw in ["glue", "api", "rest", "endpoint"]
        ), f"Glue API query results lack relevant keywords. Got: {combined_text[:300]}"

