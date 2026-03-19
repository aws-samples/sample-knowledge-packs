"""Property-based tests for preprocess_markdown() — Properties 6 and 7.

Feature: knowledge-pack

Uses Hypothesis to verify that preprocessing preserves non-template content
and correctly skips files below the minimum content threshold.

Validates: Requirements 7.2, 7.3, 7.4
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.sync_docs import preprocess_markdown, MIN_BODY_LENGTH

# --- Strategies ---

# Generate safe markdown body text that contains NO Jekyll/Liquid tags
# and NO frontmatter delimiters. Uses printable chars minus { and }.
_SAFE_CHARS = st.text(
    alphabet=st.characters(
        categories=("L", "N", "P", "Z"),
        exclude_characters="{}\r",
    ),
    min_size=0,
    max_size=500,
)

# YAML frontmatter block (valid: starts at beginning, between two --- lines)
_FRONTMATTER_VALUE = st.text(
    alphabet=st.characters(categories=("L", "N", "P", "Z"), exclude_characters="-{}\r"),
    min_size=1,
    max_size=100,
)


@st.composite
def frontmatter_block(draw):
    """Generate a valid YAML frontmatter block."""
    key = draw(st.from_regex(r"[a-z_]{1,10}", fullmatch=True))
    value = draw(_FRONTMATTER_VALUE)
    return f"---\n{key}: {value}\n---\n"


@st.composite
def markdown_without_jekyll(draw):
    """Generate markdown content with optional frontmatter but NO Jekyll tags.

    Returns (full_content, expected_body) where expected_body is the content
    after frontmatter removal.
    """
    has_frontmatter = draw(st.booleans())
    body = draw(_SAFE_CHARS)

    if has_frontmatter:
        fm = draw(frontmatter_block())
        full_content = fm + body
    else:
        full_content = body

    return (full_content, body)


# Jekyll/Liquid tag generators
_JEKYLL_BLOCK_TAG = st.from_regex(r"\{% [a-z_]{1,10} %\}", fullmatch=True)
_JEKYLL_OUTPUT_TAG = st.from_regex(r"\{\{ [a-z_.]{1,10} \}\}", fullmatch=True)


@st.composite
def markdown_with_known_body_length(draw):
    """Generate markdown with optional frontmatter and Jekyll tags,
    where we know the exact body length after all stripping.

    Returns (full_content, clean_body_length) where clean_body_length
    is len(body.strip()) after removing frontmatter and Jekyll tags.
    """
    has_frontmatter = draw(st.booleans())

    # Generate body parts: alternating safe text and optional Jekyll tags
    num_parts = draw(st.integers(min_value=1, max_value=5))
    body_text_parts = []
    jekyll_parts = []

    for _ in range(num_parts):
        text_part = draw(_SAFE_CHARS)
        body_text_parts.append(text_part)

        add_jekyll = draw(st.booleans())
        if add_jekyll:
            tag = draw(st.one_of(_JEKYLL_BLOCK_TAG, _JEKYLL_OUTPUT_TAG))
            jekyll_parts.append(tag)
        else:
            jekyll_parts.append("")

    # Interleave text and Jekyll tags to build the body
    body_with_jekyll = ""
    for i in range(num_parts):
        body_with_jekyll += body_text_parts[i] + jekyll_parts[i]

    # The clean body is just the text parts concatenated (no Jekyll)
    clean_body = "".join(body_text_parts)

    if has_frontmatter:
        fm = draw(frontmatter_block())
        full_content = fm + body_with_jekyll
    else:
        full_content = body_with_jekyll

    return (full_content, len(clean_body.strip()))


# --- Property Tests ---


@given(data=markdown_without_jekyll())
@settings(max_examples=200)
def test_property6_preserves_non_template_content(data):
    """Property 6: Preprocessing preserves all non-template content.

    For any markdown content that contains no Jekyll/Liquid template tags,
    preprocess_markdown() SHALL return content that is identical to the input
    with only the YAML frontmatter removed.

    **Validates: Requirements 7.2, 7.3**
    """
    full_content, expected_body = data

    result = preprocess_markdown(full_content)

    if len(expected_body.strip()) < MIN_BODY_LENGTH:
        # Below threshold → should return None (Property 7 covers this)
        assert result is None, (
            f"Expected None for body < {MIN_BODY_LENGTH} chars, "
            f"got: {result!r:.100}"
        )
    else:
        # Above threshold → result should be exactly the body (frontmatter stripped)
        assert result is not None, "Expected non-None result for body >= 200 chars"
        assert result == expected_body, (
            f"Content mismatch!\n"
            f"  result:   {result!r:.200}\n"
            f"  expected: {expected_body!r:.200}"
        )


@given(data=markdown_with_known_body_length())
@settings(max_examples=200)
def test_property7_skips_below_minimum_threshold(data):
    """Property 7: Preprocessing skips files below minimum content threshold.

    For any markdown content where the body (after stripping frontmatter and
    Jekyll tags) is less than 200 characters, preprocess_markdown() SHALL
    return None. For any content where the body is 200 characters or more,
    it SHALL return a non-None string.

    **Validates: Requirements 7.4**
    """
    full_content, clean_body_length = data

    result = preprocess_markdown(full_content)

    if clean_body_length < MIN_BODY_LENGTH:
        assert result is None, (
            f"Expected None for clean body length {clean_body_length} "
            f"(< {MIN_BODY_LENGTH}), got non-None"
        )
    else:
        assert result is not None, (
            f"Expected non-None for clean body length {clean_body_length} "
            f"(>= {MIN_BODY_LENGTH}), got None"
        )
