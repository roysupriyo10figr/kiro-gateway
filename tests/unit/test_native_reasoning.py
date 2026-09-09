"""Verify model-specific native reasoning schema translation."""

from copy import deepcopy
from typing import Any

import pytest

from kiro.native_reasoning import native_reasoning_fields


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
def test_sol_effort_mapping_preserves_input(effort: str) -> None:
    """All verified effort values survive translation without input mutation."""
    fields = {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
    original = deepcopy(fields)
    assert native_reasoning_fields("gpt-5.6-sol", fields, True, None) == {"reasoning": {"effort": effort}}
    assert fields == original


def test_sol_defaults_and_disable() -> None:
    """Defaults bypass fake instructions; disabling reasoning is explicit."""
    assert native_reasoning_fields("gpt-5.6-sol", None, True, None) == {}
    assert native_reasoning_fields("gpt-5.6-sol", None, False, None) == {"reasoning": {"effort": "none"}}


@pytest.mark.parametrize("fields", [
    {"thinking": {"type": "enabled", "budget_tokens": 4000}},
    {"thinking": {"type": "disabled"}, "output_config": {"effort": "max"}},
    {"thinking": "adaptive"},
    {"output_config": "max"},
    {"output_config": {"format": {}}},
    {"thinking": {"type": "unknown"}},
    {"unknown": True},
])
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
