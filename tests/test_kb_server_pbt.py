"""Property-based tests for kb_server.py — result formatting.

Feature: knowledge-pack, Property 5: MCP server result formatting includes all source locations

Uses Hypothesis to generate random lists of result dicts, then verifies
format_results output contains every source location from the input.

Validates: Requirements 6.2
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.kb_server import format_results

# --- Strategies ---

# S3 URI components
_BUCKET_NAME = st.from_regex(r"[a-z][a-z0-9\-]{2,15}", fullmatch=True)
_KEY_SEGMENT = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)
_KEY_PATH = st.lists(_KEY_SEGMENT, min_size=1, max_size=4).map(
    lambda parts: "/".join(parts) + ".md"
)

_S3_URI = st.builds(
    lambda bucket, key: f"s3://{bucket}/{key}",
    _BUCKET_NAME,
    _KEY_PATH,
)

# Content text: non-empty printable strings
_CONTENT_TEXT = st.text(min_size=1, max_size=200).filter(lambda s: s.strip())

# Score: float between 0 and 1
_SCORE = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@st.composite
def result_dict(draw):
    """Generate a single retrieval result dict with content, location, and score."""
    content = draw(_CONTENT_TEXT)
    uri = draw(_S3_URI)
    score = draw(_SCORE)

    return {
        "content": {"text": content},
        "location": {"s3Location": {"uri": uri}},
        "score": score,
    }


_RESULT_LIST = st.lists(result_dict(), min_size=1, max_size=10)


# --- Property Test ---


@given(results=_RESULT_LIST)
@settings(max_examples=200)
def test_format_results_includes_all_source_locations(results):
    """Property 5: MCP server result formatting includes all source locations.

    For any list of retrieval result dicts, the formatted output must contain
    every source location (S3 URI) from the input results.

    **Validates: Requirements 6.2**
    """
    output = format_results(results)

    for result in results:
        uri = result["location"]["s3Location"]["uri"]
        assert uri in output, (
            f"Source location missing from formatted output: {uri}\n"
            f"Output:\n{output}"
        )
