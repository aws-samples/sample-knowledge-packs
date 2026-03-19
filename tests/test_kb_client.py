"""Unit tests for scripts/kb_client.py — KBClient input validation.

Tests error handling for missing/invalid knowledge base ID, empty queries,
and ResourceNotFoundException from the Bedrock API.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from scripts.kb_client import KBClient


# --- Constructor validation ---


class TestKBClientInit:
    """Tests for KBClient.__init__ input validation."""

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            KBClient("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            KBClient("   ")

    def test_none_raises_value_error(self):
        with pytest.raises(ValueError, match="non-empty"):
            KBClient(None)


# --- retrieve() query validation ---


class TestRetrieveQueryValidation:
    """Tests for KBClient.retrieve() query input validation."""

    @patch("scripts.kb_client.boto3")
    def test_empty_query_raises_value_error(self, mock_boto3):
        client = KBClient("valid-kb-id")
        with pytest.raises(ValueError, match="non-empty"):
            client.retrieve("")

    @patch("scripts.kb_client.boto3")
    def test_whitespace_query_raises_value_error(self, mock_boto3):
        client = KBClient("valid-kb-id")
        with pytest.raises(ValueError, match="non-empty"):
            client.retrieve("   \t\n  ")

    @patch("scripts.kb_client.boto3")
    def test_none_query_raises_value_error(self, mock_boto3):
        client = KBClient("valid-kb-id")
        with pytest.raises(ValueError, match="non-empty"):
            client.retrieve(None)


# --- retrieve() ResourceNotFoundException handling ---


class TestRetrieveInvalidKBId:
    """Tests that ResourceNotFoundException is converted to ValueError."""

    @patch("scripts.kb_client.boto3")
    def test_resource_not_found_raises_value_error(self, mock_boto3):
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        error_response = {
            "Error": {"Code": "ResourceNotFoundException", "Message": "KB not found"}
        }
        mock_client.retrieve.side_effect = ClientError(error_response, "Retrieve")

        client = KBClient("nonexistent-kb-id")
        with pytest.raises(ValueError, match="Knowledge Base not found"):
            client.retrieve("some query")

    @patch("scripts.kb_client.boto3")
    def test_other_client_error_propagates(self, mock_boto3):
        """Non-ResourceNotFoundException ClientErrors should propagate as-is."""
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        error_response = {
            "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}
        }
        mock_client.retrieve.side_effect = ClientError(error_response, "Retrieve")

        client = KBClient("some-kb-id")
        with pytest.raises(ClientError):
            client.retrieve("some query")
