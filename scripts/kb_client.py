"""Shared Bedrock Knowledge Base retrieval client.

Used by both the validation script and MCP server to query the
Knowledge Base via the Bedrock Retrieve API.
"""

import boto3
from botocore.exceptions import ClientError


class KBClient:
    """Client for querying an AWS Bedrock Knowledge Base."""

    def __init__(self, knowledge_base_id: str, region: str = "eu-west-1"):
        if not knowledge_base_id or not knowledge_base_id.strip():
            raise ValueError("knowledge_base_id must be a non-empty string")

        self._knowledge_base_id = knowledge_base_id
        self._client = boto3.client("bedrock-agent-runtime", region_name=region)

    def retrieve(self, query: str, num_results: int = 5) -> list[dict]:
        """Query the Knowledge Base and return results as boto3 response dicts.

        Args:
            query: Natural language query string.
            num_results: Maximum number of results to return (default 5).

        Returns:
            List of retrieval result dicts from the API response.
            Empty list when no results match (no exception raised).

        Raises:
            ValueError: If query is empty or knowledge base ID is invalid.
        """
        if not query or not query.strip():
            raise ValueError("query must be a non-empty string")

        try:
            response = self._client.retrieve(
                knowledgeBaseId=self._knowledge_base_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": num_results,
                    }
                },
            )
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            # ResourceNotFoundException means the KB ID doesn't exist
            if error_code == "ResourceNotFoundException":
                raise ValueError(
                    f"Knowledge Base not found: {self._knowledge_base_id}"
                ) from exc
            raise

        return response.get("retrievalResults", [])
