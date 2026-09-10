"""Adversarial invariants for the immutable prompt-boundary planner."""

import json
from dataclasses import FrozenInstanceError

import pytest

from kiro.prompt_assembly import CacheDirective, TextSegment, partition_content, plan_text_prefix
from kiro.prompt_cache import cache_prefix_fingerprints
from kiro.prompt_cache import kiro_atomic_ranges
from kiro.models_anthropic import AnthropicMessagesRequest
from kiro.models_openai import ChatCompletionRequest
from kiro.converters_anthropic import anthropic_to_kiro
from kiro.converters_openai import build_kiro_payload

MARKER = {"type": "ephemeral", "ttl": "1h"}


def test_partition_retains_positions_and_source_snapshot() -> None:
    """Mutating later context cannot change an already planned stable segment."""
    blocks = [{"type": "text", "text": "stable", "cache_control": MARKER}, {"type": "text", "text": "volatile"}]
    segments = partition_content(blocks)
    assert [part.cache_after for part in segments] == [True, False]
    assert [block for part in segments for block in part.content] == blocks
    blocks[0]["text"] = "mutated by caller"
    assert segments[0].content[0]["text"] == "stable"
    assert json.loads(segments[0].directive.serialized) == MARKER
    with pytest.raises(FrozenInstanceError):
        segments[0].serialized = "changed"


def test_text_plan_composes_without_moving_boundaries() -> None:
    """Appending context changes only the suffix, never the cached units."""
    marker = CacheDirective.parse(MARKER)
    plan = plan_text_prefix([TextSegment("a"), TextSegment("b", marker), TextSegment("\nc")])
    extended = plan.append_suffix("d")
    assert plan.text == "ab\nc"
    assert extended.text == "ab\ncd"
    assert plan.units == extended.units == (TextSegment("a"), TextSegment("b", marker), TextSegment("\nc"))
    assert plan.suffix == ""


def test_nested_tool_data_is_not_a_boundary() -> None:
    """Only protocol-level markers split content, not marker-shaped tool data."""
    block = {"type": "tool_result", "tool_use_id": "t", "content": [{"type": "text", "text": "data", "cache_control": MARKER}]}
    parts = partition_content([block])
    assert len(parts) == 1 and not parts[0].cache_after


def test_disabled_translation_retains_all_source_content() -> None:
    """Disabling native checkpoints must not change or remove any source block."""
    blocks = [{"type": "text", "text": "stable", "cache_control": MARKER}, {"type": "text", "text": "suffix"}]
    parts = partition_content(blocks, enabled=False)
    assert [block for part in parts for block in part.content] == blocks
    assert all(not part.cache_after for part in parts)


@pytest.mark.parametrize("api", ["anthropic", "openai"])
@pytest.mark.parametrize("location", ["system", "user"])
@pytest.mark.parametrize("stream", [False, True])
def test_suffix_changes_leave_compiled_cache_prefix_identical(api: str, location: str, stream: bool) -> None:
    """Both wire adapters preserve the actual boundary, not just marker presence."""
    payloads = []
    for suffix in ["variable-A", "variable-B"]:
        blocks = [{"type": "text", "text": "stable-prefix", "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": suffix}]
        messages = [{"role": "user", "content": "question" if location == "system" else blocks}]
        params = dict(model="claude-opus-5", max_tokens=64, stream=stream, messages=messages)
        if api == "anthropic":
            if location == "system":
                params["system"] = blocks
            request = AnthropicMessagesRequest(**params, thinking={"type": "adaptive"}, output_config={"effort": "max"})
            payload = anthropic_to_kiro(request, "test", "profile")
        else:
            if location == "system":
                params["messages"] = [{"role": "system", "content": blocks}] + messages
            request = ChatCompletionRequest(**params, reasoning_effort="max")
            payload = build_kiro_payload(request, "test", "profile")
        cached = payload["conversationState"]["history"][0]["userInputMessage"]
        assert "stable-prefix" in cached["content"]
        assert suffix not in cached["content"]
        assert cached["cachePoint"] == {"type": "default"}
        later = [next(iter(entry.values()))["content"] for entry in payload["conversationState"].get("history", [])[1:]]
        later.append(payload["conversationState"]["currentMessage"]["userInputMessage"]["content"])
        assert suffix in "".join(later)
        payloads.append(payload)
    assert cache_prefix_fingerprints(payloads[0]) == cache_prefix_fingerprints(payloads[1])


def without_checkpoints(value: object) -> object:
    """Project model content independently of cache metadata for framing assertions."""
    if isinstance(value, dict):
        return {key: without_checkpoints(item) for key, item in value.items() if key != "cachePoint"}
    if isinstance(value, list):
        return [without_checkpoints(item) for item in value]
    return value


@pytest.mark.parametrize("api", ["anthropic", "openai"])
@pytest.mark.parametrize("location", ["system", "user"])
def test_cache_markers_do_not_control_message_framing(api: str, location: str) -> None:
    """Moving or removing a marker changes cache metadata, not the rendered prompt."""
    payloads = []
    for marked_index in [0, 1, None]:
        blocks = [{"type": "text", "text": "stable"}, {"type": "text", "text": "suffix"}]
        if marked_index is not None:
            blocks[marked_index]["cache_control"] = {"type": "ephemeral"}
        messages = [{"role": "user", "content": "question" if location == "system" else blocks}]
        params = dict(model="claude-opus-5", max_tokens=64, messages=messages)
        if api == "anthropic":
            if location == "system":
                params["system"] = blocks
            request = AnthropicMessagesRequest(**params, thinking={"type": "adaptive"}, output_config={"effort": "max"})
            payload = anthropic_to_kiro(request, "same-conversation", "same-profile")
        else:
            if location == "system":
                params["messages"] = [{"role": "system", "content": blocks}] + messages
            request = ChatCompletionRequest(**params, reasoning_effort="max")
            payload = build_kiro_payload(request, "same-conversation", "same-profile")
        payloads.append(without_checkpoints(payload))
    assert payloads[0] == payloads[1] == payloads[2]


@pytest.mark.parametrize("mode", ["native", "legacy", "disabled"])
def test_current_units_survive_history_promotion(mode: str) -> None:
    """A completed turn keeps its exact compiled units when its markers move later."""
    initial = [{"role": "user", "content": [
        {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "suffix"},
    ]}]
    settings = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}} if mode == "native" else ({"thinking": {"type": "disabled"}} if mode == "disabled" else {})
    first = anthropic_to_kiro(AnthropicMessagesRequest(model="claude-opus-5", max_tokens=64, messages=initial, **settings), "c", "p")
    grown = [{"role": "user", "content": [{"type": "text", "text": "stable"}, {"type": "text", "text": "suffix"}]}, {"role": "assistant", "content": "answer"}, {"role": "user", "content": "next"}]
    second = anthropic_to_kiro(AnthropicMessagesRequest(model="claude-opus-5", max_tokens=64, messages=grown, **settings), "c", "p")
    old_units = first["conversationState"].get("history", []) + [first["conversationState"]["currentMessage"]]
    assert without_checkpoints(old_units) == without_checkpoints(second["conversationState"]["history"][:len(old_units)])


def test_parallel_tool_results_are_an_atomic_prefix() -> None:
    """Do not emit native messages that split a tool batch before all results exist."""
    blocks = [{"type": "tool_result", "tool_use_id": "a", "content": "A"}, {"type": "tool_result", "tool_use_id": "b", "content": "B", "cache_control": MARKER}, {"type": "text", "text": "later"}]
    parts = partition_content(blocks, atomic_ranges=kiro_atomic_ranges(blocks, "user"))
    assert len(parts) == 2 and len(parts[0].content) == 2
    assert parts[0].cache_after and not parts[1].cache_after
    blocks[0]["cache_control"] = MARKER
    with pytest.raises(ValueError, match="atomic"):
        partition_content(blocks, atomic_ranges=kiro_atomic_ranges(blocks, "user"))
