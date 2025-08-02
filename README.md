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
    E --> F[(4. Create Record in <br> JobTracker Table)];

    G{status_check_func <br> (Timer Trigger - Hourly)} --> H[1. Query JobTracker Table <br> for 'pending' jobs];
    H --> I{For Each Job...};
    I --> J[2. Check Batch Job Status];
    J -- Completed --> K[3. Retrieve Results];
    J -- Failed/Expired --> L[Update Job Status to 'failed'];
    J -- In Progress --> M[Do Nothing];

    K --> N[4. Perform Fuzzy <br> String Comparison];
    N --> O[5. Send Result to <br> 'comparison-results' Queue];
    O --> P[6. Update Job Status to 'completed'];

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
    *   It sends the document to **Azure AI Document Intelligence** to extract key-value pairs.
    *   The extracted data is transformed into a JSONL file formatted for the **OpenAI Batch API**. Each request is configured to ask for a structured JSON response for more reliable data extraction.
    *   The function uploads this JSONL file and creates a new batch job with OpenAI.
    *   To track the job, it creates a new entity in an **Azure Table** (`JobTracker`) with the batch ID and a 'pending' status.
3.  **Status Check (`status_check_func`)**:
    *   An Azure Function with a timer trigger runs every hour.
    *   It queries the `JobTracker` table for all jobs with a 'pending' status.
    *   For each job, it queries the OpenAI Batch API.
    *   **If Completed**: It retrieves the results, performs a fuzzy string comparison, and sends a structured result message to the `comparison-results` Azure Queue. It then updates the job's status in the table to 'completed'.
    *   **If Failed/Expired**: It updates the job's status in the table to 'failed'.

## Project Structure

```
.
├── function_app.py         # Main file with both Azure Function definitions.
├── models.py               # Pydantic models for data validation and structure.
├── pyproject.toml          # Project metadata, dependencies, and tool configuration.
├── .pre-commit-config.yaml # Configuration for pre-commit hooks.
├── setup.sh                # Setup script for Linux/macOS.
├── setup.ps1               # Setup script for Windows.
├── host.json               # Host configuration for the Function App.
├── local.settings.json     # Local settings and connection strings (DO NOT COMMIT).
└── README.md               # This file.
```

## Setup and Configuration

### Development Setup

This project includes setup scripts to automate the creation of a virtual environment and installation of dependencies.

1.  **Prerequisites**:
    *   Python 3.9+
    *   (Optional but recommended) `uv` for faster package management.
    *   Azure Functions Core Tools.

2.  **Run the Setup Script**:

    **For Linux/macOS:**
    ```bash
    # Make the script executable
    chmod +x setup.sh
    # Run the script
    ./setup.sh
    ```

    **For Windows (using PowerShell):**
    You may need to adjust your execution policy to run the script.
    ```powershell
    # To allow the script to run in the current session
    Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
    # Run the script
    .\setup.ps1
    ```
    The script will create a virtual environment, install all necessary dependencies, and set up pre-commit hooks.

3.  **Activate the Virtual Environment**:
    After the setup script completes, activate the virtual environment for your shell session.

    **For Linux/macOS:**
    ```bash
    source .venv/bin/activate
    ```

    **For Windows (PowerShell):**
    ```powershell
    .\.venv\Scripts\Activate.ps1
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
        "JOB_TRACKER_TABLE_NAME": "JobTracker",
        "COMPARISON_RESULTS_QUEUE_NAME": "comparison-results"
      }
    }
    ```

### Azure Resources

Ensure you have the following resources created in your Azure subscription:
*   **Azure Storage Account**:
    *   **Blob Containers**: `uploads`, `batch-inputs`.
    *   **Queue**: `comparison-results`.
    *   **Table**: `JobTracker` (or the name you specify in settings).
*   **Azure AI Document Intelligence** service.
*   **Azure OpenAI** service with a model deployed (e.g., `gpt-4o`).

## How to Run Locally

1.  Start the Azure Functions host:
    ```bash
    func start
    ```
2.  To trigger the pipeline, upload a file to the `uploads` container in your Azure Storage account using a tool like Azure Storage Explorer.
3.  The `status_check_func` will run automatically based on its schedule. You can also trigger it manually in the Functions host for testing.
