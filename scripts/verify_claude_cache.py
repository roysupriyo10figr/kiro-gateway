"""Live, paid verification using kiro-claude, a streaming relay, and its transcript.

Run explicitly with python -m scripts.verify_claude_cache. The gateway must have
KIRO_CACHE_DIAGNOSTICS=true. Reports contain hashes and metadata, never auth values
or prompt/response text. This script is not part of the offline test suite.
"""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx


def source_boundaries(body: dict[str, Any]) -> list[dict[str, str]]:
    """Fingerprint the complete client prefix at each explicit cache boundary.

    Args:
        body: Original Anthropic request, before gateway parsing or normalization.

    Returns:
        Ordered source locations and prefix hashes, with duplicate end markers
        coalesced. Cache-control metadata is not treated as model prompt text.
    """
    digest = hashlib.sha256()
    boundaries: list[dict[str, str]] = []

    def append(value: Any) -> None:
        digest.update(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\0")

    def mark(value: Any, path: str) -> None:
        if isinstance(value, dict) and value.get("type") == "ephemeral":
            fingerprint = digest.hexdigest()
            if not boundaries or boundaries[-1]["source_prefix"] != fingerprint:
                boundaries.append({"path": path, "source_prefix": fingerprint})

    def block(value: dict[str, Any], path: str) -> None:
        append({key: item for key, item in value.items() if key != "cache_control"})
        mark(value.get("cache_control"), path)

    append({key: body.get(key) for key in ("model", "thinking", "output_config")})
    for index, tool in enumerate(body.get("tools", [])):
        block(tool, f"tools/{index}")
    system = body.get("system", [])
    if isinstance(system, str):
        system = [{"type": "text", "text": system}]
    if system:
        append({"role": "system"})
        for index, part in enumerate(system):
            block(part, f"system/{index}")
    for index, message in enumerate(body.get("messages", [])):
        append({"role": message["role"]})
        content = message.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for part_index, part in enumerate(content):
            block(part, f"messages/{index}/content/{part_index}")
        mark(message.get("cache_control"), f"messages/{index}")
    mark(body.get("cache_control"), "request")
    return boundaries


def response_metadata(raw: bytes) -> dict[str, Any]:
    """Extract response identifiers and explicit usage fields from completed SSE.

    Args:
        raw: Bytes forwarded unmodified to Claude Code.

    Returns:
        Non-content response metadata suitable for a diagnostic report.
    """
    result: dict[str, Any] = {"message_id": None, "cache_counters": {}, "complete": False}
    for line in raw.decode("utf-8").splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event.get("type") == "message_start":
            result["message_id"] = event["message"]["id"]
        if event.get("type") == "message_stop":
            result["complete"] = True
        usage = event.get("usage", event.get("message", {}).get("usage", {}))
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if key in usage:
                result["cache_counters"][key] = usage[key]
    return result


def transcript_metadata(session_id: str) -> dict[str, Any]:
    """Inspect only the synthetic session's persisted transcript.

    Args:
        session_id: UUID allocated by this verifier.

    Returns:
        Transcript location, assistant IDs, and tool-call/result correspondence.
    """
    paths = list((Path.home() / ".claude/projects").glob(f"*/{session_id}.jsonl"))
    messages: set[str] = set()
    calls: set[str] = set()
    results: set[str] = set()
    for path in paths:
        for line in path.read_text().splitlines():
            entry = json.loads(line)
            message = entry.get("message", {})
            if message.get("id"):
                messages.add(message["id"])
            content = message.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "tool_use":
                        calls.add(part["id"])
                    elif part.get("type") == "tool_result":
                        results.add(part["tool_use_id"])
    return {"paths": [str(path) for path in paths], "assistant_message_ids": sorted(messages),
            "tool_calls": len(calls), "tool_results": len(results), "tool_ids_match": calls == results}


def run_verification(gateway: str, window: str, turns: int, prefix_lines: int) -> dict[str, Any]:
    """Run a resumed wrapper session through a transparent streaming relay.

    Args:
        gateway: Isolated gateway URL, normally port 9000.
        window: Test server's tmux window identifier for metering correlation.
        turns: Number of user turns, including the initial context submission.
        prefix_lines: Size of the synthetic static reference material.

    Returns:
        A report distinguishing boundary stability, transcript persistence, and
        metering from unknown backend cache operations.
    """
    records: list[dict[str, Any]] = []
    relay_errors: list[str] = []
    active_turn = 0
    with httpx.Client(timeout=10, trust_env=False) as client:
        client.get(gateway + "/health").raise_for_status()

    class Relay(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            """Suppress HTTP logs, which may contain client-specific parameters."""

        def do_POST(self) -> None:
            """Forward the original request and response bytes without buffering delivery."""
            try:
                self.forward_request()
            except (httpx.HTTPError, OSError, ValueError) as exc:
                relay_errors.append(type(exc).__name__)
                self.close_connection = True

        def forward_request(self) -> None:
            """Relay one request, retaining only redacted verification metadata."""
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw)
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in {"host", "content-length", "connection", "transfer-encoding"}}
            record = {"status": None, "request_id": None, "source_boundaries": source_boundaries(body),
                      "compiled_prefixes": [], "diagnostics_present": False, "client_turn": active_turn}
            capture = self.path.startswith("/v1/messages") and "count_tokens" not in self.path
            started = time.monotonic()
            chunks = []
            with httpx.Client(timeout=120, trust_env=False) as client:
                with client.stream("POST", gateway + self.path, headers=headers, content=raw) as response:
                    record.update(status=response.status_code, request_id=response.headers.get("x-kiro-request-id"),
                                  header_seconds=round(time.monotonic() - started, 4),
                                  diagnostics_present="x-kiro-cache-prefixes" in response.headers,
                                  compiled_prefixes=[v for v in response.headers.get("x-kiro-cache-prefixes", "").split(",") if v])
                    self.send_response(response.status_code)
                    self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for chunk in response.iter_bytes():
                        chunks.append(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
            record["total_seconds"] = round(time.monotonic() - started, 4)
            if capture:
                if record["status"] == 200:
                    record.update(response_metadata(b"".join(chunks)))
                records.append(record)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session_id = str(uuid.uuid4())
    settings = json.dumps({"env": {"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}"}})
    client_results = []
    try:
        with tempfile.TemporaryDirectory(prefix="kiro-cache-verification-") as directory:
            for turn in range(turns):
                active_turn = turn
                reference = "\n".join(f"Reference {index}: stable verification data." for index in range(prefix_lines)) if turn == 0 else ""
                prompt = reference + "\nRun Bash: printf CACHE_VERIFY_OK. Then reply READY. Do not read or modify files."
                command = ["kiro-claude", "--bare", "--tools", "Bash", "--allowedTools", "Bash",
                           "--settings", settings, "--output-format", "json"]
                command += ["--session-id", session_id] if turn == 0 else ["--resume", session_id]
                completed = subprocess.run(command + ["-p", prompt], cwd=directory, text=True,
                                           capture_output=True, timeout=150)
                result = {"turn": turn, "exit_code": completed.returncode, "success": False}
                for line in completed.stdout.splitlines():
                    try:
                        decoded = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for event in decoded if isinstance(decoded, list) else [decoded]:
                        if isinstance(event, dict) and event.get("type") == "result":
                            result["success"] = not event.get("is_error", True) and bool(event.get("result"))
                client_results.append(result)
                print(json.dumps({"progress": result}), flush=True)
                if not result["success"]:
                    break
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    log = subprocess.check_output(["tmux", "capture-pane", "-p", "-J", "-t", window, "-S", "-5000"], text=True)
    mappings: dict[str, str] = {}
    violations = []
    retained = 0
    for record in records:
        request_id = record["request_id"]
        amounts = re.findall(r"Kiro metered request_id=" + re.escape(request_id or "unknown") + r" usage=([0-9.eE+-]+) unit=credit", log)
        record["metered_credits"] = sum(map(float, amounts)) if amounts else None
        source = record["source_boundaries"]
        compiled = record["compiled_prefixes"]
        if not record["diagnostics_present"] or len(source) != len(compiled):
            violations.append({"request_id": request_id, "reason": "boundary_count_or_diagnostics", "source": len(source), "compiled": len(compiled)})
            continue
        for boundary, fingerprint in zip(source, compiled):
            identity = boundary["source_prefix"]
            if identity in mappings:
                retained += 1
                if mappings[identity] != fingerprint:
                    violations.append({"request_id": request_id, "reason": "compiled_prefix_changed", "path": boundary["path"]})
            mappings[identity] = fingerprint
    return {"session_id": session_id, "gateway": gateway, "client_turns": client_results,
            "requests": records, "transcript": transcript_metadata(session_id),
            "relay_errors": relay_errors,
            "retained_boundary_comparisons": retained, "boundary_violations": violations,
            "backend_cache_writes_and_reads": "unknown unless explicitly reported; metering is separate evidence"}


def evaluate_report(report: dict[str, Any], expected_turns: int) -> dict[str, bool]:
    """Evaluate evidence without mistaking absent captures or counters for success.

    Args:
        report: Completed diagnostic report.
        expected_turns: Number of requested user turns.

    Returns:
        Independent checks of capture, boundaries, transcript, and metering.
    """
    records = report["requests"]
    transcript = report["transcript"]
    saved_ids = set(transcript["assistant_message_ids"])
    return {
        "relay_error_free": not report.get("relay_errors"),
        "client_turns_completed": len(report["client_turns"]) == expected_turns and all(turn["success"] for turn in report["client_turns"]),
        "all_captured_requests_completed": bool(records) and all(record["status"] == 200 and record.get("complete", False) for record in records),
        "cache_boundaries_stable": bool(records) and report["retained_boundary_comparisons"] > 0 and not report["boundary_violations"],
        "every_turn_has_persisted_response": bool(transcript["paths"]) and all(any(record["client_turn"] == turn and record.get("message_id") in saved_ids for record in records) for turn in range(expected_turns)),
        "tool_round_trip_persisted": transcript["tool_calls"] > 0 and transcript["tool_ids_match"],
        "metering_correlated": bool(records) and all(record["metered_credits"] is not None for record in records),
    }


def main() -> None:
    """Run the explicit live verifier and print its redacted JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://100.116.20.25:9000")
    parser.add_argument("--tmux-window", required=True)
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--prefix-lines", type=int, default=500)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.turns < 2 or args.prefix_lines < 1:
        parser.error("Use at least two turns and a positive synthetic prefix size")
    report = run_verification(args.gateway, args.tmux_window, args.turns, args.prefix_lines)
    report["checks"] = evaluate_report(report, args.turns)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)
    if not all(report["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
