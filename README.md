# Azure Document Processing Pipeline

This project implements a serverless, event-driven pipeline on Azure to process documents. It uses Azure Functions to orchestrate a workflow between Azure Document Intelligence, Azure OpenAI's Batch API, and Azure Storage services.

## Architecture & Workflow

The pipeline consists of two main Azure Functions that work together asynchronously.

```mermaid
graph TD
    A[Upload Document to <br> 'uploads' Blob Container] --> B{doc_processing_func <br> (Blob Trigger)};
    B --> C[1. Analyze with <br> Document Intelligence];
    C --> D[2. Prepare JSONL <br> for Batch API];
    D --> E[3. Submit Job to <br> OpenAI Batch API];
    E --> F[4. Create Tracking Blob in <br> 'pending-jobs' Container];

    G{status_check_func <br> (Timer Trigger - Hourly)} --> H[1. List Blobs in <br> 'pending-jobs'];
    H --> I{For Each Job...};
    I --> J[2. Check Batch Job Status];
    J -- Completed --> K[3. Retrieve Results];
    J -- Failed/Expired --> L[Move Tracking Blob to 'failed-jobs'];
    J -- In Progress --> M[Do Nothing];

    K --> N[4. Perform Fuzzy <br> String Comparison];
    N --> O[5. Send Result to <br> 'comparison-results' Queue];
    O --> P[6. Move Tracking Blob to 'completed-jobs'];

    subgraph "Input"
        A
    end

    subgraph "Function 1: Document Ingestion"
        B
        C
        D
        E
        F
    end

    subgraph "Function 2: Status Check & Processing"
        G
        H
        I
        J
        K
        L
        M
        N
        O
        P
    end
```

### Step-by-Step Process

1.  **Trigger**: The process begins when a document (e.g., PDF, JPG, PNG) is uploaded to the `uploads` blob storage container.
2.  **Document Processing (`doc_processing_func`)**:
    *   An Azure Function with a blob trigger is invoked.
    *   It sends the document to **Azure AI Document Intelligence** to extract key-value pairs using the `prebuilt-document` model.
    *   The extracted data is transformed into a JSONL (JSON Lines) file, where each line is a separate request formatted for the **OpenAI Batch API**.
    *   The function uploads this JSONL file to OpenAI's file service and creates a new batch job.
    *   To track the job, a small blob named after the `batch_id` is created in the `pending-jobs` container.
3.  **Status Check (`status_check_func`)**:
    *   An Azure Function with a timer trigger runs every hour.
    *   It lists all blobs in the `pending-jobs` container to find active jobs.
    *   For each job, it queries the OpenAI Batch API for the status.
    *   **If Completed**: It retrieves the results, performs a fuzzy string comparison on the extracted text against a sample string, and creates a structured result message using a Pydantic model. This result is then sent to the `comparison-results` Azure Queue. The tracking blob is moved to the `completed-jobs` container.
    *   **If Failed/Expired**: The tracking blob is moved to the `failed-jobs` container for later inspection.

## Project Structure

```
.
├── app.py                  # Main file with both Azure Function definitions.
├── models.py               # Pydantic models for data validation and structure.
├── requirements.txt        # Python dependencies.
├── host.json               # Host configuration for the Function App.
├── local.settings.json     # Local settings and connection strings (DO NOT COMMIT).
└── README.md               # This file.
```

## Setup and Configuration

### Prerequisites
*   Python 3.9+
*   Azure Functions Core Tools
*   An Azure subscription with access to create Storage Accounts, Document Intelligence, and Azure OpenAI resources.

### Development Setup (Recommended)

This project is configured with Ruff and pre-commit for code linting and formatting. It also supports the `uv` package manager for fast environment and dependency management.

1.  **Install `uv`** (optional, but recommended):
    Follow the official instructions to install `uv`: [https://github.com/astral-sh/uv#installation](https://github.com/astral-sh/uv#installation).

2.  **Create a Virtual Environment**:
    Using `uv`:
    ```bash
    uv venv
    ```
    Using standard `venv`:
    ```bash
    python -m venv .venv
    ```
    Activate the environment:
    ```bash
    source .venv/bin/activate
    ```

3.  **Install Dependencies**:
    Using `uv`:
    ```bash
    uv pip install -r requirements.txt
    uv pip install -r requirements-dev.txt
    ```
    Using `pip`:
    ```bash
    pip install -r requirements.txt
    pip install -r requirements-dev.txt
    ```

4.  **Set up Pre-commit Hooks**:
    Install the git hooks to automatically lint and format your code before you commit.
    ```bash
    pre-commit install
    ```

### Environment Variables

Create a `local.settings.json` file (if it doesn't exist) and fill in the placeholder values.

    ```json
    {
      "IsEncrypted": false,
      "Values": {
        "AzureWebJobsStorage": "UseDevelopmentStorage=true", // Or your Azure Storage connection string
        "FUNCTIONS_WORKER_RUNTIME": "python",
        "AzureWebJobsFeatureFlags": "EnableWorkerIndexing",
        "DOCUMENT_INTELLIGENCE_ENDPOINT": "YOUR_DOCUMENT_INTELLIGENCE_ENDPOINT",
        "DOCUMENT_INTELLIGENCE_KEY": "YOUR_DOCUMENT_INTELLIGENCE_KEY",
        "OPENAI_API_KEY": "YOUR_AZURE_OPENAI_API_KEY",
        "OPENAI_ENDPOINT": "YOUR_AZURE_OPENAI_ENDPOINT",
        "STORAGE_CONNECTION_STRING": "YOUR_FULL_AZURE_STORAGE_CONNECTION_STRING",
        "UPLOADS_CONTAINER_NAME": "uploads",
        "BATCH_INPUTS_CONTAINER_NAME": "batch-inputs",
        "PENDING_JOBS_CONTAINER_NAME": "pending-jobs",
        "COMPLETED_JOBS_CONTAINER_NAME": "completed-jobs",
        "FAILED_JOBS_CONTAINER_NAME": "failed-jobs",
        "COMPARISON_RESULTS_QUEUE_NAME": "comparison-results"
      }
    }
    ```

### Azure Resources

Ensure you have the following resources created in your Azure subscription:
*   **Azure Storage Account**: Create the following containers: `uploads`, `batch-inputs`, `pending-jobs`, `completed-jobs`, `failed-jobs`. Also, create the `comparison-results` queue.
*   **Azure AI Document Intelligence** service.
*   **Azure OpenAI** service with a model deployed (e.g., `gpt-4o`).

## How to Run Locally

1.  Start the Azure Functions host:
    ```bash
    func start
    ```
2.  To trigger the pipeline, upload a file to the `uploads` container in your Azure Storage account using a tool like Azure Storage Explorer.
3.  The `status_check_func` will run automatically based on its schedule. You can also trigger it manually in the Functions host for testing.
