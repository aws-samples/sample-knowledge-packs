"""Unit tests for scripts/sync_docs.py — collect_markdown_files function."""

import os

from scripts.sync_docs import collect_markdown_files, preprocess_markdown, MIN_BODY_LENGTH


def _create_file(base: str, rel_path: str, content: str = "# test") -> None:
    """Helper to create a file inside a directory tree."""
    full = os.path.join(base, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


def test_collects_md_from_dg_dev(tmp_path):
    repo = str(tmp_path / "repo")
    _create_file(repo, "docs/dg/dev/guide.md")
    _create_file(repo, "docs/dg/dev/sub/nested.md")

    result = collect_markdown_files(repo)
    assert result == ["docs/dg/dev/guide.md", "docs/dg/dev/sub/nested.md"]


def test_collects_md_from_ca_dev(tmp_path):
    repo = str(tmp_path / "repo")
    _create_file(repo, "docs/ca/dev/cloud.md")

    result = collect_markdown_files(repo)
    assert result == ["docs/ca/dev/cloud.md"]


def test_collects_from_both_directories(tmp_path):
    repo = str(tmp_path / "repo")
    _create_file(repo, "docs/dg/dev/a.md")
    _create_file(repo, "docs/ca/dev/b.md")

    result = collect_markdown_files(repo)
    assert "docs/dg/dev/a.md" in result
    assert "docs/ca/dev/b.md" in result
    assert len(result) == 2


def test_excludes_non_md_files(tmp_path):
    repo = str(tmp_path / "repo")
    _create_file(repo, "docs/dg/dev/guide.md")
    _create_file(repo, "docs/dg/dev/image.png", content="binary")
    _create_file(repo, "docs/dg/dev/data.json", content="{}")

    result = collect_markdown_files(repo)
    assert result == ["docs/dg/dev/guide.md"]


def test_excludes_files_outside_target_dirs(tmp_path):
    repo = str(tmp_path / "repo")
    _create_file(repo, "docs/dg/dev/included.md")
    _create_file(repo, "docs/other/excluded.md")
    _create_file(repo, "README.md")
    _create_file(repo, "docs/dg/other.md")

    result = collect_markdown_files(repo, include_prefixes=["docs/dg/dev/", "docs/ca/dev/"])
    assert result == ["docs/dg/dev/included.md"]


def test_returns_empty_for_missing_dirs(tmp_path):
    repo = str(tmp_path / "repo")
    os.makedirs(repo)

    result = collect_markdown_files(repo)
    assert result == []


def test_returns_sorted_paths(tmp_path):
    repo = str(tmp_path / "repo")
    _create_file(repo, "docs/dg/dev/z.md")
    _create_file(repo, "docs/dg/dev/a.md")
    _create_file(repo, "docs/ca/dev/m.md")

    result = collect_markdown_files(repo)
    assert result == sorted(result)


# --- preprocess_markdown tests ---


class TestPreprocessMarkdownFrontmatter:
    """Tests for YAML frontmatter stripping."""

    def test_strips_frontmatter(self):
        content = "---\ntitle: Test\nredirect_from:\n  - /old\n---\n# Hello World\n" + "x" * 200
        result = preprocess_markdown(content)
        assert result is not None
        assert "---" not in result
        assert "title:" not in result
        assert "# Hello World" in result

    def test_no_frontmatter_passes_through(self):
        content = "# Just a heading\n" + "Some content. " * 20
        result = preprocess_markdown(content)
        assert result is not None
        assert "# Just a heading" in result

    def test_frontmatter_only_file_returns_none(self):
        content = "---\ntitle: Empty\n---\nshort"
        result = preprocess_markdown(content)
        assert result is None

    def test_frontmatter_with_redirect_from_list(self):
        frontmatter = "---\ntitle: Page\nredirect_from:\n" + "".join(
            f"  - /redirect/{i}\n" for i in range(50)
        ) + "---\n"
        body = "# Real content\n" + "Substantive text. " * 20
        result = preprocess_markdown(frontmatter + body)
        assert result is not None
        assert "redirect_from" not in result
        assert "# Real content" in result


class TestPreprocessMarkdownJekyll:
    """Tests for Jekyll/Liquid template tag stripping."""

    def test_strips_block_tags(self):
        content = "# Title\n{% include some_template.html %}\n" + "Body content. " * 20
        result = preprocess_markdown(content)
        assert result is not None
        assert "{%" not in result
        assert "%}" not in result
        assert "# Title" in result

    def test_strips_output_tags(self):
        content = "# Title\nHello {{ page.title }} world\n" + "More content. " * 20
        result = preprocess_markdown(content)
        assert result is not None
        assert "{{" not in result
        assert "}}" not in result
        assert "Hello" in result
        assert "world" in result

    def test_strips_multiline_block_tags(self):
        content = "# Title\n{% if condition\n   and more %}\nVisible\n{% endif %}\n" + "x" * 200
        result = preprocess_markdown(content)
        assert result is not None
        assert "{%" not in result

    def test_file_with_only_jekyll_includes_returns_none(self):
        content = "{% include header.html %}\n{% include footer.html %}\n"
        result = preprocess_markdown(content)
        assert result is None


class TestPreprocessMarkdownMinLength:
    """Tests for minimum content length threshold."""

    def test_returns_none_below_threshold(self):
        content = "# Short\nToo little."
        result = preprocess_markdown(content)
        assert result is None

    def test_returns_content_at_threshold(self):
        body = "a" * MIN_BODY_LENGTH
        result = preprocess_markdown(body)
        assert result is not None
        assert result.strip() == body

    def test_returns_none_when_body_below_threshold_after_stripping(self):
        frontmatter = "---\ntitle: Big Frontmatter\n" + "key: value\n" * 50 + "---\n"
        body = "tiny"
        result = preprocess_markdown(frontmatter + body)
        assert result is None

    def test_whitespace_only_after_stripping_returns_none(self):
        content = "---\ntitle: Test\n---\n   \n\n  \n"
        result = preprocess_markdown(content)
        assert result is None


class TestPreprocessMarkdownCombined:
    """Tests combining frontmatter, Jekyll tags, and length checks."""

    def test_strips_frontmatter_and_jekyll_tags(self):
        content = (
            "---\ntitle: Page\n---\n"
            "{% include header.html %}\n"
            "# Real Content\n"
            "{{ page.description }}\n"
        ) + "Substantive documentation text. " * 10
        result = preprocess_markdown(content)
        assert result is not None
        assert "title: Page" not in result
        assert "{%" not in result
        assert "{{" not in result
        assert "# Real Content" in result
        assert "Substantive documentation text." in result

    def test_empty_string_returns_none(self):
        assert preprocess_markdown("") is None

    def test_preserves_markdown_formatting(self):
        body = (
            "# Heading\n\n"
            "## Subheading\n\n"
            "- List item 1\n"
            "- List item 2\n\n"
            "```python\nprint('hello')\n```\n\n"
            "Regular paragraph with enough content to pass the threshold. " * 5
        )
        result = preprocess_markdown(body)
        assert result is not None
        assert "# Heading" in result
        assert "## Subheading" in result
        assert "- List item 1" in result
        assert "```python" in result
