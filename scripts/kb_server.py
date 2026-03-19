"""FastMCP server exposing Bedrock Knowledge Bases as MCP tools.

Supports two transport modes controlled by the MCP_TRANSPORT env var:
- "stdio" (default): launched by Kiro IDE as a local process
- "streamable-http": deployed to AgentCore Runtime for remote access

Multi-KB support via environment variables:
- KB_ALLOWLIST: JSON object mapping alias names to KB IDs
  e.g. '{"example": "YOUR_KB_ID", "internal-docs": "ANOTHER_ID"}'
- DEFAULT_KB: alias to use when caller omits the kb parameter
- KB_DESCRIPTIONS: optional JSON object mapping alias names to topic descriptions
  e.g. '{"example": "Spryker architecture, Glue APIs, ACP integrations, deployment"}'
  These descriptions are included in the tool description so the LLM knows when to use the tool.

Usage:
    KB_ALLOWLIST='{"example":"YOUR_KB_ID"}' DEFAULT_KB=example python scripts/kb_server.py
    KB_ALLOWLIST='{"example":"YOUR_KB_ID"}' DEFAULT_KB=example MCP_TRANSPORT=streamable-http python scripts/kb_server.py
"""

import json
import logging
import os
import sys
import traceback

from fastmcp import FastMCP

logger = logging.getLogger("kb_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

try:
    from scripts.kb_client import KBClient
except ImportError:
    # Support running as `python scripts/kb_server.py` from project root
    from kb_client import KBClient

REGION = "eu-west-1"

# Characters to strip from tool responses before sending over SSE.
# The SSE spec (W3C) only treats LF, CR, CRLF as line terminators, but some
# parsers (including AgentCore Gateway) also break on U+2028/U+2029.
# We also strip NULL (can truncate strings) and other C0 control chars
# (except tab U+0009, LF U+000A, CR U+000D which are meaningful in text).
# See COE-002 for the full story.
_SSE_UNSAFE_CHARS = {
    "\u0000",                          # NULL — can truncate
    *[chr(c) for c in range(1, 9)],    # U+0001–U+0008 — C0 controls
    "\u000b",                          # U+000B VERTICAL TAB
    "\u000c",                          # U+000C FORM FEED
    *[chr(c) for c in range(14, 32)],  # U+000E–U+001F — C0 controls
    "\u2028",                          # LINE SEPARATOR — breaks Gateway SSE parser
    "\u2029",                          # PARAGRAPH SEPARATOR — same risk
}
_SSE_SANITIZE_TABLE = str.maketrans({ch: " " for ch in _SSE_UNSAFE_CHARS})


def sanitize_for_sse(text: str) -> str:
    """Replace characters that break SSE event framing with spaces.

    Targets U+2028/U+2029 (known to break AgentCore Gateway) and C0 control
    characters (except tab, LF, CR) that have no business in readable text.

    Args:
        text: Raw text, potentially from KB retrieval results.

    Returns:
        Sanitized text safe for SSE transport.
    """
    return text.translate(_SSE_SANITIZE_TABLE)


def format_results(results: list[dict]) -> str:
    """Format retrieval results as readable text with source locations.

    Args:
        results: List of retrieval result dicts from KBClient.retrieve().
            Each dict has content.text, location.s3Location.uri, and score.

    Returns:
        Formatted string with all results, or a no-results message.
    """
    if not results:
        return "No results found for your query."

    parts = []
    for i, result in enumerate(results, start=1):
        content = result.get("content", {}).get("text", "N/A")
        source = (
            result.get("location", {})
            .get("s3Location", {})
            .get("uri", "Unknown source")
        )
        score = result.get("score", "N/A")

        parts.append(
            f"--- Result {i} (score: {score}) ---\n"
            f"Source: {source}\n\n"
            f"{content}"
        )

    formatted = "\n\n".join(parts)
    return sanitize_for_sse(formatted)


def load_kb_config() -> tuple[dict[str, str], str]:
    """Load and validate KB_ALLOWLIST and DEFAULT_KB from environment.

    Returns:
        Tuple of (allowlist dict, default alias).

    Raises:
        SystemExit: If config is missing or invalid.
    """
    raw = os.environ.get("KB_ALLOWLIST", "")
    if not raw:
        print("Error: KB_ALLOWLIST environment variable is required.", file=sys.stderr)
        sys.exit(1)

    try:
        allowlist = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Error: KB_ALLOWLIST is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(allowlist, dict) or not allowlist:
        print("Error: KB_ALLOWLIST must be a non-empty JSON object.", file=sys.stderr)
        sys.exit(1)

    default_kb = os.environ.get("DEFAULT_KB", "")
    if not default_kb:
        print("Error: DEFAULT_KB environment variable is required.", file=sys.stderr)
        sys.exit(1)

    if default_kb not in allowlist:
        print(
            f"Error: DEFAULT_KB '{default_kb}' not in KB_ALLOWLIST. "
            f"Valid aliases: {', '.join(sorted(allowlist.keys()))}",
            file=sys.stderr,
        )
        sys.exit(1)

    return allowlist, default_kb


def create_server() -> FastMCP:
    """Create and configure the FastMCP server with multi-KB support.

    Reads KB_ALLOWLIST, DEFAULT_KB, and MCP_TRANSPORT from environment.

    KB_ALLOWLIST: JSON object mapping alias names to KB IDs.
    DEFAULT_KB: alias to use when caller omits the kb parameter.
    MCP_TRANSPORT controls the transport mode:
    - "streamable-http": stateless HTTP for AgentCore Runtime (0.0.0.0:8000)
    - "stdio" or unset: standard stdio transport for local IDE use
    - Unknown values fall back to stdio.

    Returns:
        Configured FastMCP server instance.

    Raises:
        SystemExit: If KB_ALLOWLIST or DEFAULT_KB is missing/invalid.
    """
    allowlist, default_alias = load_kb_config()

    mcp = FastMCP("docs-kb")

    # Build dynamic tool description with available aliases
    aliases_str = ", ".join(sorted(allowlist.keys()))

    # Optional: per-KB topic descriptions for LLM priming
    descriptions_raw = os.environ.get("KB_DESCRIPTIONS", "")
    try:
        kb_descriptions = json.loads(descriptions_raw) if descriptions_raw else {}
    except json.JSONDecodeError:
        print(f"WARNING: KB_DESCRIPTIONS is not valid JSON, ignoring: {descriptions_raw[:100]}")
        kb_descriptions = {}

    topics_block = ""
    if kb_descriptions:
        topics_lines = [f"- {alias}: {desc}" for alias, desc in sorted(kb_descriptions.items())]
        topics_block = "\n\nTopics covered:\n" + "\n".join(topics_lines)

    tool_description = (
        f"Search product documentation using natural language. "
        f"Returns relevant documentation chunks with source references.\n\n"
        f"Available KBs: {aliases_str}. Default: {default_alias}."
        f"{topics_block}\n\n"
        f"Args:\n"
        f"    query: Natural language question.\n"
        f"    kb: Optional KB alias from the allowlist. "
        f"Defaults to {default_alias}.\n"
        f"    num_results: Maximum number of results to return (default 5)."
    )

    # Pre-create KBClient instances for connection reuse (Q-6)
    kb_clients = {alias: KBClient(kb_id, region=REGION) for alias, kb_id in allowlist.items()}

    @mcp.tool(description=tool_description)
    def query_docs(query: str, kb: str | None = None, num_results: int = 5) -> str:
        logger.info("query_docs called: query=%r, kb=%r, num_results=%r", query[:100], kb, num_results)
        try:
            alias = kb or default_alias
            if alias not in allowlist:
                # Generic error — don't enumerate valid aliases to prevent information disclosure
                return f"Unknown KB alias '{alias}'."
            # Cap num_results to prevent cost amplification (M9)
            num_results = max(1, min(num_results, 10))
            # Cap query length to prevent oversized embedding requests (M9)
            query = query[:1000]
            client = kb_clients[alias]
            logger.info("Calling KBClient.retrieve for kb_id=%s", allowlist[alias])
            results = client.retrieve(query, num_results=num_results)
            logger.info("KBClient returned %d results", len(results))
            formatted = format_results(results)
            logger.info("Formatted response length: %d chars", len(formatted))
            return formatted
        except Exception:
            logger.error("query_docs failed:\n%s", traceback.format_exc())
            raise

    return mcp


def main():
    """Entry point: create server and run with the configured transport."""
    server = create_server()
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()

    logger.info("Starting kb_server: transport=%s, fastmcp=%s, mcp_sdk=%s",
                transport, _get_version("fastmcp"), _get_version("mcp"))

    if transport == "streamable-http":
        server.run(transport="streamable-http", host="0.0.0.0", port=8000, stateless_http=True)
    else:
        server.run()  # stdio


def _get_version(package: str) -> str:
    """Get installed package version, or 'unknown' if not found."""
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return "unknown"


# Module-level server instance for FastMCP stdio transport.
# Only created when run as a script (not when importing format_results for tests).
if __name__ == "__main__":
    main()
