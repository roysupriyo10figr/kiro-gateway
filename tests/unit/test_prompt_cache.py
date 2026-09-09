"""Cache intent translation without live network calls or real credentials."""

from copy import deepcopy
from typing import Any

import pytest

from kiro.prompt_cache import requests_cache, finalize_cache_points
from kiro.models_anthropic import AnthropicMessagesRequest
from kiro.models_openai import ChatCompletionRequest
from kiro.converters_anthropic import anthropic_to_kiro
from kiro.converters_openai import build_kiro_payload
from kiro.config import FALLBACK_MODELS

MARKER = {"type": "ephemeral"}
# Advertised CLI models supplement the runtime fallback list; future IDs exercise pass-through.
CACHE_MODELS = sorted(
    {entry["modelId"] for entry in FALLBACK_MODELS}
    | {
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-opus-4.8",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "future-model-id",
    }
)


@pytest.mark.parametrize("api", ["anthropic", "openai"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model", CACHE_MODELS)
def test_message_system_and_tool_cache_points(
    api: str, stream: bool, model: str
) -> None:
    """Both APIs preserve client markers through validation and conversion."""
    messages = [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Second question", "cache_control": MARKER}
            ],
        },
    ]
    base = dict(model=model, max_tokens=64, messages=messages, stream=stream)
    if api == "anthropic":
        request = AnthropicMessagesRequest(
            **base,
            system=[{"type": "text", "text": "Instructions", "cache_control": MARKER}],
            tools=[
                {
                    "name": "lookup",
                    "input_schema": {"type": "object"},
                    "cache_control": MARKER,
                }
            ],
        )
        convert = anthropic_to_kiro
    else:
        base["messages"] = [
            {"role": "system", "content": "Instructions", "cache_control": MARKER}
        ] + messages
        request = ChatCompletionRequest(
            **base,
            tools=[
                {
                    "type": "function",
                    "function": {"name": "lookup", "parameters": {"type": "object"}},
                    "cache_control": MARKER,
                }
            ],
        )
        convert = build_kiro_payload
    original = request.model_dump()
    payload = convert(request, "test", "profile")
    state = payload["conversationState"]
    assert state["history"][0]["userInputMessage"]["cachePoint"] == {}
    assert "cachePoint" not in state["history"][1]["assistantResponseMessage"]
    current = state["currentMessage"]["userInputMessage"]
    assert current["cachePoint"] == {}
    assert current["userInputMessageContext"]["tools"][-1] == {"cachePoint": {}}
    assert "Instructions" in state["history"][0]["userInputMessage"]["content"]
    assert "Second question" in current["content"]
    assert request.model_dump() == original


@pytest.mark.parametrize(
    "value",
    [
        None,
        "cache_control",
        2,
        {"cache_control": "ephemeral"},
        {"cache_control": {"type": "unknown"}},
        {"input_schema": {"cache_control": MARKER}},
    ],
)
def test_no_false_positive_cache_markers(value: Any) -> None:
    """Malformed metadata and schema properties must not activate caching."""
    assert requests_cache(value) is False


def test_disable_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The escape hatch disables generated native cache points."""
    monkeypatch.setattr("kiro.prompt_cache.CACHE_ENABLED", False)
    assert not requests_cache(
        {"content": [{"type": "text", "text": "Hi", "cache_control": MARKER}]}
    )


def test_limits_checkpoints_without_removing_content() -> None:
    """Keep recent checkpoints without trimming user text or tools."""
    history = [
        {"userInputMessage": {"content": str(i), "cachePoint": {}}} for i in range(6)
    ]
    payload = {
        "conversationState": {
            "history": history,
            "currentMessage": {
                "userInputMessage": {
                    "content": "last",
                    "cachePoint": {},
                    "userInputMessageContext": {
                        "tools": [
                            {"toolSpecification": {"name": "x"}},
                            {"cachePoint": {}},
                        ]
                    },
                }
            },
        }
    }
    original = deepcopy(payload)
    finalize_cache_points(payload)
    assert sum("cachePoint" in entry["userInputMessage"] for entry in history) == 3
    assert [entry["userInputMessage"]["content"] for entry in history] == [
        entry["userInputMessage"]["content"]
        for entry in original["conversationState"]["history"]
    ]
    assert payload["conversationState"]["currentMessage"]["userInputMessage"][
        "userInputMessageContext"
    ]["tools"] == [{"toolSpecification": {"name": "x"}}]


def test_merge_and_orphan_repair_keep_cache_intent() -> None:
    """Required Kiro conversation normalization preserves cache hints."""
    request = AnthropicMessagesRequest(
        model="claude-opus-5",
        max_tokens=32,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "missing",
                        "content": "result",
                        "cache_control": MARKER,
                    }
                ],
            },
            {"role": "user", "content": "continue"},
        ],
    )
    payload = anthropic_to_kiro(request, "test", "profile")
    assert (
        payload["conversationState"]["currentMessage"]["userInputMessage"]["cachePoint"]
        == {}
    )
