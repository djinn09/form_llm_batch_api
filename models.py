from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

# --- Pydantic Models for OpenAI Batch API ---

class ChatRequestBody(BaseModel):
    """Defines the structure of the 'body' for a chat completion request."""
    model: str = "gpt-4o"
    messages: List[Dict[str, str]]
    temperature: float = 0.0
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[str] = None


class BatchRequest(BaseModel):
    """A single request object for the OpenAI Batch API."""
    custom_id: str = Field(..., description="A unique identifier for the request to match it with the response.")
    method: str = Field("POST", description="The HTTP method, which is always POST for chat completions.")
    url: str = Field("/v1/chat/completions", description="The API endpoint for the request.")
    body: ChatRequestBody

# --- Pydantic Models for Processing Batch Responses ---

class FunctionCall(BaseModel):
    """Represents the function call with arguments."""
    arguments: str  # This will be a JSON string
    name: str

class ToolCall(BaseModel):
    """Represents a tool call made by the model."""
    id: str
    function: FunctionCall
    type: str

classResponseMessage(BaseModel):
    """The message object within a choice, containing tool calls."""
    tool_calls: List[ToolCall]

class ChatCompletionChoice(BaseModel):
    """Structure of a single choice in a chat completion response."""
    message: ResponseMessage

class ChatCompletionBody(BaseModel):
    """The 'body' of a successful response from the Batch API."""
    id: str
    model: str
    choices: List[ChatCompletionChoice]

class ResponseInfo(BaseModel):
    """The 'response' object within a batch result line."""
    status_code: int
    body: ChatCompletionBody

class BatchResponse(BaseModel):
    """A single result line from the OpenAI Batch API output file."""
    id: str
    custom_id: str
    response: Optional[ResponseInfo] = None
    error: Optional[Dict[str, Any]] = None


# --- Pydantic Models for Application Logic ---

class ExtractedData(BaseModel):
    """The structured data we want OpenAI to extract."""
    extracted_key: str = Field(description="The key extracted from the document text.")
    extracted_value: str = Field(description="The value corresponding to the extracted key.")

class ComparisonResult(BaseModel):
    """The final output message sent to the queue after processing."""
    document_field_id: str = Field(..., description="The custom_id from the original request, linking back to the document field.")
    openai_extracted_text: str = Field(..., description="The processed text extracted by the OpenAI model.")
    original_text_for_comparison: str = Field(..., description="The original text used for the fuzzy comparison.")
    fuzzy_match_score: int = Field(..., description="The similarity score (0-100) from the fuzzy comparison.")
    status: str = Field(..., description="The outcome of the processing, e.g., 'Match Found', 'Mismatch', 'Error'.")
    error_message: Optional[str] = Field(None, description="Any error message encountered during processing.")
