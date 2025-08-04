from hypothesis import given
from hypothesis import strategies as st

from models import (
    BatchRequest,
    ChatRequestBody,
    ComparisonResult,
    ExtractedData,
)

# --- Hypothesis Strategies for Model Fields ---

# A strategy for generating a list of messages for ChatRequestBody
message_strategy = st.lists(
    st.dictionaries(
        keys=st.sampled_from(["role", "content"]),
        values=st.text(),
        min_size=2,
        max_size=2,
    ),
)

# A strategy for generating a list of tools
tool_strategy = st.lists(
    st.dictionaries(
        keys=st.sampled_from(["type", "function"]),
        values=st.text(),  # Simplified for now
        min_size=2,
        max_size=2,
    ),
)

# --- Hypothesis Strategies for Pydantic Models ---


@st.composite
def chat_request_body_strategy(draw):
    return ChatRequestBody(
        model=draw(st.text()),
        messages=draw(message_strategy),
        temperature=draw(st.floats(min_value=0.0, max_value=2.0)),
        tools=draw(st.one_of(st.none(), tool_strategy)),
        tool_choice=draw(st.one_of(st.none(), st.text())),
    )


@st.composite
def batch_request_strategy(draw):
    return BatchRequest(
        custom_id=draw(st.text()),
        method=draw(st.sampled_from(["POST"])),
        url=draw(st.sampled_from(["/v1/chat/completions"])),
        body=draw(chat_request_body_strategy()),
    )


@st.composite
def extracted_data_strategy(draw):
    return ExtractedData(
        extracted_key=draw(st.text()),
        extracted_value=draw(st.text()),
    )


@st.composite
def comparison_result_strategy(draw):
    return ComparisonResult(
        document_field_id=draw(st.text()),
        openai_extracted_text=draw(st.text()),
        original_text_for_comparison=draw(st.text()),
        fuzzy_match_score=draw(st.integers(min_value=0, max_value=100)),
        status=draw(st.text()),
        error_message=draw(st.one_of(st.none(), st.text())),
    )


# --- Pytest Tests ---


@given(data=chat_request_body_strategy())
def test_chat_request_body_serialization(data) -> None:
    """Test that ChatRequestBody can be serialized and deserialized."""
    json_data = data.model_dump_json()
    new_data = ChatRequestBody.model_validate_json(json_data)
    assert data == new_data


@given(data=batch_request_strategy())
def test_batch_request_serialization(data) -> None:
    """Test that BatchRequest can be serialized and deserialized."""
    json_data = data.model_dump_json()
    new_data = BatchRequest.model_validate_json(json_data)
    assert data == new_data


@given(data=extracted_data_strategy())
def test_extracted_data_serialization(data) -> None:
    """Test that ExtractedData can be serialized and deserialized."""
    json_data = data.model_dump_json()
    new_data = ExtractedData.model_validate_json(json_data)
    assert data == new_data


@given(data=comparison_result_strategy())
def test_comparison_result_serialization(data) -> None:
    """Test that ComparisonResult can be serialized and deserialized."""
    json_data = data.model_dump_json()
    new_data = ComparisonResult.model_validate_json(json_data)
    assert data == new_data
