"""Pure, provider-independent planning of content and text cache boundaries.

Adapters project wire content into immutable segments. The Kiro compiler renders
the resulting plan; this module performs no I/O and knows no model identifiers.
"""

import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class CacheDirective:
    """An immutable copy of the client's complete cache directive."""

    serialized: str

    def to_wire(self) -> dict[str, Any]:
        """Return a detached copy of the complete client directive."""
        return json.loads(self.serialized)

    @classmethod
    def parse(cls, value: Any, *, enabled: bool = True) -> Optional["CacheDirective"]:
        """Read an ephemeral directive without discarding its optional fields.

        Args:
            value: Client cache-control value.
            enabled: Whether explicit checkpoint translation is enabled.

        Returns:
            Immutable directive, or None for absent or unsupported marker types.
        """
        if enabled and isinstance(value, dict) and value.get("type") == "ephemeral":
            return cls(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return None


@dataclass(frozen=True)
class ContentSegment:
    """Original wire content ending at a specific client checkpoint, if any."""

    serialized: str
    directive: Optional[CacheDirective] = None

    @property
    def content(self) -> Any:
        """Return a detached copy of this segment's content."""
        return json.loads(self.serialized)

    @property
    def cache_after(self) -> bool:
        """Whether this segment ends at a cache boundary."""
        return self.directive is not None


@dataclass(frozen=True)
class TextSegment:
    """Rendered text and its original boundary metadata."""

    text: str
    directive: Optional[CacheDirective] = None


@dataclass(frozen=True)
class TextPrefixPlan:
    """Stable source units followed by appended context, independent of markers."""

    units: tuple[TextSegment, ...] = ()
    suffix: str = ""

    @property
    def text(self) -> str:
        """Render the original text without moving boundaries or separators."""
        return "".join(part.text for part in self.units) + self.suffix

    def append_suffix(self, text: str) -> "TextPrefixPlan":
        """Append later context without changing any cached prefix.

        Args:
            text: Additional, unmarked context.

        Returns:
            A new plan with unchanged cached units.
        """
        return replace(self, suffix=self.suffix + text)


def _wire_value(value: Any) -> Any:
    """Return JSON-compatible data from a wire mapping or Pydantic block.

    Args:
        value: Source content value.

    Returns:
        JSON-compatible content with explicitly supplied fields retained.
    """
    return value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else value


def partition_content(
    content: Any, *, directive: Optional[CacheDirective] = None, enabled: bool = True,
    atomic_ranges: tuple[tuple[int, int], ...] = (),
) -> tuple[ContentSegment, ...]:
    """Partition source units independently of their changing cache annotations.

    Args:
        content: String or ordered content blocks.
        directive: Whole-message marker, applied only after its final block.
        enabled: Whether block-level checkpoint translation is enabled.
        atomic_ranges: Inclusive block ranges that must remain in one native unit.

    Returns:
        Immutable segments covering every source block exactly once, in order.
    """
    if not isinstance(content, list):
        return (ContentSegment(json.dumps(content), directive if enabled else None),)
    segments = []
    pending = []
    for index, value in enumerate(content):
        block = _wire_value(value)
        pending.append(block)
        boundary = CacheDirective.parse(block.get("cache_control"), enabled=enabled) if isinstance(block, dict) else None
        can_split = all(not start <= index < end for start, end in atomic_ranges)
        if boundary is not None and not can_split:
            raise ValueError("Cache boundary splits an atomic tool or reasoning group; place it after the complete group")
        if can_split:
            segments.append(ContentSegment(json.dumps(pending), boundary))
            pending = []
    if pending or not segments:
        segments.append(ContentSegment(json.dumps(pending), directive if enabled else None))
    elif directive is not None and enabled:
        segments[-1] = replace(segments[-1], directive=directive)
    return tuple(segments)


def plan_text_prefix(parts: Iterable[TextSegment]) -> TextPrefixPlan:
    """Build native text units without moving a marker past subsequent content.

    Args:
        parts: Ordered text projections, including their original separators.

    Returns:
        A plan whose complete rendering equals the input text concatenation.
    """
    units = tuple(part for part in parts if part.text)
    return TextPrefixPlan(units)
