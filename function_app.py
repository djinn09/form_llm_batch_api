"""Document processing and batch API orchestration for Azure Functions."""

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
from thefuzz import fuzz  # type: ignore

from models import (
    BatchRequest,
    BatchResponse,
    ChatRequestBody,
    ComparisonResult,
    ExtractedData,
)

# --- Configuration ---
STORAGE_CONNECTION_STRING = os.environ["STORAGE_CONNECTION_STRING"]
UPLOADS_CONTAINER_NAME = os.environ.get("UPLOADS_CONTAINER_NAME", "uploads")
BATCH_INPUTS_CONTAINER_NAME = os.environ.get(
    "BATCH_INPUTS_CONTAINER_NAME",
    "batch-inputs",
)
JOB_TRACKER_TABLE_NAME = os.environ.get("JOB_TRACKER_TABLE_NAME", "JobTracker")
DOC_INTELLIGENCE_ENDPOINT = os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"]
DOC_INTELLIGENCE_KEY = os.environ["DOCUMENT_INTELLIGENCE_KEY"]
OPENAI_ENDPOINT = os.environ["OPENAI_ENDPOINT"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_API_VERSION = os.environ.get("OPENAI_API_VERSION", "2024-05-01-preview")
QUEUE_NAME = os.environ.get("UPLOADS_QUEUE_NAME", "uploads-queue")
DEAD_LETTER_QUEUE_NAME = os.environ.get("UPLOADS_DLQ_NAME", "uploads-dead_letter")
WIN_PICK_COUNT = int(os.environ.get("WIN_PICK_COUNT", "5"))
FUZZY_MATCH_SCORE_THRESHOLD = 75  # Magic value replaced with named constant

# --- Client Initialization ---
blob_service_client = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
document_intelligence_client = DocumentIntelligenceClient(
    endpoint=DOC_INTELLIGENCE_ENDPOINT,
    credential=AzureKeyCredential(DOC_INTELLIGENCE_KEY),
)
table_service_client = TableServiceClient.from_connection_string(conn_str=STORAGE_CONNECTION_STRING)
openai_client = AzureOpenAI(
    api_key=OPENAI_API_KEY,
    api_version=OPENAI_API_VERSION,
    azure_endpoint=OPENAI_ENDPOINT,
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
logger = logging.getLogger(__name__)
queue_service_client = QueueServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
uploads_queue_client = queue_service_client.get_queue_client(QUEUE_NAME)
dead_letter_queue_client = queue_service_client.get_queue_client(DEAD_LETTER_QUEUE_NAME)
uploads_container_client = blob_service_client.get_container_client(UPLOADS_CONTAINER_NAME)


def analyze_document(
    client: DocumentIntelligenceClient,
    blob_bytes: bytes,
    blob_name: str,
) -> object | None:
    """Analyze the document using Azure Document Intelligence."""
    try:
        poller = client.begin_analyze_document(
            model_id="prebuilt-document",
            document=blob_bytes,
            content_type="application/octet-stream",
        )  # type: ignore
    except Exception:
        logger.exception(f"Error analyzing document '{blob_name}'")
        return None
    else:
        result = poller.result()
        logger.info(f"Document '{blob_name}' analyzed successfully.")
        return result


def download_blob_content(
    container_client: ContainerClient,
    blob_name: str,
) -> bytes | None:
    """Download the content of a blob as bytes."""
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
    result: Any,  # DocumentAnalysisResult if available
    blob_name: str,
) -> list[str]:
    """Prepare batch requests for the OpenAI Batch API."""
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
                custom_id = f"{blob_name}-{i}"
                prompt_message = (
                    "Please extract the key and value from the following text:\n"
                    f"Key: '{item.key.content}'\n"
                    f"Value: '{item.value.content}'"
                )
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
) -> Any:  # OpenAI file object if available
    """Upload the JSONL file to OpenAI and return the file object."""
    openai_file = openai_client.files.create(
        file=(batch_input_filename, jsonl_content.encode("utf-8")),
        purpose="batch",
    )
    logger.info(f"OpenAI file created with ID: {openai_file.id}")
    return openai_file


def create_openai_batch_job(
    openai_file_id: str,
) -> Any:  # OpenAI batch job object if available
    """Create an OpenAI batch job and return the job object."""
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
    """Create a tracking entity in Azure Table Storage for the batch job."""
    tracking_entity: dict[str, str] = {
        "PartitionKey": base_blob_name,
        "RowKey": batch_job_id,
        "status": "pending",
        "original_document": blob_name,
    }
    table_client.create_entity(entity=tracking_entity)
    logger.info(f"Created tracking entity for job: {batch_job_id}")


# =================================================================================
# FUNCTION 1: Document Processing (Blob Trigger)
# =================================================================================
@app.timer_trigger(schedule="0 */10 * * * *", arg_name="my_timer", run_on_startup=True)
def doc_processing_func() -> None:
    """Timer-triggered function to process documents from the uploads queue."""
    logger.info("Timer triggered document processing function.")
    # Ensure the table exists using the service client, not the table client
    table_service_client.create_table_if_not_exists(JOB_TRACKER_TABLE_NAME)
    table_client = table_service_client.get_table_client(table_name=JOB_TRACKER_TABLE_NAME)

    picked_messages = pick_messages_from_queue(uploads_queue_client, WIN_PICK_COUNT)
    if not picked_messages:
        logger.warning("No messages found in uploads queue.")
        return

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
    """Pick messages from the Azure queue."""
    messages = queue_client.receive_messages(messages_per_page=count)
    return [msg for msg_page in messages.by_page() for msg in msg_page]


def process_queue_message(
    msg: QueueMessage,
    uploads_container_client: ContainerClient,
    uploads_queue_client: QueueClient,
    dead_letter_queue_client: QueueClient,
    table_client: TableClient,
) -> None:
    """Process a single message from the uploads queue."""
    message_content = msg.content
    logger.info(f"Processing document: {message_content}")
    try:
        blob_bytes = download_blob_content(uploads_container_client, message_content.file_name)
        if blob_bytes is None:
            logger.error(f"Failed to download blob '{message_content}'. Skipping processing.")
            dead_letter_queue_client.send_message(msg.content)
            uploads_queue_client.delete_message(msg)
            return
        result = analyze_document(document_intelligence_client, blob_bytes, message_content)
        batch_requests = prepare_batch_requests(result, message_content)
        if not batch_requests:
            logger.warning(f"No key-value pairs found in document '{message_content}'. Nothing to process.")
            uploads_queue_client.delete_message(msg)
            return

        jsonl_content = "\n".join(batch_requests)
        base_blob_name = Path(message_content).name
        batch_input_filename = f"{base_blob_name}.jsonl"
        upload_jsonl_to_blob(batch_input_filename, jsonl_content)
        openai_file = upload_file_to_openai(batch_input_filename, jsonl_content)
        batch_job = create_openai_batch_job(openai_file.id)
        create_tracking_entity(table_client, base_blob_name, batch_job.id, message_content)
        uploads_queue_client.delete_message(msg)
    except Exception:
        logger.exception(f"An error occurred processing blob '{msg.content}'")
        dead_letter_queue_client.send_message(msg.content)
        uploads_queue_client.delete_message(msg)


def upload_jsonl_to_blob(
    batch_input_filename: str,
    jsonl_content: str,
) -> None:
    """Upload the JSONL file to Azure Blob Storage."""
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
def process_completed_job(
    batch_job: Any,  # OpenAI batch job object if available
    job_entity: dict,
    existing_text: str,
    table_client: TableClient,
) -> list[str]:
    """Process a completed batch job and update its status."""
    comparison_results: list[str] = []
    output_file_id = batch_job.output_file_id
    if output_file_id is not None:
        result_content = openai_client.files.content(output_file_id).read()
        result_lines = result_content.decode("utf-8").strip().split("\n")
        for line in result_lines:
            batch_response = BatchResponse.model_validate_json(line)
            if batch_response.response and batch_response.response.body:
                try:
                    tool_calls = batch_response.response.body.choices[0].message.tool_calls
                    if tool_calls:
                        tool_call = tool_calls[0]
                        extracted_data = ExtractedData.model_validate_json(tool_call.function.arguments)
                        extracted_text = extracted_data.extracted_value
                        score = fuzz.ratio(existing_text.lower(), extracted_text.lower())
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
                        logger.warning(
                            "No tool_calls found in the response for %s",
                            batch_response.custom_id,
                        )
                except Exception:
                    logger.exception("Error processing tool_calls for %s", batch_response.custom_id)
            else:
                logger.error("Batch job completed but no valid response body found.")
    else:
        logger.error("Batch job completed but no output_file_id found.")
    job_entity["status"] = "completed"
    table_client.update_entity(job_entity)
    logger.info("Updated job %s status to completed.", job_entity["RowKey"])
    return comparison_results


def handle_failed_job(batch_job: Any, job_entity: dict, table_client: TableClient) -> None:
    """Handle a batch job that failed or was cancelled."""
    logger.error(
        "Batch job %s has status: %s. Marking as failed.",
        job_entity["RowKey"],
        batch_job.status,
    )
    job_entity["status"] = "failed"
    job_entity["error_message"] = f"Job failed with status: {batch_job.status}"
    table_client.update_entity(job_entity)


def status_check_func(
    output_queue: func.Out[list[str]],
) -> None:
    """Timer-triggered function to check batch job status and output results."""
    logger.info("Status check function executed.")
    table_client = table_service_client.get_table_client(table_name=JOB_TRACKER_TABLE_NAME)

    # Sample data for comparison
    existing_data = {"sampel": "amsmdskm"}
    existing_text_to_compare = next(iter(existing_data.values()))

    all_comparison_results: list[str] = []

    # Query for pending jobs
    pending_jobs = table_client.query_entities("status eq 'pending'")
    for job_entity in pending_jobs:
        batch_id = job_entity["RowKey"]
        logger.info("Checking status for batch job: %s", batch_id)
        try:
            batch_job = openai_client.batches.retrieve(batch_id)
            if batch_job.status == "completed":
                logger.info("Batch job %s completed. Processing results.", batch_id)
                results = process_completed_job(batch_job, job_entity, existing_text_to_compare, table_client)
                all_comparison_results.extend(results)
            elif batch_job.status in ["failed", "expired", "cancelling", "cancelled"]:
                handle_failed_job(batch_job, job_entity, table_client)
            else:
                logger.info(
                    "Batch job %s is still in progress with status: %s",
                    batch_id,
                    batch_job.status,
                )
        except Exception:
            logger.exception("An error occurred while checking job %s", batch_id)
            job_entity["status"] = "error"
            job_entity["error_message"] = "Error occurred during status check."
            table_client.update_entity(job_entity)
            continue

    if all_comparison_results:
        output_queue.set(all_comparison_results)
        logger.info("Sent %d comparison results to the queue.", len(all_comparison_results))
