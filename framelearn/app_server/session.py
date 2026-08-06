"""AppServerSession — one logical conversation backed by one Codex thread.

Responsibilities:
- initialize handshake
- thread/start (lazy, once per session)
- run_turn: turn/start → consume notifications → return TurnResult
- handle server-initiated requests (approvals)
- turn/interrupt on cancel or timeout
- post-tool-call silence watchdog
- subprocess death detection
- session retirement when unrecoverable
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from framelearn.app_server.jsonrpc_client import JsonRpcStdioClient, RpcError
from framelearn.app_server.projector import EventProjector, ProjectedMessages


# ------------------------------------------------------------------
# Result types
# ------------------------------------------------------------------

@dataclass
class TurnResult:
    thread_id: str = ""
    turn_id: str = ""
    messages: list[dict] = field(default_factory=list)
    final_text: Optional[str] = None
    interrupted: bool = False
    error: Optional[str] = None
    should_retire: bool = False  # True → caller must discard this session
    written_files: list[str] = field(default_factory=list)  # paths written by fileChange events


# ------------------------------------------------------------------
# Approval policy
# ------------------------------------------------------------------

# Callable(method, params) -> "accept" | "acceptForSession" | "decline"
ApprovalCallback = Callable[[str, dict], str]


def _default_approval(method: str, params: dict) -> str:
    """Fail-closed by default — always decline unknown requests."""
    return "decline"


# ------------------------------------------------------------------
# AppServerSession
# ------------------------------------------------------------------

class AppServerSession:
    """Manages one application session ↔ one Codex thread."""

    TURN_TIMEOUT_SECONDS = 600
    POST_TOOL_SILENCE_SECONDS = 90
    NOTIFICATION_POLL_SECONDS = 0.25
    INITIALIZE_TIMEOUT = 15.0
    THREAD_START_TIMEOUT = 15.0
    TURN_START_TIMEOUT = 15.0

    def __init__(
        self,
        workspace: str,
        approval_callback: Optional[ApprovalCallback] = None,
        codex_command: tuple[str, ...] = ("codex", "app-server"),
        codex_env: Optional[dict] = None,
    ):
        self.workspace = workspace
        self._approval_callback = approval_callback or _default_approval
        self._codex_command = codex_command
        self._codex_env = codex_env

        self._client: Optional[JsonRpcStdioClient] = None
        self._thread_id: Optional[str] = None
        self._initialized = False
        self._retired = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def thread_id(self) -> Optional[str]:
        return self._thread_id

    @property
    def is_retired(self) -> bool:
        return self._retired

    def run_turn(
        self,
        text: str = "",
        inputs: Optional[list[dict]] = None,
        ui_callback: Optional[Callable[[dict], None]] = None,
    ) -> TurnResult:
        """
        Send one user message and consume the turn until completion.

        Args:
            text: User message text (deprecated when inputs is provided)
            inputs: Structured turn inputs (list of {type, text/path/url})
            ui_callback: Optional callable for streaming UI events (errors ignored)

        Returns:
            TurnResult with projected messages and metadata
        """
        if self._retired:
            return TurnResult(error="Session is retired — create a new one", should_retire=True)

        try:
            self._ensure_started()
        except Exception as e:
            self._retire()
            return TurnResult(error=f"Failed to start app-server: {e}", should_retire=True)

        assert self._client is not None
        assert self._thread_id is not None

        # Build turn inputs
        if inputs is not None:
            turn_inputs = inputs
        elif text:
            turn_inputs = [{"type": "text", "text": text}]
        else:
            raise ValueError("Either text or inputs must be provided")

        # Start the turn
        try:
            response = self._client.request(
                "turn/start",
                {
                    "threadId": self._thread_id,
                    "input": turn_inputs,
                },
                timeout=self.TURN_START_TIMEOUT,
            )
        except (RpcError, TimeoutError) as e:
            self._retire()
            return TurnResult(
                thread_id=self._thread_id or "",
                error=str(e),
                should_retire=True,
            )

        turn_obj = response.get("turn") or {}
        turn_id = (
            turn_obj.get("id")
            or turn_obj.get("turnId")
            or response.get("turnId")
            or ""
        )

        result = TurnResult(thread_id=self._thread_id, turn_id=turn_id)
        projector = EventProjector()
        deadline = time.monotonic() + self.TURN_TIMEOUT_SECONDS
        last_event_time = time.monotonic()

        while time.monotonic() < deadline:
            # Check subprocess health
            if not self._client.is_alive():
                result.error = f"app-server exited unexpectedly\n{self._client.last_stderr()}"
                result.should_retire = True
                self._retire()
                break

            # Handle pending server-initiated requests (approvals)
            try:
                server_req = self._client.server_requests.get_nowait()
                self._handle_server_request(server_req)
                continue
            except queue.Empty:
                pass

            # Poll for notifications
            try:
                event = self._client.notifications.get(timeout=self.NOTIFICATION_POLL_SECONDS)
            except queue.Empty:
                # Post-tool watchdog
                silence = time.monotonic() - last_event_time
                if silence > self.POST_TOOL_SILENCE_SECONDS:
                    self._interrupt_turn(turn_id)
                    result.interrupted = True
                    result.error = f"Watchdog: no events for {silence:.0f}s"
                    self._retire()
                continue

            last_event_time = time.monotonic()

            # Filter events that don't belong to this turn
            if not self._belongs_to_turn(event, self._thread_id, turn_id):
                continue

            # Stream to UI
            if ui_callback:
                try:
                    ui_callback(event)
                except Exception:
                    pass

            # Capture token usage
            self._capture_token_usage(event, result)

            # Project to messages (only item/completed events produce messages)
            projected: ProjectedMessages = projector.project(event)
            result.messages.extend(projected.messages)
            if projected.final_text is not None:
                result.final_text = projected.final_text

            # Capture file paths written by fileChange events
            if event.get("method") == "item/completed":
                item = (event.get("params") or {}).get("item") or {}
                if item.get("type") == "fileChange":
                    import json as _json
                    print(f"[DEBUG fileChange] {_json.dumps(item, ensure_ascii=False)[:500]}")
                    for ch in (item.get("changes") or []):
                        path = ch.get("path") or ch.get("file") or ""
                        if path:
                            result.written_files.append(path)

            # Check for turn completion
            if event.get("method") == "turn/completed":
                turn_status = (
                    (event.get("params") or {}).get("turn") or {}
                ).get("status", "completed")
                if turn_status == "interrupted":
                    result.interrupted = True
                elif turn_status not in ("completed", ""):
                    result.error = str(
                        ((event.get("params") or {}).get("turn") or {}).get("error", "")
                    )
                break

        else:
            # Deadline exceeded
            self._interrupt_turn(turn_id)
            result.interrupted = True
            result.error = f"Turn exceeded {self.TURN_TIMEOUT_SECONDS}s deadline"
            self._retire()

        return result

    def interrupt(self, turn_id: str):
        """Externally interrupt a running turn (e.g. user pressed Ctrl+C)."""
        if self._client and self._thread_id:
            self._interrupt_turn(turn_id)

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Internal lifecycle
    # ------------------------------------------------------------------

    def _ensure_started(self):
        """Lazily start the app-server and initialize the thread."""
        if self._client is None:
            self._client = JsonRpcStdioClient(
                command=self._codex_command,
                env=self._codex_env,
            )

        if not self._initialized:
            self._do_initialize()

        if self._thread_id is None:
            self._do_thread_start()

    def _do_initialize(self):
        self._client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "framelearn",
                    "title": "FrameLearn",
                    "version": "0.1.0",
                },
                "capabilities": {},
            },
            timeout=self.INITIALIZE_TIMEOUT,
        )
        self._client.notify("initialized", {})
        self._initialized = True

    def _do_thread_start(self):
        result = self._client.request(
            "thread/start",
            {"cwd": self.workspace},
            timeout=self.THREAD_START_TIMEOUT,
        )
        thread_obj = result.get("thread") or {}
        thread_id = (
            thread_obj.get("id")
            or thread_obj.get("sessionId")
            or result.get("sessionId")
            or result.get("threadId")
        )
        if not thread_id:
            raise RuntimeError(f"thread/start returned no thread id: {result}")
        self._thread_id = thread_id

    def _retire(self):
        self._retired = True
        self.close()

    # ------------------------------------------------------------------
    # Turn helpers
    # ------------------------------------------------------------------

    def _interrupt_turn(self, turn_id: str):
        if not self._client or not self._thread_id:
            return
        try:
            self._client.request(
                "turn/interrupt",
                {"threadId": self._thread_id, "turnId": turn_id},
                timeout=5.0,
            )
        except Exception:
            pass  # interrupt is best-effort

    def _handle_server_request(self, server_req: dict):
        req_id = server_req.get("id")
        method = server_req.get("method", "")
        params = server_req.get("params") or {}

        approval_methods = {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
            "item/permissions/requestApproval",
            "mcpServer/elicitation/request",
        }

        if method in approval_methods and self._client:
            decision = self._approval_callback(method, params)
            self._client.respond(req_id, {"decision": decision})
        elif self._client:
            # Unknown server request — must respond with error or Codex waits forever
            self._client.respond_error(req_id, -32601, "Unsupported method")

    @staticmethod
    def _belongs_to_turn(event: dict, thread_id: str, turn_id: str) -> bool:
        params = event.get("params") or {}
        event_thread = params.get("threadId")
        event_turn = params.get("turnId")

        if event_thread and event_thread != thread_id:
            return False
        if event_turn and turn_id and event_turn != turn_id:
            return False
        return True

    @staticmethod
    def _capture_token_usage(event: dict, result: TurnResult):
        if event.get("method") == "thread/tokenUsage/updated":
            usage = (event.get("params") or {}).get("tokenUsage") or {}
            if not hasattr(result, "token_usage"):
                result.token_usage = {}  # type: ignore[attr-defined]
            result.token_usage.update(usage)  # type: ignore[attr-defined]
