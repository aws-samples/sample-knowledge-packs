"""Validation script for the Knowledge Pack.

Runs predefined queries from a JSON config file against the Knowledge Base
and reports results with source locations and relevance scores.
Exits with non-zero code if any query returns zero results.

Usage:
    python -m scripts.validate_kb                          # KB_ID from env
    python -m scripts.validate_kb --kb-id <id>             # KB_ID from CLI
    python -m scripts.validate_kb --queries other.json     # custom queries file
"""

import argparse
import json
import os
import sys
from pathlib import Path

import boto3

from scripts.kb_client import KBClient


def load_queries(queries_file: str) -> list[dict]:
    """Load validation queries from a JSON config file.

    Args:
        queries_file: Path to the JSON file containing query objects.

    Returns:
        List of query dicts, each with 'question' and 'expected_topic' keys.

    Raises:
        SystemExit: If the file is missing, unreadable, or contains invalid JSON.
    """
    path = Path(queries_file)
    if not path.exists():
        print(f"Error: queries file not found: {queries_file}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(path) as f:
            queries = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in {queries_file}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(queries, list):
        print(f"Error: expected a JSON array in {queries_file}", file=sys.stderr)
        sys.exit(1)

    return queries

def report_ingestion_stats(kb_id: str, ds_id: str) -> dict:
    """Fetch and report the latest ingestion job statistics.

    Calls bedrock-agent list_ingestion_jobs() and extracts stats from the
    most recent completed job.

    Returns dict with keys: scanned, indexed, failed, success_rate.
    """
    client = boto3.client("bedrock-agent", region_name="eu-west-1")

    response = client.list_ingestion_jobs(
        knowledgeBaseId=kb_id,
        dataSourceId=ds_id,
        sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
    )

    jobs = response.get("ingestionJobSummaries", [])
    if not jobs:
        print("\n⚠️  No ingestion jobs found.")
        return {"scanned": 0, "indexed": 0, "failed": 0, "success_rate": 0.0}

    # Find the latest completed job; fall back to the most recent job overall
    latest = None
    for job in jobs:
        if job.get("status") == "COMPLETE":
            latest = job
            break
    if latest is None:
        latest = jobs[0]

    stats = latest.get("statistics", {})
    scanned = stats.get("numberOfDocumentsScanned", 0)
    new_indexed = stats.get("numberOfNewDocumentsIndexed", 0)
    modified_indexed = stats.get("numberOfModifiedDocumentsIndexed", 0)
    indexed = new_indexed + modified_indexed
    failed = stats.get("numberOfDocumentsFailed", 0)
    success_rate = (indexed / scanned * 100) if scanned > 0 else 0.0

    print(f"\n{'='*60}")
    print("Ingestion Statistics (latest job)")
    print(f"{'='*60}")
    print(f"  Status:       {latest.get('status', 'N/A')}")
    print(f"  Started:      {latest.get('startedAt', 'N/A')}")
    print(f"  Scanned:      {scanned}")
    print(f"  Indexed:      {indexed} (new: {new_indexed}, modified: {modified_indexed})")
    print(f"  Failed:       {failed}")
    print(f"  Success rate: {success_rate:.1f}%")

    return {
        "scanned": scanned,
        "indexed": indexed,
        "failed": failed,
        "success_rate": success_rate,
    }


def run_validation(kb_id: str, queries_file: str = "validation_queries.json") -> bool:
    """Run all validation queries and print results.

    Args:
        kb_id: The Bedrock Knowledge Base ID.
        queries_file: Path to the JSON config file with queries.

    Returns:
        True if all queries returned at least one result, False otherwise.
    """
    queries = load_queries(queries_file)

    if not queries:
        print("Warning: no queries found in config file", file=sys.stderr)
        return True

    client = KBClient(kb_id, region="eu-west-1")
    all_passed = True

    for i, query_obj in enumerate(queries, start=1):
        question = query_obj.get("question", "")
        expected_topic = query_obj.get("expected_topic", "unknown")

        print(f"\n{'='*60}")
        print(f"Query {i}/{len(queries)}: {question}")
        print(f"Expected topic: {expected_topic}")
        print(f"{'='*60}")

        results = client.retrieve(question)

        if not results:
            print("  ❌ NO RESULTS RETURNED")
            all_passed = False
            continue

        print(f"  ✅ {len(results)} result(s) returned\n")

        for j, result in enumerate(results, start=1):
            # Extract content text
            content = result.get("content", {}).get("text", "N/A")
            snippet = content[:200] + "..." if len(content) > 200 else content

            # Extract source location (S3 URI)
            location = (
                result.get("location", {})
                .get("s3Location", {})
                .get("uri", "N/A")
            )

            # Extract relevance score
            score = result.get("score", "N/A")

            print(f"  Result {j}:")
            print(f"    Score:    {score}")
            print(f"    Source:   {location}")
            print(f"    Content:  {snippet}")
            print()

    return all_passed


def main():
    parser = argparse.ArgumentParser(
        description="Validate Knowledge Pack with test queries"
    )
    parser.add_argument(
        "--kb-id",
        default=os.environ.get("KNOWLEDGE_BASE_ID", ""),
        help="Knowledge Base ID (default: KNOWLEDGE_BASE_ID env var)",
    )
    parser.add_argument(
        "--ds-id",
        default=os.environ.get("DATA_SOURCE_ID", ""),
        help="Data Source ID (default: DATA_SOURCE_ID env var)",
    )
    parser.add_argument(
        "--queries",
        default="validation_queries.json",
        help="Path to validation queries JSON file (default: validation_queries.json)",
    )
    args = parser.parse_args()

    if not args.kb_id:
        print(
            "Error: Knowledge Base ID required. Set KNOWLEDGE_BASE_ID env var "
            "or pass --kb-id",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Validating Knowledge Base: {args.kb_id}")
    print(f"Queries file: {args.queries}")

    passed = run_validation(args.kb_id, args.queries)

    # Report ingestion stats if data source ID is available
    if args.ds_id:
        report_ingestion_stats(args.kb_id, args.ds_id)
    else:
        print("\n⚠️  Skipping ingestion stats (no --ds-id provided)")

    if passed:
        print("\n✅ All validation queries returned results.")
        sys.exit(0)
    else:
        print("\n❌ Some validation queries returned no results.", file=sys.stderr)
        sys.exit(1)



if __name__ == "__main__":
    main()
