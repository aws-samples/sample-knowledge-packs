"""Property-based tests for sync_docs.py — file filtering correctness.

Feature: knowledge-pack, Property 1: Sync script file filtering correctness

Uses Hypothesis to generate random directory trees with mixed file types
and paths, then verifies collect_markdown_files returns only .md files
from docs/dg/dev/ and docs/ca/dev/.

Validates: Requirements 3.2
"""

import os

from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.sync_docs import collect_markdown_files

# --- Strategies ---

# File extensions: mix of markdown and non-markdown
_MD_EXT = st.just(".md")
_NON_MD_EXTS = st.sampled_from([".txt", ".png", ".json", ".html", ".rst", ".yaml", ""])

# Target prefixes where .md files SHOULD be collected
_TARGET_PREFIXES = ["docs/dg/dev/", "docs/ca/dev/"]

# Non-target directory prefixes (files here should be excluded)
_NON_TARGET_PREFIXES = st.sampled_from(
    [
        "docs/dg/other/",
        "docs/ca/other/",
        "docs/other/",
        "docs/",
        "src/",
        "",
        "docs/dg/",
        "docs/ca/",
        "README",
    ]
)

# Simple filename stems (safe for filesystem)
_FILENAME_STEM = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)

# Optional subdirectory depth within a prefix
_SUBDIR = st.lists(
    st.from_regex(r"[a-z][a-z0-9]{0,5}", fullmatch=True),
    min_size=0,
    max_size=3,
)


@st.composite
def file_entry(draw):
    """Generate a single file entry: (relative_path, is_target_md).

    is_target_md is True when the file is a .md under a target prefix.
    """
    # Decide: target prefix or non-target prefix
    use_target = draw(st.booleans())

    if use_target:
        prefix = draw(st.sampled_from(_TARGET_PREFIXES))
    else:
        prefix = draw(_NON_TARGET_PREFIXES)

    # Build subdirectory path
    subdirs = draw(_SUBDIR)
    subdir_path = "/".join(subdirs) + "/" if subdirs else ""

    # Filename
    stem = draw(_FILENAME_STEM)

    # Extension: sometimes .md, sometimes not
    is_md = draw(st.booleans())
    ext = ".md" if is_md else draw(_NON_MD_EXTS)

    rel_path = f"{prefix}{subdir_path}{stem}{ext}"

    # A file is a "target md" only if it's .md AND under a target prefix
    is_target_md = use_target and is_md

    return (rel_path, is_target_md)


@st.composite
def directory_tree(draw):
    """Generate a list of file entries representing a directory tree.

    Returns list of (relative_path, is_target_md) tuples.
    Ensures no path conflicts where a file path is also used as a directory
    prefix by another entry (e.g. 'docs/dg/a' and 'docs/dg/a/b.txt').
    """
    entries = draw(st.lists(file_entry(), min_size=0, max_size=30))
    # Deduplicate by path (keep first occurrence)
    seen = set()
    unique = []
    for path, is_target in entries:
        if path not in seen:
            seen.add(path)
            unique.append((path, is_target))

    # Remove entries where a path is used as both a file and a directory prefix.
    # A file at "docs/dg/a" conflicts with "docs/dg/a/b.txt" because the OS
    # can't have "a" be both a file and a directory.
    all_paths = {p for p, _ in unique}
    filtered = []
    for path, is_target in unique:
        # Check if any other path treats this path as a directory
        is_dir_of_another = any(
            other.startswith(path + "/") for other in all_paths if other != path
        )
        # Check if any ancestor of this path is also a file
        parts = path.split("/")
        ancestor_is_file = any(
            "/".join(parts[:i]) in all_paths
            for i in range(1, len(parts))
        )
        if not is_dir_of_another and not ancestor_is_file:
            filtered.append((path, is_target))

    return filtered


def _create_file(base: str, rel_path: str) -> None:
    """Create a file at base/rel_path with dummy content."""
    full = os.path.join(base, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write("# placeholder\n")


# --- Property Test ---


@given(tree=directory_tree())
@settings(max_examples=200)
def test_file_filtering_correctness(tmp_path_factory, tree):
    """Property 1: Sync script file filtering correctness.

    For any directory tree with mixed file types and paths, collect_markdown_files
    returns only .md files whose paths start with docs/dg/dev/ or docs/ca/dev/,
    and no others.

    **Validates: Requirements 3.2**
    """
    repo_dir = str(tmp_path_factory.mktemp("repo"))

    # Build the directory tree on disk
    for rel_path, _is_target in tree:
        _create_file(repo_dir, rel_path)

    # Run the function under test — with the prefixes used in production
    result = collect_markdown_files(repo_dir, include_prefixes=["docs/dg/dev/", "docs/ca/dev/"])

    # Compute expected: only .md files under target prefixes
    expected = sorted(
        rel_path
        for rel_path, is_target in tree
        if is_target
    )

    # Property: result matches expected exactly
    assert result == expected, (
        f"Mismatch!\n  result:   {result}\n  expected: {expected}"
    )

    # Additional sub-properties for clarity:

    # Every returned path ends with .md
    for path in result:
        assert path.endswith(".md"), f"Non-.md file in result: {path}"

    # Every returned path starts with a target prefix
    for path in result:
        assert any(
            path.startswith(p) for p in _TARGET_PREFIXES
        ), f"File outside target dirs in result: {path}"

    # Result is sorted
    assert result == sorted(result), "Result is not sorted"
