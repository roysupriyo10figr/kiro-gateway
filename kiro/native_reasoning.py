"""Translate native reasoning controls for verified Kiro model schemas."""

from typing import Any, Dict, Optional

from loguru import logger


def native_reasoning_fields(
    model_id: str,
    fields: Optional[Dict[str, Any]],
    enabled: bool,
    budget_tokens: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Adapt client reasoning settings without changing other model families.

    Args:
        model_id: Resolved upstream model identifier.
        fields: Native controls produced by the API adapter.
        enabled: Whether the client permits reasoning.
        budget_tokens: Legacy fixed thinking budget, if supplied.

    Returns:
        Native model fields, or None to retain legacy prompt-based reasoning.

    Raises:
        ValueError: If Sol cannot represent the requested thinking controls.
    """
    if model_id != "gpt-5.6-sol":
        return fields

    controls = fields or {}
    thinking = controls.get("thinking", {})
    output = controls.get("output_config", {})
    if not isinstance(thinking, dict) or not isinstance(output, dict):
        raise ValueError("Sol thinking and output_config must be objects")
    if set(controls) - {"thinking", "output_config"} or set(output) - {"effort"}:
        raise ValueError("Sol supports output_config.effort, not other Claude output controls")
    if "budget_tokens" in thinking or (fields is None and budget_tokens is not None):
        raise ValueError("Sol uses effort levels, not budget_tokens; use adaptive thinking and output_config.effort")
    if set(thinking) - {"type"} or thinking.get("type") not in (None, "adaptive", "disabled"):
        raise ValueError("For Sol, use thinking.type adaptive or disabled")

    effort = output.get("effort")
    if not enabled or thinking.get("type") == "disabled":
        if effort not in (None, "none"):
            raise ValueError("Sol thinking is disabled but effort is enabled; use adaptive thinking or effort none")
        effort = "none"
    logger.debug("Using Sol native reasoning.effort configuration")
    # An empty native configuration also bypasses Claude-specific prompt injection.
    return {"reasoning": {"effort": effort}} if effort is not None else {}
