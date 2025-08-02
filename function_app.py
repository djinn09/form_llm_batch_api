import logging
import os

import azure.functions as func
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient
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
    "BATCH_INPUTS_CONTAINER_NAME", "batch-inputs"
)
JOB_TRACKER_TABLE_NAME = os.environ.get("JOB_TRACKER_TABLE_NAME", "JobTracker")
DOC_INTELLIGENCE_ENDPOINT = os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"]
DOC_INTELLIGENCE_KEY = os.environ["DOCUMENT_INTELLIGENCE_KEY"]
OPENAI_ENDPOINT = os.environ["OPENAI_ENDPOINT"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# --- Client Initialization ---
blob_service_client = BlobServiceClient.from_connection_string(
    STORAGE_CONNECTION_STRING
)
document_intelligence_client = DocumentIntelligenceClient(
    endpoint=DOC_INTELLIGENCE_ENDPOINT,
    credential=AzureKeyCredential(DOC_INTELLIGENCE_KEY),
)
table_service_client = TableServiceClient.from_connection_string(
    conn_str=STORAGE_CONNECTION_STRING
)
openai_client = AzureOpenAI(
    api_key=OPENAI_API_KEY,
    api_version="2024-05-01-preview",
    azure_endpoint=OPENAI_ENDPOINT,
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# =================================================================================
# FUNCTION 1: Document Processing (Blob Trigger)
# =================================================================================
@app.blob_trigger(
    arg_name="inputblob",
    path=f"{UPLOADS_CONTAINER_NAME}/{{name}}",
    connection="STORAGE_CONNECTION_STRING",
)
def doc_processing_func(inputblob: func.InputStream) -> None:
    logging.info(f"Processing document: {inputblob.name}")

    try:
        # 1. Analyze document
        poller = document_intelligence_client.begin_analyze_document(
            "prebuilt-document", document=inputblob
        )
        result = poller.result()
        logging.info(f"Document '{inputblob.name}' analyzed successfully.")

        # 2. Prepare JSONL for Batch API using the 'tools' approach
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "extract_data",
                    "description": "Extracts the key and value from a document field.",
                    "parameters": ExtractedData.model_json_schema(),
                },
            }
        ]

        batch_requests = []
        if result.key_value_pairs:
            for i, item in enumerate(result.key_value_pairs):
                if item.key and item.value:
                    custom_id = f"{inputblob.name}-{i}"
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
                                    "You are a data extraction expert. "
                                    "Use the provided tool to extract structured data."
                                ),
                            },
                            {"role": "user", "content": prompt_message},
                        ],
                        tools=tools,
                        tool_choice="auto",
                    )
                    batch_request = BatchRequest(custom_id=custom_id, body=chat_body)
                    batch_requests.append(batch_request.model_dump_json())

        if not batch_requests:
            logging.warning(
                f"No key-value pairs found in document "
                f"'{inputblob.name}'. Nothing to process."
            )
            return

        jsonl_content = "\n".join(batch_requests)

        # 3. Upload JSONL to blob storage
        blob_name = os.path.basename(inputblob.name)
        batch_input_filename = f"{blob_name}.jsonl"
        batch_input_blob_client = blob_service_client.get_blob_client(
            container=BATCH_INPUTS_CONTAINER_NAME, blob=batch_input_filename
        )
        batch_input_blob_client.upload_blob(jsonl_content, overwrite=True)
        logging.info(f"Uploaded batch input file: {batch_input_filename}")

        # 4. Upload file to OpenAI
        openai_file = openai_client.files.create(
            file=(batch_input_filename, jsonl_content.encode("utf-8")), purpose="batch"
        )
        logging.info(f"OpenAI file created with ID: {openai_file.id}")

        # 5. Create OpenAI Batch job
        batch_job = openai_client.batches.create(
            input_file_id=openai_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        logging.info(f"Created OpenAI batch job with ID: {batch_job.id}")

        # 6. Create a tracking entity in Azure Table Storage
        table_client = table_service_client.get_table_client(
            table_name=JOB_TRACKER_TABLE_NAME
        )
        table_client.create_table_if_not_exists()

        tracking_entity: dict[str, str] = {
            "PartitionKey": blob_name,
            "RowKey": batch_job.id,
            "status": "pending",
            "original_document": inputblob.name,
        }
        table_client.create_entity(entity=tracking_entity)
        logging.info(f"Created tracking entity for job: {batch_job.id}")

    except Exception as e:
        logging.error(f"An error occurred processing {inputblob.name}: {e}")
        raise


# =================================================================================
# FUNCTION 2: Status Check (Timer Trigger)
# =================================================================================
@app.timer_trigger(schedule="0 0 * * * *", arg_name="myTimer", run_on_startup=True)
@app.queue_output(
    arg_name="outputQueue",
    queue_name=os.environ.get("COMPARISON_RESULTS_QUEUE_NAME", "comparison-results"),
    connection="STORAGE_CONNECTION_STRING",
)
def status_check_func(
    myTimer: func.TimerRequest, outputQueue: func.Out[list[str]]
) -> None:
    logging.info("Status check function executed.")

    table_client = table_service_client.get_table_client(
        table_name=JOB_TRACKER_TABLE_NAME
    )

    # The user-provided sample data for comparison
    existing_data = {
        "sampel": "amsmdskm"
    }  # TODO: Replace with real data or make configurable
    existing_text_to_compare = next(iter(existing_data.values()))

    all_comparison_results = []

    # Query for pending jobs
    pending_jobs = table_client.query_entities("status eq 'pending'")
    for job_entity in pending_jobs:
        batch_id = job_entity["RowKey"]
        logging.info(f"Checking status for batch job: {batch_id}")

        try:
            batch_job = openai_client.batches.retrieve(batch_id)

            if batch_job.status == "completed":
                logging.info(f"Batch job {batch_id} completed. Processing results.")
                result_content = openai_client.files.content(
                    batch_job.output_file_id
                ).read()
                result_lines = result_content.decode("utf-8").strip().split("\n")

                for line in result_lines:
                    batch_response = BatchResponse.model_validate_json(line)
                    if batch_response.response and batch_response.response.body:
                        try:
                            tool_calls = batch_response.response.body.choices[
                                0
                            ].message.tool_calls
                            if tool_calls:
                                tool_call = tool_calls[0]
                                # The arguments are a JSON string, parse them
                                # into our Pydantic model
                                extracted_data = ExtractedData.model_validate_json(
                                    tool_call.function.arguments
                                )
                                extracted_text = extracted_data.extracted_value

                                score = fuzz.ratio(
                                    existing_text_to_compare.lower(),
                                    extracted_text.lower(),
                                )

                                comparison_result = ComparisonResult(
                                    document_field_id=batch_response.custom_id,
                                    openai_extracted_text=extracted_text,
                                    original_text_for_comparison=existing_text_to_compare,
                                    fuzzy_match_score=score,
                                    status="Processed - High Score"
                                    if score > 75
                                    else "Processed - Low Score",
                                )  # type: ignore
                                all_comparison_results.append(
                                    comparison_result.model_dump_json()
                                )
                            else:
                                logging.warning(
                                    f"No tool_calls found in the response for "
                                    f"{batch_response.custom_id}"
                                )

                        except Exception as e:
                            logging.error(
                                "An unexpected error occurred while "
                                "processing tool_calls for "
                                f"{batch_response.custom_id}: {e}"
                            )

                # Update entity status to 'completed'
                job_entity["status"] = "completed"
                table_client.update_entity(job_entity)
                logging.info(f"Updated job {batch_id} status to completed.")

            elif batch_job.status in ["failed", "expired", "cancelling", "cancelled"]:
                logging.error(
                    f"Batch job {batch_id} has status: {batch_job.status}. "
                    "Marking as failed."
                )
                job_entity["status"] = "failed"
                job_entity["error_message"] = (
                    f"Job failed with status: {batch_job.status}"
                )
                table_client.update_entity(job_entity)

            else:
                logging.info(
                    f"Batch job {batch_id} is still in progress with status: "
                    f"{batch_job.status}"
                )

        except Exception as e:
            logging.error(f"An error occurred while checking job {batch_id}: {e}")
            job_entity["status"] = "error"
            job_entity["error_message"] = str(e)
            table_client.update_entity(job_entity)
            continue

    if all_comparison_results:
        outputQueue.set(all_comparison_results)
        logging.info(
            f"Sent {len(all_comparison_results)} comparison results to the queue."
        )
