"""Translate native reasoning controls for verified Kiro model schemas."""

from typing import Any, Dict, Optional
import re

from loguru import logger


def merge_reasoning_history(
    left: Optional[Dict[str, Any]], right: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Combine compatible reasoning fragments without discarding signatures.

    Args:
        left: Earlier native reasoning content.
        right: Later native reasoning content.

    Returns:
        Native reasoning content preserving both text and signature.

    Raises:
        ValueError: If signed or opaque blocks cannot fit Kiro's single union field.
    """
    if left is None:
        return right
    if right is None:
        return left
    a, b = left.get("reasoningText"), right.get("reasoningText")
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise ValueError(
            "Kiro cannot merge opaque reasoning blocks; keep them in separate assistant turns"
        )
    # Equal signatures do not authorize changing the text they authenticate.
    if a.get("signature") or b.get("signature"):
        raise ValueError(
            "Kiro cannot merge independently signed reasoning blocks; keep them in separate assistant turns"
        )
    return {"reasoningText": {**a, "text": a["text"] + b["text"]}}


def extract_reasoning_history(content: Any) -> Optional[Dict[str, Any]]:
    """Translate Anthropic thinking blocks into the native Kiro history union.

    Args:
        content: Assistant content blocks, as mappings or Pydantic objects.

    Returns:
        Reasoning text and its original signature, or None when absent.

    Raises:
        ValueError: If independently signed blocks cannot be represented faithfully.
    """
    if not isinstance(content, list):
        return None
    result = None
    for block in content:
        data = block.model_dump() if hasattr(block, "model_dump") else block
        if not isinstance(data, dict) or data.get("type") != "thinking":
            continue
        text = data.get("thinking")
        if not isinstance(text, str):
            continue
        reasoning = {"text": text}
        signature = data.get("signature")
        if isinstance(signature, str) and signature and not re.fullmatch(r"sig_[0-9a-f]{32}", signature):
            reasoning["signature"] = signature
        result = merge_reasoning_history(result, {"reasoningText": reasoning})
    return result


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
        raise ValueError(
            "Sol supports output_config.effort, not other Claude output controls"
        )
    if "budget_tokens" in thinking or (fields is None and budget_tokens is not None):
        raise ValueError(
            "Sol uses effort levels, not budget_tokens; use adaptive thinking and output_config.effort"
        )
    if set(thinking) - {"type"} or thinking.get("type") not in (
        None,
        "adaptive",
        "disabled",
    ):
        raise ValueError("For Sol, use thinking.type adaptive or disabled")

    effort = output.get("effort")
    if not enabled or thinking.get("type") == "disabled":
        if effort not in (None, "none"):
            raise ValueError(
                "Sol thinking is disabled but effort is enabled; use adaptive thinking or effort none"
            )
        effort = "none"
    logger.debug("Using Sol native reasoning.effort configuration")
    # An empty native configuration also bypasses Claude-specific prompt injection.
    return {"reasoning": {"effort": effort}} if effort is not None else {}
