"""Verify model-specific native reasoning schema translation."""

from copy import deepcopy
from typing import Any

import pytest

from kiro.native_reasoning import native_reasoning_fields
from kiro.native_reasoning import extract_reasoning_history, merge_reasoning_history


def test_signed_reasoning_round_trip_shape() -> None:
    """Preserve the SDK's reasoningText union and the exact native signature."""
    result = extract_reasoning_history([
        {"type": "thinking", "thinking": "reason", "signature": "signed-native-data"},
        {"type": "text", "text": "answer"},
    ])
    assert result == {"reasoningText": {"text": "reason", "signature": "signed-native-data"}}


def test_fake_signature_not_sent_as_native() -> None:
    """Keep legacy reasoning text without forwarding a fabricated signature."""
    assert extract_reasoning_history([{"type": "thinking", "thinking": "reason", "signature": "sig_" + "a" * 32}]) == {"reasoningText": {"text": "reason"}}


def test_distinct_signatures_are_not_corrupted() -> None:
    """Reject unrepresentable signed merges instead of dropping original content."""
    with pytest.raises(ValueError, match="independently signed"):
        merge_reasoning_history({"reasoningText": {"text": "a", "signature": "one"}}, {"reasoningText": {"text": "b", "signature": "two"}})


@pytest.mark.parametrize("signatures", [("same", "same"), ("signed", None), (None, "signed")])
def test_signed_text_is_never_concatenated(signatures: tuple) -> None:
    """Matching signatures and unsigned neighbors do not permit signed-text edits."""
    left = {"reasoningText": {"text": "first", "signature": signatures[0]}}
    right = {"reasoningText": {"text": "second", "signature": signatures[1]}}
    original = deepcopy((left, right))
    with pytest.raises(ValueError, match="independently signed"):
        merge_reasoning_history(left, right)
    assert (left, right) == original


def test_unsigned_reasoning_can_merge_without_mutating_inputs() -> None:
    """Only unsigned text fragments can be composed into a single native field."""
    left = {"reasoningText": {"text": "first"}}
    right = {"reasoningText": {"text": "second"}}
    original = deepcopy((left, right))
    assert merge_reasoning_history(left, right) == {
        "reasoningText": {"text": "firstsecond"}
    }
    assert (left, right) == original


@pytest.mark.parametrize("reverse", [False, True])
def test_opaque_reasoning_merge_is_actionable(reverse: bool) -> None:
    """An opaque union cannot be treated as text or fail with an internal KeyError."""
    blocks = [{"redactedContent": "b3BhcXVl"}, {"reasoningText": {"text": "text"}}]
    if reverse:
        blocks.reverse()
    with pytest.raises(ValueError, match="opaque reasoning"):
        merge_reasoning_history(*blocks)


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_sol_effort_mapping_preserves_input(effort: str) -> None:
    """All verified effort values survive translation without input mutation."""
    fields = {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
    original = deepcopy(fields)
    assert native_reasoning_fields("gpt-5.6-sol", fields, True, None) == {
        "reasoning": {"effort": effort}
    }
    assert fields == original


def test_sol_defaults_and_disable() -> None:
    """Defaults bypass fake instructions; disabling reasoning is explicit."""
    assert native_reasoning_fields("gpt-5.6-sol", None, True, None) == {}
    assert native_reasoning_fields("gpt-5.6-sol", None, False, None) == {
        "reasoning": {"effort": "none"}
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"thinking": {"type": "enabled", "budget_tokens": 4000}},
        {"thinking": {"type": "disabled"}, "output_config": {"effort": "max"}},
        {"thinking": "adaptive"},
        {"output_config": "max"},
        {"output_config": {"format": {}}},
        {"thinking": {"type": "unknown"}},
        {"unknown": True},
    ],
)
def test_sol_rejects_unrepresentable_controls(fields: Any) -> None:
    """Unsupported fields and contradictory controls cannot be silently dropped."""
    with pytest.raises(ValueError):
        native_reasoning_fields("gpt-5.6-sol", fields, True, None)


def test_fixed_budget_rejected() -> None:
    """A legacy budget cannot be translated into a Sol effort value."""
    with pytest.raises(ValueError, match="budget_tokens"):
        native_reasoning_fields("gpt-5.6-sol", None, True, 4000)


@pytest.mark.parametrize("model", ["claude-opus-5", "unknown", "gpt-5.6-terra"])
def test_unverified_models_unchanged(model: str) -> None:
    """Do not assume other models share Sol's verified schema."""
    fields = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}}
    assert native_reasoning_fields(model, fields, True, None) is fields
