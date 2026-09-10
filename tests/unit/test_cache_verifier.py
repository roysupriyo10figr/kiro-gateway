"""Offline tests for live-verifier evidence handling; no inference is invoked."""

from scripts.verify_claude_cache import evaluate_report, response_metadata, source_boundaries


def test_source_boundary_excludes_later_content() -> None:
    """Only mutations before a source boundary change its fingerprint."""
    def request(prefix: str, suffix: str) -> dict:
        return {"model": "test", "messages": [{"role": "user", "content": [
            {"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": suffix},
        ]}]}
    a = source_boundaries(request("stable", "a"))
    b = source_boundaries(request("stable", "b"))
    c = source_boundaries(request("changed", "a"))
    assert a == b
    assert a != c
    assert a[0]["path"] == "messages/0/content/0"


def test_missing_cache_counters_remain_unknown() -> None:
    """Never turn absent counters into reported zero cache reads or writes."""
    raw = b'data: {"type":"message_start","message":{"id":"m","usage":{"input_tokens":10}}}\n\ndata: {"type":"message_stop"}\n\n'
    assert response_metadata(raw) == {"message_id": "m", "cache_counters": {}, "complete": True}


def test_empty_capture_cannot_pass_verification() -> None:
    """A wrapper that bypasses the relay must fail instead of reporting success."""
    report = {"client_turns": [{"success": True}, {"success": True}], "requests": [],
              "retained_boundary_comparisons": 0, "boundary_violations": [],
              "transcript": {"paths": [], "assistant_message_ids": [], "tool_calls": 0, "tool_ids_match": True}}
    checks = evaluate_report(report, 2)
    assert checks["client_turns_completed"]
    assert not checks["cache_boundaries_stable"]
    assert not checks["all_captured_requests_completed"]
    assert not checks["metering_correlated"]
    assert not checks["every_turn_has_persisted_response"]


def test_response_error_is_not_a_completed_stream() -> None:
    """An error event cannot be accepted as a normal completed response."""
    assert not response_metadata(b'data: {"type":"error","error":{"type":"api_error"}}\n\n')["complete"]


def test_relay_errors_cannot_be_hidden_by_successful_captures() -> None:
    """Transport failures invalidate a run even when later requests succeed."""
    report = {"client_turns": [{"success": True}], "requests": [{"status": 200, "complete": True, "client_turn": 0, "message_id": "m", "metered_credits": 0.1}],
              "retained_boundary_comparisons": 1, "boundary_violations": [], "relay_errors": ["ConnectError"],
              "transcript": {"paths": ["test"], "assistant_message_ids": ["m"], "tool_calls": 1, "tool_ids_match": True}}
    assert not evaluate_report(report, 1)["relay_error_free"]
