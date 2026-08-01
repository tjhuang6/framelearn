"""Codex event projector.

Converts Codex item/* notifications into OpenAI-style chat messages.
Only materializes messages on item/completed — never on delta events.

Output message format (OpenAI-compatible):
    {"role": "assistant", "content": "..."}
    {"role": "assistant", "content": None, "tool_calls": [...]}
    {"role": "tool", "tool_call_id": "...", "content": "..."}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProjectedMessages:
    messages: list[dict] = field(default_factory=list)
    final_text: Optional[str] = None  # set when an agentMessage completes


class EventProjector:
    """Stateful projector for a single turn.

    Create a new instance per turn — it accumulates reasoning state
    internally and clears it when an agentMessage is completed.
    """

    def __init__(self):
        # Pending reasoning for the next agentMessage
        self._pending_reasoning_summary: Optional[str] = None
        self._pending_reasoning_content: Optional[str] = None

    def project(self, event: dict) -> ProjectedMessages:
        """
        Project a single Codex notification into zero or more chat messages.

        Only item/completed events produce persisted messages.
        Delta events return an empty ProjectedMessages (UI-only).
        """
        method = event.get("method", "")
        params = event.get("params") or {}
        result = ProjectedMessages()

        # Only materialize on item/completed
        if method != "item/completed":
            # Accumulate reasoning summary from delta events
            if method == "item/reasoning/summaryDelta":
                delta = params.get("delta", "")
                self._pending_reasoning_summary = (
                    (self._pending_reasoning_summary or "") + delta
                )
            return result

        item = params.get("item") or {}
        item_type = item.get("type", "")

        if item_type == "agentMessage":
            result = self._project_agent_message(item)
        elif item_type == "reasoning":
            self._capture_reasoning(item)
        elif item_type == "commandExecution":
            result = self._project_command_execution(item)
        elif item_type == "fileChange":
            result = self._project_file_change(item)
        elif item_type == "mcpToolCall":
            result = self._project_mcp_tool_call(item)
        elif item_type == "dynamicToolCall":
            result = self._project_dynamic_tool_call(item)
        elif item_type in ("contextCompaction",):
            pass  # bookkeeping only, no messages
        else:
            result = self._project_unknown(item, item_type)

        return result

    # ------------------------------------------------------------------
    # Item type handlers
    # ------------------------------------------------------------------

    def _project_agent_message(self, item: dict) -> ProjectedMessages:
        text = item.get("text") or item.get("content") or ""
        msg: dict = {"role": "assistant", "content": text}

        # Attach pending reasoning (privacy: stored internally, not shown by default)
        if self._pending_reasoning_summary or self._pending_reasoning_content:
            msg["_reasoning"] = {
                "summary": self._pending_reasoning_summary,
                "content": self._pending_reasoning_content,
            }
            self._pending_reasoning_summary = None
            self._pending_reasoning_content = None

        return ProjectedMessages(messages=[msg], final_text=text)

    def _capture_reasoning(self, item: dict):
        """Accumulate reasoning — will be attached to the next agentMessage."""
        summary = item.get("summary") or item.get("text") or ""
        content = item.get("content") or ""
        self._pending_reasoning_summary = summary or self._pending_reasoning_summary
        self._pending_reasoning_content = content or self._pending_reasoning_content

    def _project_command_execution(self, item: dict) -> ProjectedMessages:
        item_id = item.get("id") or ""
        call_id = _stable_call_id("exec", item_id)

        command = item.get("command") or item.get("cmd") or ""
        cwd = item.get("cwd") or ""
        exit_code = item.get("exitCode")
        output = item.get("output") or item.get("stdout") or ""

        if exit_code not in (None, 0):
            output = f"[exit {exit_code}]\n{output}"

        tool_call_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "arguments": json.dumps({"command": command, "cwd": cwd}),
                },
            }],
        }
        tool_result_msg = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": output,
        }
        return ProjectedMessages(messages=[tool_call_msg, tool_result_msg])

    def _project_file_change(self, item: dict) -> ProjectedMessages:
        item_id = item.get("id") or ""
        call_id = _stable_call_id("file", item_id)

        # Only store metadata — not full file contents
        changes = item.get("changes") or []
        summary_parts = []
        for ch in changes[:20]:  # cap at 20 entries
            op = ch.get("op") or ch.get("type") or "update"
            path = ch.get("path") or ch.get("file") or ""
            summary_parts.append(f"{op}: {path}")
        summary = "\n".join(summary_parts) or json.dumps(item)[:200]

        tool_call_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "apply_patch",
                    "arguments": json.dumps({"summary": summary}),
                },
            }],
        }
        tool_result_msg = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": f"Applied:\n{summary}",
        }
        return ProjectedMessages(messages=[tool_call_msg, tool_result_msg])

    def _project_mcp_tool_call(self, item: dict) -> ProjectedMessages:
        item_id = item.get("id") or ""
        server = item.get("server") or item.get("serverName") or "unknown"
        tool = item.get("tool") or item.get("toolName") or "unknown"
        # namespace: mcp.<server>.<tool>
        tool_name = f"mcp.{server}.{tool}"
        call_id = _stable_call_id("mcp", item_id)

        args = item.get("arguments") or item.get("input") or {}
        output = item.get("output") or item.get("result") or ""
        if isinstance(output, dict):
            output = json.dumps(output, ensure_ascii=False)
        # Cap large outputs
        if len(str(output)) > 4000:
            output = str(output)[:4000] + "\n...[truncated]"

        tool_call_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                },
            }],
        }
        tool_result_msg = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": str(output),
        }
        return ProjectedMessages(messages=[tool_call_msg, tool_result_msg])

    def _project_dynamic_tool_call(self, item: dict) -> ProjectedMessages:
        item_id = item.get("id") or ""
        tool_name = item.get("tool") or item.get("name") or "dynamic_tool"
        call_id = _stable_call_id("dyn", item_id)

        args = item.get("arguments") or item.get("input") or {}
        output = item.get("output") or item.get("result") or ""

        tool_call_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                },
            }],
        }
        tool_result_msg = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": str(output)[:4000],
        }
        return ProjectedMessages(messages=[tool_call_msg, tool_result_msg])

    def _project_unknown(self, item: dict, item_type: str) -> ProjectedMessages:
        # Don't fabricate a tool call — record as an assistant note
        snippet = json.dumps(item, ensure_ascii=False)[:300]
        msg = {
            "role": "assistant",
            "content": f"[codex {item_type}] {snippet}",
        }
        return ProjectedMessages(messages=[msg])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _stable_call_id(kind: str, item_id: str) -> str:
    """Generate a stable, deterministic tool call id from the Codex item id.

    Must be stable — the same item must always produce the same id so that
    tool_calls and tool results can be correlated across UI renders and DB reads.
    Never use random UUIDs here.
    """
    if item_id:
        return f"codex_{kind}_{item_id}"
    # Fallback — should rarely happen in practice
    import hashlib
    return f"codex_{kind}_{hashlib.sha1(kind.encode()).hexdigest()[:8]}"
