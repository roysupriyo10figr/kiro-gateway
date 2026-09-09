"""Translate explicit client cache intent into native Kiro checkpoints."""

import hashlib
import json
import os
from typing import Any, Dict

from loguru import logger

CACHE_ENABLED = os.getenv("KIRO_PROMPT_CACHE", "true").lower() not in {
    "false",
    "0",
    "off",
}
MAX_CHECKPOINTS = 4


def requests_cache(value: Any) -> bool:
    """Inspect client messages, tools, or blocks for an ephemeral cache marker.

    Args:
        value: Client model, mapping, or sequence of content blocks.

    Returns:
        Whether explicit caching is requested. Tool schemas are not traversed.
    """
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return any(requests_cache(item) for item in value)
    if not isinstance(value, dict):
        return False
    marker = value.get("cache_control")
    if isinstance(marker, dict) and marker.get("type") == "ephemeral":
        if marker.get("ttl") not in (None, "5m"):
            logger.debug(
                "Kiro uses its native cache lifetime; client TTL cannot be guaranteed"
            )
        return CACHE_ENABLED
    return requests_cache(value.get("content"))


def finalize_cache_points(payload: Dict[str, Any]) -> None:
    """Bound checkpoints and log fingerprints without exposing prompt content.

    Args:
        payload: Compiled Kiro request, updated in place. Content is unchanged.
    """
    state = payload["conversationState"]
    current = state["currentMessage"]["userInputMessage"]
    tools = current.get("userInputMessageContext", {}).get("tools", [])
    checkpoints = [tool for tool in tools if "cachePoint" in tool]
    messages = [next(iter(entry.values())) for entry in state.get("history", [])]
    messages.append(current)
    checkpoints.extend(message for message in messages if "cachePoint" in message)
    for checkpoint in checkpoints[:-MAX_CHECKPOINTS]:
        checkpoint.pop("cachePoint")
    if tools:
        tools[:] = [tool for tool in tools if tool]
    if checkpoints:
        logger.opt(lazy=True).debug(
            "Native cache checkpoints={} fingerprints={} (not cache-hit counters)",
            lambda: min(len(checkpoints), MAX_CHECKPOINTS),
            lambda: [
                hashlib.sha256(
                    json.dumps(point, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()[:16]
                for point in checkpoints[-MAX_CHECKPOINTS:]
            ],
        )
