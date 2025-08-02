import azure.functions as func
import logging
import os
import json
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.storage.blob import BlobServiceClient
from models import BatchRequest, ChatRequestBody, BatchResponse, ComparisonResult
from thefuzz import fuzz

# Get configuration from environment variables
STORAGE_CONNECTION_STRING = os.environ["STORAGE_CONNECTION_STRING"]
UPLOADS_CONTAINER_NAME = os.environ.get("UPLOADS_CONTAINER_NAME", "uploads")
BATCH_INPUTS_CONTAINER_NAME = os.environ.get("BATCH_INPUTS_CONTAINER_NAME", "batch-inputs")
PENDING_JOBS_CONTAINER_NAME = os.environ.get("PENDING_JOBS_CONTAINER_NAME", "pending-jobs")
DOC_INTELLIGENCE_ENDPOINT = os.environ["DOCUMENT_INTELLIGENCE_ENDPOINT"]
DOC_INTELLIGENCE_KEY = os.environ["DOCUMENT_INTELLIGENCE_KEY"]
OPENAI_ENDPOINT = os.environ["OPENAI_ENDPOINT"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

# Initialize clients
blob_service_client = BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)
document_intelligence_client = DocumentIntelligenceClient(endpoint=DOC_INTELLIGENCE_ENDPOINT, credential=AzureKeyCredential(DOC_INTELLIGENCE_KEY))
openai_client = AzureOpenAI(
    api_key=OPENAI_API_KEY,
    api_version="2024-05-01-preview",
    azure_endpoint=OPENAI_ENDPOINT
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.blob_trigger(arg_name="inputblob", path=f"{UPLOADS_CONTAINER_NAME}/{{name}}",
                  connection="STORAGE_CONNECTION_STRING")
def doc_processing_func(inputblob: func.InputStream):
    logging.info(f"Processing document: {inputblob.name}")

    try:
        # 1. Analyze document with Document Intelligence
        poller = document_intelligence_client.begin_analyze_document(
            "prebuilt-document", analyze_request=inputblob.read()
        )
        result = poller.result()
        logging.info(f"Document '{inputblob.name}' analyzed successfully.")

        # 2. Prepare JSONL content for OpenAI Batch API
        batch_requests = []
        if result.key_value_pairs:
            for i, item in enumerate(result.key_value_pairs):
                if item.key and item.value:
                    custom_id = f"{inputblob.name}-{i}"
                    prompt_message = f"Extract the key information from the following key-value pair:\nKey: '{item.key.content}'\nValue: '{item.value.content}'"

                    chat_body = ChatRequestBody(
                        messages=[
                            {"role": "system", "content": "You are an expert data extraction assistant."},
                            {"role": "user", "content": prompt_message}
                        ]
                    )

                    batch_request = BatchRequest(
                        custom_id=custom_id,
                        body=chat_body
                    )
                    batch_requests.append(batch_request.model_dump_json())

        if not batch_requests:
            logging.warning(f"No key-value pairs found in document '{inputblob.name}'. Nothing to process.")
            return

        jsonl_content = "\n".join(batch_requests)

        # 3. Upload JSONL file for batch processing
        blob_name = os.path.basename(inputblob.name)
        batch_input_filename = f"{blob_name}.jsonl"
        batch_input_blob_client = blob_service_client.get_blob_client(container=BATCH_INPUTS_CONTAINER_NAME, blob=batch_input_filename)
        batch_input_blob_client.upload_blob(jsonl_content, overwrite=True)
        logging.info(f"Uploaded batch input file: {batch_input_filename}")

        # 4. Upload file to OpenAI using an in-memory stream
        openai_file = openai_client.files.create(
            file=(batch_input_filename, jsonl_content.encode('utf-8')),
            purpose='batch'
        )
        logging.info(f"OpenAI file created with ID: {openai_file.id}")

        # 5. Create OpenAI Batch job
        batch_job = openai_client.batches.create(
            input_file_id=openai_file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h"
        )
        logging.info(f"Created OpenAI batch job with ID: {batch_job.id}")

        # 6. Create a tracking blob
        tracking_blob_name = batch_job.id
        tracking_blob_content = json.dumps({
            "original_document": inputblob.name,
            "batch_job_id": batch_job.id,
            "status": "pending"
        })
        tracking_blob_client = blob_service_client.get_blob_client(container=PENDING_JOBS_CONTAINER_NAME, blob=tracking_blob_name)
        tracking_blob_client.upload_blob(tracking_blob_content, overwrite=True)
        logging.info(f"Created tracking blob for job: {batch_job.id}")

    except Exception as e:
        logging.error(f"An error occurred processing {inputblob.name}: {e}")
        # Optional: Add dead-letter queue logic here
        raise


@app.timer_trigger(schedule="0 0 * * * *", arg_name="myTimer", run_on_startup=True)
@app.queue_output(arg_name="outputQueue", queue_name=os.environ.get("COMPARISON_RESULTS_QUEUE_NAME", "comparison-results"),
                  connection="STORAGE_CONNECTION_STRING")
def status_check_func(myTimer: func.TimerRequest, outputQueue: func.Out[List[str]]):
    logging.info('Status check function executed.')

    pending_jobs_container_client = blob_service_client.get_container_client(PENDING_JOBS_CONTAINER_NAME)
    completed_jobs_container_client = blob_service_client.get_container_client(os.environ.get("COMPLETED_JOBS_CONTAINER_NAME", "completed-jobs"))
    failed_jobs_container_client = blob_service_client.get_container_client(os.environ.get("FAILED_JOBS_CONTAINER_NAME", "failed-jobs"))

    # The user-provided sample data for comparison
    existing_data = {"sampel": "amsmdskm"}
    existing_text_to_compare = next(iter(existing_data.values()))

    all_comparison_results = []

    for blob in pending_jobs_container_client.list_blobs():
        batch_id = blob.name
        logging.info(f"Checking status for batch job: {batch_id}")

        try:
            batch_job = openai_client.batches.retrieve(batch_id)

            if batch_job.status == "completed":
                logging.info(f"Batch job {batch_id} completed. Processing results.")

                # Retrieve results from OpenAI
                result_content = openai_client.files.content(batch_job.output_file_id).read()
                result_lines = result_content.decode('utf-8').strip().split('\n')

                for line in result_lines:
                    batch_response = BatchResponse.model_validate_json(line)

                    if batch_response.response and batch_response.response.body:
                        # Extract the main text from the response
                        extracted_text = batch_response.response.body.choices[0].message.get('content', '')

                        # Perform fuzzy comparison
                        score = fuzz.ratio(existing_text_to_compare.lower(), extracted_text.lower())

                        # Create result object
                        comparison_result = ComparisonResult(
                            document_field_id=batch_response.custom_id,
                            openai_extracted_text=extracted_text,
                            original_text_for_comparison=existing_text_to_compare,
                            fuzzy_match_score=score,
                            status="Processed - High Score" if score > 75 else "Processed - Low Score"
                        )
                        all_comparison_results.append(comparison_result.model_dump_json())

                # Move tracking blob to 'completed' container
                source_blob_client = pending_jobs_container_client.get_blob_client(blob)
                dest_blob_client = completed_jobs_container_client.get_blob_client(blob.name)
                dest_blob_client.start_copy_from_url(source_blob_client.url)
                source_blob_client.delete_blob()
                logging.info(f"Moved tracking blob for {batch_id} to completed container.")

            elif batch_job.status in ["failed", "expired", "cancelling", "cancelled"]:
                logging.error(f"Batch job {batch_id} has status: {batch_job.status}. Moving to failed container.")
                # Move tracking blob to 'failed' container
                source_blob_client = pending_jobs_container_client.get_blob_client(blob)
                dest_blob_client = failed_jobs_container_client.get_blob_client(blob.name)
                dest_blob_client.start_copy_from_url(source_blob_client.url)
                source_blob_client.delete_blob()

            else:
                logging.info(f"Batch job {batch_id} is still in progress with status: {batch_job.status}")

        except Exception as e:
            logging.error(f"An error occurred while checking job {batch_id}: {e}")
            continue # Move to the next job

    if all_comparison_results:
        outputQueue.set(all_comparison_results)
        logging.info(f"Sent {len(all_comparison_results)} comparison results to the queue.")
