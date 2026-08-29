from __future__ import annotations


def persistent_worker_event(event: object) -> dict[str, object]:
    """Reduce a validated Codex event to the shared durable audit shape."""
    if not isinstance(event, dict):
        raise ValueError("local_agent_worker_event_invalid")
    event_type = event.get("type")
    allowed_types = {
        "worker.heartbeat", "thread.started", "turn.started", "turn.completed", "error",
        "item.started", "item.updated", "item.completed",
    }
    sequence_no = event.get("sequence_no")
    if event_type not in allowed_types or not isinstance(sequence_no, int) or isinstance(sequence_no, bool) or sequence_no <= 0:
        raise ValueError("local_agent_worker_event_invalid")
    result: dict[str, object] = {"type": event_type, "sequence_no": sequence_no}
    if str(event_type).startswith("item."):
        item_type = event.get("item_type")
        if item_type not in {"agent_message", "reasoning", "command_execution", "file_change", "mcp_tool_call", "web_search", "todo_list", "error"}:
            raise ValueError("local_agent_worker_event_invalid")
        result["item_type"] = item_type
    if event_type != "worker.heartbeat":
        digest = event.get("raw_line_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("local_agent_worker_event_invalid")
        result["raw_line_digest"] = f"sha256:{digest}"
    return result
