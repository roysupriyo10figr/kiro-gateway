"""Native Kiro checkpoint rendering and side-effect-free prefix diagnostics."""

import hashlib
import json
import os
from typing import Any, Dict

from loguru import logger

CACHE_ENABLED = os.getenv("KIRO_PROMPT_CACHE", "true").lower() not in {
    "false", "0", "off",
}
DIAGNOSTICS_ENABLED = os.getenv("KIRO_CACHE_DIAGNOSTICS", "false").lower() in {"true", "1", "on"}


def cache_diagnostic_headers(payload: Dict[str, Any]) -> Dict[str, str]:
    """Expose compiled prefix fingerprints only for explicitly enabled diagnostics.

    Args:
        payload: Actual final upstream payload.

    Returns:
        Optional response headers containing hashes, never prompt text or tokens.
    """
    if not DIAGNOSTICS_ENABLED:
        return {}
    return {"X-Kiro-Cache-Prefixes": ",".join(cache_prefix_fingerprints(payload))}


def cache_translation_enabled() -> bool:
    """Return whether explicit native checkpoint translation is enabled."""
    return CACHE_ENABLED


def kiro_atomic_ranges(content: Any, role: str) -> tuple[tuple[int, int], ...]:
    """Describe Kiro's indivisible content groups without consulting cache flags.

    Args:
        content: Ordered wire content blocks.
        role: Source message role.

    Returns:
        Inclusive ranges whose interior cannot carry a native message checkpoint.
    """
    if not isinstance(content, list) or not content:
        return ()
    blocks = [block.model_dump() if hasattr(block, "model_dump") else block for block in content]
    types = [block.get("type") if isinstance(block, dict) else None for block in blocks]
    if role == "assistant" and any(kind != "text" for kind in types):
        return ((0, len(content) - 1),)
    results = [index for index, kind in enumerate(types) if kind == "tool_result"]
    return ((0, results[-1]),) if results else ()


def make_cache_point() -> Dict[str, str]:
    """Return a fresh checkpoint matching the AWS SDK cache-point schema.

    Returns:
        Native Kiro cache point with its required type field.
    """
    return {"type": "default"}


def requests_cache(value: Any) -> bool:
    """Inspect legacy wire values for explicit cache intent.

    Args:
        value: Message, tool, content block, or list. Tool schemas are not traversed.

    Returns:
        Whether an ephemeral marker is present and translation is enabled.
    """
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return any(requests_cache(item) for item in value)
    if not isinstance(value, dict):
        return False
    marker = value.get("cache_control")
    if isinstance(marker, dict) and marker.get("type") == "ephemeral":
        return CACHE_ENABLED
    return requests_cache(value.get("content"))


def cache_prefix_fingerprints(payload: Dict[str, Any]) -> tuple[str, ...]:
    """Fingerprint complete compiled prefixes through each native checkpoint.

    Args:
        payload: Final upstream payload, after all content transformations.

    Returns:
        Ordered fingerprints of controls, tools, and complete message prefixes.
        These identify compiled content, not confirmed backend cache hits.
    """
    state = payload["conversationState"]
    current = state["currentMessage"]["userInputMessage"]
    digest = hashlib.sha256()
    scope = {
        "model": current.get("modelId"),
        "profile": payload.get("profileArn"),
        "controls": payload.get("additionalModelRequestFields"),
    }
    digest.update(json.dumps(scope, sort_keys=True).encode() + b"\0")
    fingerprints = []
    tools = current.get("userInputMessageContext", {}).get("tools", [])
    for tool in tools:
        if "cachePoint" in tool:
            fingerprints.append(digest.hexdigest()[:16])
        else:
            digest.update(json.dumps(tool, sort_keys=True).encode() + b"\0")
    messages = list(state.get("history", [])) + [{"userInputMessage": current}]
    for entry in messages:
        role, message = next(iter(entry.items()))
        projected = {key: value for key, value in message.items() if key != "cachePoint"}
        context = projected.get("userInputMessageContext")
        if isinstance(context, dict):
            context = {key: value for key, value in context.items() if key != "tools"}
            if context:
                projected["userInputMessageContext"] = context
            else:
                projected.pop("userInputMessageContext", None)
        digest.update(json.dumps({role: projected}, sort_keys=True).encode() + b"\0")
        if "cachePoint" in message:
            fingerprints.append(digest.hexdigest()[:16])
    return tuple(fingerprints)


def finalize_cache_points(payload: Dict[str, Any]) -> None:
    """Log cache-prefix diagnostics without removing or moving client boundaries.

    Native checkpoint limits are left to the upstream contract. Silently dropping
    a stable anchor to keep an arbitrary number of recent markers is not faithful.

    Args:
        payload: Fully compiled Kiro request, inspected without mutation.
    """
    logger.opt(lazy=True).debug(
        "Native cache prefix fingerprints={} (not cache-hit counters)",
        lambda: cache_prefix_fingerprints(payload),
    )
