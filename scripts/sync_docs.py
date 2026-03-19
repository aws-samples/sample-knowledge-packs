"""Sync documentation from a Git repository to S3 and trigger KB ingestion.

Clones or pulls a documentation repository, uploads markdown files from
configurable path prefixes to an S3 bucket, then triggers a Bedrock
Knowledge Base ingestion job.
"""

import argparse
import os
import re
import subprocess
import sys

import boto3

# Preprocessing regex patterns
FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
JEKYLL_BLOCK_RE = re.compile(r"\{%.*?%\}", re.DOTALL)
JEKYLL_OUTPUT_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)

MIN_BODY_LENGTH = 200


def preprocess_markdown(content: str) -> str | None:
    """Preprocess markdown content for Bedrock ingestion.

    Strips YAML frontmatter and Jekyll/Liquid template tags.
    Returns None if remaining body is < 200 chars (file should be skipped).
    Returns cleaned content otherwise.
    """
    # Step 1: Strip YAML frontmatter
    cleaned = FRONTMATTER_RE.sub("", content)

    # Step 2: Strip Jekyll/Liquid template tags
    cleaned = JEKYLL_BLOCK_RE.sub("", cleaned)
    cleaned = JEKYLL_OUTPUT_RE.sub("", cleaned)

    # Step 3: Check minimum content length
    if len(cleaned.strip()) < MIN_BODY_LENGTH:
        return None

    return cleaned


def collect_markdown_files(repo_dir: str, include_prefixes: list[str] | None = None) -> list[str]:
    """Collect .md files from specified directories (or entire repo if none given).

    Returns relative paths (relative to repo_dir) of the markdown files found.
    """
    md_files: list[str] = []
    if include_prefixes:
        search_dirs = [os.path.join(repo_dir, p) for p in include_prefixes]
    else:
        search_dirs = [repo_dir]
    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        for root, _dirs, files in os.walk(search_dir):
            for fname in files:
                if fname.endswith(".md"):
                    full_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(full_path, repo_dir)
                    md_files.append(rel_path)
    return sorted(md_files)


def clone_or_pull(repo_url: str, repo_dir: str) -> None:
    """Clone the docs repo, or pull if it already exists."""
    if not repo_url.startswith(("https://", "git@")):
        raise ValueError(f"Invalid repo URL: {repo_url}")
    if os.path.isdir(repo_dir):
        print(f"Updating existing repo in {repo_dir}...")
        subprocess.run(
            ["git", "pull", "--progress"],
            cwd=repo_dir,
            check=True,
        )
    else:
        print(f"Cloning {repo_url} into {repo_dir}...")
        subprocess.run(
            ["git", "clone", "--progress", repo_url, repo_dir],
            check=True,
        )


def upload_to_s3(bucket_name: str, repo_dir: str, files: list[str]) -> tuple[int, int]:
    """Upload preprocessed markdown files to S3, preserving directory structure as keys.

    Each file is preprocessed (frontmatter/Jekyll stripped) before upload.
    Files that don't pass preprocessing are skipped.

    Returns (uploaded_count, skipped_count).
    """
    s3 = boto3.client("s3", region_name="eu-west-1")
    uploaded = 0
    skipped = 0
    total = len(files)
    for rel_path in files:
        local_path = os.path.join(repo_dir, rel_path)
        with open(local_path, "r", errors="replace") as f:
            raw_content = f.read()

        cleaned = preprocess_markdown(raw_content)
        if cleaned is None:
            skipped += 1
            continue

        s3_key = rel_path
        s3.put_object(Bucket=bucket_name, Key=s3_key, Body=cleaned.encode("utf-8"))
        uploaded += 1
        if uploaded % 50 == 0:
            print(f"  Uploaded {uploaded}/{total} files ({skipped} skipped)...")
    return uploaded, skipped


def start_ingestion(kb_id: str, ds_id: str) -> str | None:
    """Trigger a Bedrock Knowledge Base ingestion job.

    Returns the ingestion job ID on success, or None on failure.
    """
    client = boto3.client("bedrock-agent", region_name="eu-west-1")
    try:
        response = client.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
        )
        job_id = response["ingestionJob"]["ingestionJobId"]
        print(f"Ingestion job started: {job_id}")
        return job_id
    except Exception as e:
        # Log warning but don't fail — upload already succeeded
        print(f"Warning: Failed to start ingestion job: {e}", file=sys.stderr)
        return None


def sync_docs(
    repo_url: str,
    bucket_name: str,
    kb_id: str,
    ds_id: str,
    repo_dir: str = "./docs-repo",
    include_prefixes: list[str] | None = None,
) -> None:
    """Clone/pull docs, upload to S3, trigger KB sync job."""
    clone_or_pull(repo_url, repo_dir)
    md_files = collect_markdown_files(repo_dir, include_prefixes)
    print(f"Found {len(md_files)} markdown files to upload")

    if not md_files:
        print("No markdown files found. Nothing to upload.")
        return

    # Step 3: Upload to S3 (with preprocessing)
    uploaded, skipped = upload_to_s3(bucket_name, repo_dir, md_files)
    print(f"Uploaded {uploaded} files, skipped {skipped} files (too small after preprocessing)")
    print(f"Upload target: s3://{bucket_name}/")

    # Step 4: Trigger ingestion job
    start_ingestion(kb_id, ds_id)


def main() -> None:
    """CLI entry point — reads config from env vars or CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Sync docs from a Git repo to S3 and trigger KB ingestion",
    )
    parser.add_argument(
        "--repo-url",
        default=os.environ.get("REPO_URL"),
        help="Git repository URL to clone (or set REPO_URL env var)",
    )
    parser.add_argument(
        "--include-prefix",
        action="append",
        dest="include_prefixes",
        help="Path prefix to include (repeatable). If omitted, all .md files are included.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("S3_BUCKET"),
        help="S3 bucket name (or set S3_BUCKET env var)",
    )
    parser.add_argument(
        "--kb-id",
        default=os.environ.get("KB_ID"),
        help="Bedrock Knowledge Base ID (or set KB_ID env var)",
    )
    parser.add_argument(
        "--ds-id",
        default=os.environ.get("DS_ID"),
        help="Bedrock Data Source ID (or set DS_ID env var)",
    )
    parser.add_argument(
        "--repo-dir",
        default=os.environ.get("REPO_DIR", "./docs-repo"),
        help="Local directory for the cloned repo (default: ./docs-repo)",
    )

    args = parser.parse_args()

    if not args.repo_url:
        parser.error("--repo-url is required (or set REPO_URL env var)")
    if not args.bucket:
        parser.error("--bucket is required (or set S3_BUCKET env var)")
    if not args.kb_id:
        parser.error("--kb-id is required (or set KB_ID env var)")
    if not args.ds_id:
        parser.error("--ds-id is required (or set DS_ID env var)")

    sync_docs(
        repo_url=args.repo_url,
        bucket_name=args.bucket,
        kb_id=args.kb_id,
        ds_id=args.ds_id,
        repo_dir=args.repo_dir,
        include_prefixes=args.include_prefixes,
    )


if __name__ == "__main__":
    main()
