import os

# Set environment variables before importing function_app to avoid KeyErrors
os.environ.setdefault(
    "STORAGE_CONNECTION_STRING",
    "DefaultEndpointsProtocol=https;AccountName=dummy;AccountKey=dummy;EndpointSuffix=core.windows.net",
)
os.environ.setdefault(
    "DOCUMENT_INTELLIGENCE_ENDPOINT", "https://dummy.cognitiveservices.azure.com/"
)
os.environ.setdefault("DOCUMENT_INTELLIGENCE_KEY", "dummy_key")
os.environ.setdefault("OPENAI_ENDPOINT", "https://dummy.openai.azure.com/")
os.environ.setdefault("OPENAI_API_KEY", "dummy_openai_key")

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from function_app import download_blob_content, prepare_batch_requests
from models import BatchRequest


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    """Mock environment variables and service clients for all tests in this module."""
    monkeypatch.setenv(
        "STORAGE_CONNECTION_STRING",
        "DefaultEndpointsProtocol=https;AccountName=dummy;AccountKey=dummy;EndpointSuffix=core.windows.net",
    )
    monkeypatch.setenv(
        "DOCUMENT_INTELLIGENCE_ENDPOINT", "https://dummy.cognitiveservices.azure.com/"
    )
    monkeypatch.setenv("DOCUMENT_INTELLIGENCE_KEY", "dummy_key")
    monkeypatch.setenv("OPENAI_ENDPOINT", "https://dummy.openai.azure.com/")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy_openai_key")

    # Mock the clients to prevent them from being initialized
    mocker.patch("azure.storage.blob.BlobServiceClient.from_connection_string")
    mocker.patch("azure.ai.documentintelligence.DocumentIntelligenceClient")
    mocker.patch("azure.data.tables.TableServiceClient.from_connection_string")
    mocker.patch("openai.AzureOpenAI")
    mocker.patch("azure.storage.queue.QueueServiceClient.from_connection_string")


# A mock result object that simulates the structure of DocumentIntelligenceClient's result
class MockKeyValue:
    def __init__(self, key: str, value: str) -> None:
        self.key: Any = MagicMock()
        self.key.content = key
        self.value: Any = MagicMock()
        self.value.content = value


@dataclass
class MockDocumentAnalysisResult:
    key_value_pairs: list[MockKeyValue]


def test_prepare_batch_requests_with_data() -> None:
    """
    Test that prepare_batch_requests correctly formats data from a
    DocumentAnalysisResult into OpenAI Batch API requests.
    """
    # Arrange: Create a mock analysis result with some key-value pairs
    mock_result: Any = MockDocumentAnalysisResult(
        key_value_pairs=[
            MockKeyValue("Name", "Jules"),
            MockKeyValue("Company", "ACME Inc."),
        ]
    )
    blob_name = "test_document.pdf"

    # Act: Call the function to prepare batch requests
    batch_requests = prepare_batch_requests(mock_result, blob_name)

    # Assert: Check that the output is as expected
    assert len(batch_requests) == 2

    # Validate the first request
    req1 = BatchRequest.model_validate_json(batch_requests[0])
    assert req1.custom_id == f"{blob_name}-0"
    assert req1.method == "POST"
    assert req1.url == "/v1/chat/completions"
    assert "Key: 'Name'" in req1.body.messages[1]["content"]
    assert "Value: 'Jules'" in req1.body.messages[1]["content"]

    # Validate the second request
    req2 = BatchRequest.model_validate_json(batch_requests[1])
    assert req2.custom_id == f"{blob_name}-1"
    assert "Key: 'Company'" in req2.body.messages[1]["content"]
    assert "Value: 'ACME Inc.'" in req2.body.messages[1]["content"]


def test_prepare_batch_requests_with_no_data() -> None:
    """
    Test that prepare_batch_requests returns an empty list when the
    document analysis result has no key-value pairs.
    """
    # Arrange: Create a mock analysis result with no key-value pairs
    mock_result: Any = MockDocumentAnalysisResult(key_value_pairs=[])
    blob_name = "empty_document.pdf"

    # Act: Call the function
    batch_requests = prepare_batch_requests(mock_result, blob_name)

    # Assert: The result should be an empty list
    assert len(batch_requests) == 0


def test_download_blob_content_success() -> None:
    """
    Test that download_blob_content successfully downloads and returns blob content.
    """
    # Arrange: Mock the ContainerClient and its methods
    mock_container_client: Any = MagicMock()
    mock_blob_client: Any = MagicMock()
    mock_blob_content = b"This is a test blob."

    # Configure the mocks
    mock_container_client.get_blob_client.return_value = mock_blob_client
    mock_blob_client.download_blob.return_value.readall.return_value = mock_blob_content

    # Act: Call the function with the mocked client
    result = download_blob_content(mock_container_client, "test.txt")

    # Assert: The function should return the mocked blob content
    assert result == mock_blob_content
    mock_container_client.get_blob_client.assert_called_once_with("test.txt")
    mock_blob_client.download_blob.assert_called_once()


def test_download_blob_content_failure() -> None:
    """
    Test that download_blob_content returns None when a download error occurs.
    """
    # Arrange: Mock the ContainerClient to raise an exception
    mock_container_client: Any = MagicMock()
    mock_container_client.get_blob_client.side_effect = Exception("Test Exception")

    # Act: Call the function
    result = download_blob_content(mock_container_client, "nonexistent.txt")

    # Assert: The function should return None
    assert result is None
