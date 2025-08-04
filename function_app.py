"""Azure Function App for Document Processing and OpenAI Batch Orchestration.

This script defines two main Azure Functions triggered by timers:
1. `doc_processing_func`: This function runs periodically to process documents.
   It retrieves messages from an Azure Queue, where each message points to a document
   in Azure Blob Storage. It uses Azure Document Intelligence to analyze the document,
   prepares the extracted data for the OpenAI Batch API, creates a batch job,
   and logs the job's metadata in Azure Table Storage for tracking.

2. `status_check_func`: This function runs on a schedule to check the status of
   pending OpenAI batch jobs. For completed jobs, it processes the results,
   performs a fuzzy string comparison on the extracted data, and sends the
   comparison results to another Azure Queue. It also handles failed or
   expired jobs by updating their status in Table Storage.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import azure.functions as func
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.data.tables import TableClient, TableServiceClient
from azure.storage.blob import BlobServiceClient, ContainerClient
from azure.storage.queue import QueueClient, QueueMessage, QueueServiceClient
from openai import AzureOpenAI
from thefuzz import fuzz

from models import (
    BatchRequest,
    BatchResponse,
    ChatRequestBody,
    ComparisonResult,
    ExtractedData,
)

# --- Configuration ---
# Load environment variables to configure the application's connection strings,
# container names, API keys, and other settings.

# Azure Storage settings
STORAGE_CONNECTION_STRING = os.environ["STORAGE_CONNECTION_STRING"]
UPLOADS_CONTAINER_NAME = os.environ.get("UPLOADS_CONTAINER_NAME", "uploads")
BATCH_INPUTS_CONTAINER_NAME = os.environ.get(
    "BATCH_INPUTS_CONTAINER_NAME",
    "batch-inputs",
)
JOB_TRACKER_TABLE_NAME = os.environ.get("JOB_TRACKER_TABLE_NAME", "JobTracker")
QUEUE_NAME = os.environ.get("UPLOADS_QUEUE_NAME", "uploads-queue")
DEAD_LETTER_QUEUE_NAME = os.environ.get("UPLOADS_DLQ_NAME", "uploads-dead_letter")

# Azure Document Intelligence settings
DOC_INTELLIGENCE_ENDPOINT = os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"]
DOC_INTELLIGENCE_KEY = os.environ["DOCUMENT_INTELLIGENCE_KEY"]

# Azure OpenAI settings
OPENAI_ENDPOINT = os.environ["OPENAI_ENDPOINT"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_API_VERSION = os.environ.get("OPENAI_API_VERSION", "2024-05-01-preview")

# Application-specific settings
WIN_PICK_COUNT = int(os.environ.get("WIN_PICK_COUNT", "5"))
FUZZY_MATCH_SCORE_THRESHOLD = 75

# --- Client Initialization ---
# Initialize clients for various Azure services and OpenAI using the loaded
# configuration. These clients are reused across function invocations.

# Azure Blob Storage client
blob_service_client = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
uploads_container_client = blob_service_client.get_container_client(UPLOADS_CONTAINER_NAME)

# Azure Document Intelligence client
document_intelligence_client = DocumentIntelligenceClient(
    endpoint=DOC_INTELLIGENCE_ENDPOINT,
    credential=AzureKeyCredential(DOC_INTELLIGENCE_KEY),
)

# Azure Table Storage client
table_service_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONNECTION_STRING)

# Azure OpenAI client
openai_client = AzureOpenAI(
    api_key=OPENAI_API_KEY,
    api_version=OPENAI_API_VERSION,
    azure_endpoint=OPENAI_ENDPOINT,
)

# Azure Queue Storage clients
queue_service_client = QueueServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
uploads_queue_client = queue_service_client.get_queue_client(QUEUE_NAME)
dead_letter_queue_client = queue_service_client.get_queue_client(DEAD_LETTER_QUEUE_NAME)

# --- Azure Function App Initialization ---
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
logger = logging.getLogger(__name__)


# =================================================================================
# HELPER FUNCTIONS for Document Processing
# =================================================================================

def analyze_document(
    client: DocumentIntelligenceClient,
    blob_bytes: bytes,
    blob_name: str,
) -> object | None:
    """Analyze a document using Azure Document Intelligence.

    Args:
        client: The Document Intelligence client.
        blob_bytes: The document content as bytes.
        blob_name: The name of the blob being analyzed (for logging).

    Returns:
        The analysis result object if successful, otherwise None.

    """
    try:
        # Start the analysis process
        poller = client.begin_analyze_document(
            model_id="prebuilt-document",
            document=blob_bytes,
            content_type="application/octet-stream",
        )
        # Wait for the analysis to complete
        result = poller.result()
    except Exception:
        logger.exception(f"Error analyzing document '{blob_name}'")
        return None
    else:
        logger.info(f"Document '{blob_name}' analyzed successfully.")
        return result


def download_blob_content(
    container_client: ContainerClient,
    blob_name: str,
) -> bytes | None:
    """Download the content of a blob from Azure Storage.

    Args:
        container_client: The client for the blob container.
        blob_name: The name of the blob to download.

    Returns:
        The blob content as bytes if successful, otherwise None.

    """
    try:
        blob_client = container_client.get_blob_client(blob_name)
        blob_bytes = blob_client.download_blob().readall()
    except Exception:
        logger.exception(f"Error downloading blob '{blob_name}'")
        return None
    else:
        logger.info(f"Downloaded blob '{blob_name}' successfully.")
        return blob_bytes


def prepare_batch_requests(
    result: Any,
    blob_name: str,
) -> list[str]:
    """Prepare a list of JSONL strings for the OpenAI Batch API.

    This function iterates through the key-value pairs extracted by Document
    Intelligence and formats them into individual requests for the OpenAI Batch API.

    Args:
        result: The result from the Document Intelligence analysis.
        blob_name: The name of the original blob, used to create unique custom IDs.

    Returns:
        A list of JSON strings, where each string is a single batch request.

    """
    tools = [
        {
            "type": "function",
            "function": {
                "name": "extract_data",
                "description": "Extracts the key and value from a document field.",
                "parameters": ExtractedData.model_json_schema(),
            },
        },
    ]

    batch_requests: list[str] = []
    if hasattr(result, "key_value_pairs") and result.key_value_pairs:
        for i, item in enumerate(result.key_value_pairs):
            if hasattr(item, "key") and hasattr(item, "value") and item.key and item.value:
                # Create a unique ID for each key-value pair to track it through the process
                custom_id = f"{blob_name}-{i}"
                prompt_message = (
                    "Please extract the key and value from the following text:\n"
                    f"Key: '{item.key.content}'\n"
                    f"Value: '{item.value.content}'"
                )

                # Construct the request body for the OpenAI chat completion
                chat_body = ChatRequestBody(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a data extraction expert. Use the provided tool to extract structured data."
                            ),
                        },
                        {"role": "user", "content": prompt_message},
                    ],
                    tools=tools,
                    tool_choice="auto",
                )

                # Create the full batch request object
                batch_request = BatchRequest(
                    custom_id=custom_id,
                    body=chat_body,
                    method="POST",
                    url="/v1/chat/completions",
                )
                batch_requests.append(batch_request.model_dump_json())

    return batch_requests


def upload_file_to_openai(
    batch_input_filename: str,
    jsonl_content: str,
) -> Any:
    """Upload a JSONL file to OpenAI for batch processing.

    Args:
        batch_input_filename: The name of the file to be created in OpenAI.
        jsonl_content: The content of the JSONL file as a string.

    Returns:
        The OpenAI file object upon successful upload.

    """
    openai_file = openai_client.files.create(
        file=(batch_input_filename, jsonl_content.encode("utf-8")),
        purpose="batch",
    )
    logger.info(f"OpenAI file created with ID: {openai_file.id}")
    return openai_file


def create_openai_batch_job(
    openai_file_id: str,
) -> Any:
    """Create a new batch job in OpenAI.

    Args:
        openai_file_id: The ID of the file previously uploaded to OpenAI.

    Returns:
        The OpenAI batch job object.

    """
    batch_job = openai_client.batches.create(
        input_file_id=openai_file_id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    logger.info(f"Created OpenAI batch job with ID: {batch_job.id}")
    return batch_job


def create_tracking_entity(
    table_client: TableClient,
    base_blob_name: str,
    batch_job_id: str,
    blob_name: str,
) -> None:
    """Create a tracking entity in Azure Table Storage for the batch job.

    This entity stores the job's status and links it back to the original document.

    Args:
        table_client: The client for the Azure Table.
        base_blob_name: The base name of the blob, used as the PartitionKey.
        batch_job_id: The ID of the OpenAI batch job, used as the RowKey.
        blob_name: The full name of the original document blob.

    """
    tracking_entity: dict[str, str] = {
        "PartitionKey": base_blob_name,
        "RowKey": batch_job_id,
        "status": "pending",  # Initial status
        "original_document": blob_name,
    }
    table_client.create_entity(entity=tracking_entity)
    logger.info(f"Created tracking entity for job: {batch_job_id}")


# =================================================================================
# FUNCTION 1: Document Processing (Timer Trigger)
# =================================================================================
@app.timer_trigger(schedule="0 */10 * * * *", arg_name="my_timer", run_on_startup=True)
def doc_processing_func() -> None:
    """Timer-triggered function to process documents from the uploads queue.

    This function runs every 10 minutes, picks messages from the queue, and
    orchestrates the document analysis and OpenAI batch job creation process.
    """
    logger.info("Timer triggered document processing function.")
    # Ensure the job tracker table exists before proceeding
    table_service_client.create_table_if_not_exists(JOB_TRACKER_TABLE_NAME)
    table_client = table_service_client.get_table_client(table_name=JOB_TRACKER_TABLE_NAME)

    # Fetch a batch of messages from the queue
    picked_messages = pick_messages_from_queue(uploads_queue_client, WIN_PICK_COUNT)
    if not picked_messages:
        logger.warning("No messages found in uploads queue.")
        return

    # Process each message individually
    for msg in picked_messages:
        process_queue_message(
            msg=msg,
            uploads_container_client=uploads_container_client,
            uploads_queue_client=uploads_queue_client,
            dead_letter_queue_client=dead_letter_queue_client,
            table_client=table_client,
        )


def pick_messages_from_queue(
    queue_client: QueueClient,
    count: int,
) -> list[QueueMessage]:
    """Pick a specified number of messages from an Azure Queue.

    Args:
        queue_client: The client for the Azure Queue.
        count: The maximum number of messages to retrieve.

    Returns:
        A list of queue messages.

    """
    messages = queue_client.receive_messages(messages_per_page=count)
    return [msg for msg_page in messages.by_page() for msg in msg_page]


def process_queue_message(
    msg: QueueMessage,
    uploads_container_client: ContainerClient,
    uploads_queue_client: QueueClient,
    dead_letter_queue_client: QueueClient,
    table_client: TableClient,
) -> None:
    """Process a single message from the uploads queue.

    This involves downloading the document, analyzing it, creating a batch job,
    and handling success or failure cases.

    Args:
        msg: The queue message to process.
        uploads_container_client: The client for the uploads blob container.
        uploads_queue_client: The client for the main uploads queue.
        dead_letter_queue_client: The client for the dead-letter queue.
        table_client: The client for the job tracker table.

    """
    message_content = msg.content
    logger.info(f"Processing document: {message_content}")
    try:
        # Step 1: Download the document from blob storage
        blob_bytes = download_blob_content(uploads_container_client, message_content)
        if blob_bytes is None:
            logger.error(f"Failed to download blob '{message_content}'. Moving to dead-letter queue.")
            dead_letter_queue_client.send_message(msg.content)
            uploads_queue_client.delete_message(msg)
            return

        # Step 2: Analyze the document with Document Intelligence
        result = analyze_document(document_intelligence_client, blob_bytes, message_content)

        # Step 3: Prepare requests for the OpenAI Batch API
        batch_requests = prepare_batch_requests(result, message_content)
        if not batch_requests:
            logger.warning(f"No key-value pairs found in document '{message_content}'. Nothing to process.")
            uploads_queue_client.delete_message(msg)  # Successfully processed, no further action
            return

        # Step 4: Create and upload the JSONL file for the batch job
        jsonl_content = "\n".join(batch_requests)
        base_blob_name = Path(message_content).name
        batch_input_filename = f"{base_blob_name}.jsonl"
        upload_jsonl_to_blob(batch_input_filename, jsonl_content)

        # Step 5: Create the OpenAI file and batch job
        openai_file = upload_file_to_openai(batch_input_filename, jsonl_content)
        batch_job = create_openai_batch_job(openai_file.id)

        # Step 6: Create a tracking entity in Table Storage
        create_tracking_entity(table_client, base_blob_name, batch_job.id, message_content)

        # Step 7: Delete the original message from the queue
        uploads_queue_client.delete_message(msg)

    except Exception:
        logger.exception(f"An error occurred processing blob '{msg.content}'. Moving to dead-letter queue.")
        dead_letter_queue_client.send_message(msg.content)
        uploads_queue_client.delete_message(msg)


def upload_jsonl_to_blob(
    batch_input_filename: str,
    jsonl_content: str,
) -> None:
    """Upload the generated JSONL file to Azure Blob Storage for archival.

    Args:
        batch_input_filename: The name for the blob.
        jsonl_content: The JSONL content string to upload.

    """
    batch_input_blob_client = blob_service_client.get_blob_client(
        container=BATCH_INPUTS_CONTAINER_NAME,
        blob=batch_input_filename,
    )
    batch_input_blob_client.upload_blob(jsonl_content, overwrite=True)
    logger.info(f"Uploaded batch input file: {batch_input_filename}")


# =================================================================================
# FUNCTION 2: Status Check (Timer Trigger)
# =================================================================================
@app.timer_trigger(schedule="0 0 * * * *", arg_name="my_timer", run_on_startup=True)
@app.queue_output(
    arg_name="output_queue",
    queue_name=os.environ.get("COMPARISON_RESULTS_QUEUE_NAME", "comparison-results"),
    connection="STORAGE_CONNECTION_STRING",
)
def status_check_func(
    output_queue: func.Out[list[str]],
) -> None:
    """Timer-triggered function to check batch job statuses and output results.

    This function runs once per hour, queries for 'pending' jobs in Table Storage,
    checks their status via the OpenAI API, and processes them if they are completed,
    failed, or expired.

    Args:
        output_queue: An output binding to send comparison results to another queue.

    """
    logger.info("Status check function executed.")
    table_client = table_service_client.get_table_client(table_name=JOB_TRACKER_TABLE_NAME)

    # TODO(jules): #123 Replace with a proper mechanism to get original data for comparison  # noqa: FIX002
    existing_data = {"sampel": "amsmdskm"}
    existing_text_to_compare = next(iter(existing_data.values()))

    all_comparison_results: list[str] = []

    # Query Table Storage for all jobs with a 'pending' status
    pending_jobs = table_client.query_entities("status eq 'pending'")
    for job_entity in pending_jobs:
        batch_id = job_entity["RowKey"]
        logger.info("Checking status for batch job: %s", batch_id)
        try:
            # Retrieve the latest status from the OpenAI API
            batch_job = openai_client.batches.retrieve(batch_id)

            if batch_job.status == "completed":
                logger.info("Batch job %s completed. Processing results.", batch_id)
                results = process_completed_job(batch_job, job_entity, existing_text_to_compare, table_client)
                all_comparison_results.extend(results)
            elif batch_job.status in ["failed", "expired", "cancelling", "cancelled"]:
                handle_failed_job(batch_job, job_entity, table_client)
            else:
                # Job is still running, do nothing and check again later
                logger.info(
                    "Batch job %s is still in progress with status: %s",
                    batch_id,
                    batch_job.status,
                )
        except Exception:
            # Handle exceptions during the status check (e.g., network issues)
            logger.exception("An error occurred while checking job %s", batch_id)
            job_entity["status"] = "error"
            job_entity["error_message"] = "Error occurred during status check."
            table_client.update_entity(job_entity)
            continue

    # If there were any completed jobs, send the comparison results to the output queue
    if all_comparison_results:
        output_queue.set(all_comparison_results)
        logger.info("Sent %d comparison results to the queue.", len(all_comparison_results))


def process_completed_job(
    batch_job: Any,
    job_entity: dict,
    existing_text: str,
    table_client: TableClient,
) -> list[str]:
    """Process a completed OpenAI batch job.

    This involves downloading the output file, parsing the results, performing
    fuzzy string matching, and updating the job's status in Table Storage.

    Args:
        batch_job: The completed OpenAI batch job object.
        job_entity: The corresponding entity from Azure Table Storage.
        existing_text: The text to compare against the extracted text.
        table_client: The client for the Azure Table.

    Returns:
        A list of JSON strings representing the comparison results.

    """
    comparison_results: list[str] = []
    output_file_id = batch_job.output_file_id
    if output_file_id is not None:
        # Download the content of the output file from OpenAI
        result_content = openai_client.files.content(output_file_id).read()
        result_lines = result_content.decode("utf-8").strip().split("\n")

        # Process each line in the output file
        for line in result_lines:
            batch_response = BatchResponse.model_validate_json(line)
            if batch_response.response and batch_response.response.body:
                try:
                    tool_calls = batch_response.response.body.choices[0].message.tool_calls
                    if tool_calls:
                        # Extract the data from the tool call
                        tool_call = tool_calls[0]
                        extracted_data = ExtractedData.model_validate_json(tool_call.function.arguments)
                        extracted_text = extracted_data.extracted_value

                        # Perform fuzzy matching
                        score = fuzz.ratio(existing_text.lower(), extracted_text.lower())

                        # Create a result object
                        comparison_result = ComparisonResult(
                            document_field_id=batch_response.custom_id,
                            openai_extracted_text=extracted_text,
                            original_text_for_comparison=existing_text,
                            fuzzy_match_score=score,
                            status=(
                                "Processed - High Score"
                                if score > FUZZY_MATCH_SCORE_THRESHOLD
                                else "Processed - Low Score"
                            ),
                            error_message="",
                        )
                        comparison_results.append(comparison_result.model_dump_json())
                    else:
                        logger.warning("No tool_calls found in the response for %s", batch_response.custom_id)
                except Exception:
                    logger.exception("Error processing tool_calls for %s", batch_response.custom_id)
            else:
                logger.error("Batch job completed but no valid response body found for %s.", batch_response.custom_id)
    else:
        logger.error("Batch job completed but no output_file_id found.")

    # Update the job status to 'completed' in Table Storage
    job_entity["status"] = "completed"
    table_client.update_entity(job_entity)
    logger.info("Updated job %s status to completed.", job_entity["RowKey"])
    return comparison_results


def handle_failed_job(batch_job: Any, job_entity: dict, table_client: TableClient) -> None:
    """Handle a batch job that has failed, been cancelled, or expired.

    This function updates the job's status in Table Storage to 'failed' and
    logs the reason.

    Args:
        batch_job: The failed/cancelled/expired OpenAI batch job object.
        job_entity: The corresponding entity from Azure Table Storage.
        table_client: The client for the Azure Table.

    """
    logger.error(
        "Batch job %s has status: %s. Marking as failed.",
        job_entity["RowKey"],
        batch_job.status,
    )
    job_entity["status"] = "failed"
    job_entity["error_message"] = f"Job failed with status: {batch_job.status}"
    table_client.update_entity(job_entity)
