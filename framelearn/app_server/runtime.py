"""RuntimeAdapter — connects AppServerSession to FrameLearn's main app.

Responsibilities:
- Accept user text input
- Write user message to DB before calling Codex
- Call session.run_turn()
- Write projected assistant/tool messages to DB after turn
- Handle session retirement and recreation
- Return a consistent result structure to the caller (router)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from framelearn.app_server.persistence import SessionDB
from framelearn.app_server.session import AppServerSession, TurnResult


@dataclass
class RunResult:
    """Returned to the caller (CommandRouter) after a turn completes."""
    session_id: str
    thread_id: str
    turn_id: str
    final_text: Optional[str]
    messages: list[dict] = field(default_factory=list)
    interrupted: bool = False
    error: Optional[str] = None
    persisted: bool = False  # upper layers must NOT write again when True


class RuntimeAdapter:
    """Orchestrates one FrameLearn session backed by a Codex app-server thread.

    Create one RuntimeAdapter per interactive session (e.g. one CLI invocation).
    The underlying AppServerSession and Codex thread are reused across multiple
    run_turn() calls until retirement.
    """

    def __init__(
        self,
        workspace: str,
        session_id: Optional[str] = None,
        db: Optional[SessionDB] = None,
        approval_callback: Optional[Callable[[str, dict], str]] = None,
        ui_callback: Optional[Callable[[dict], None]] = None,
        codex_command: tuple[str, ...] = ("codex", "app-server"),
    ):
        self.workspace = workspace
        self.session_id = session_id or str(uuid.uuid4())
        self._db = db or SessionDB()
        self._approval_callback = approval_callback
        self._ui_callback = ui_callback
        self._codex_command = codex_command

        self._session: Optional[AppServerSession] = None

        # Ensure session row exists in DB
        self._db.create_session(self.session_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_turn(self, user_text: str, ui_callback: Optional[Callable[[dict], None]] = None) -> RunResult:
        """
        Send a user message and return the completed turn result.

        The user message is written to the DB before the Codex call.
        Assistant/tool messages are written after the turn completes.
        The returned RunResult.persisted=True signals that DB writes are done.
        """
        # 1. Write user message to DB immediately
        self._db.append_message(
            session_id=self.session_id,
            role="user",
            content=user_text,
            codex_thread_id=self._session.thread_id if self._session else None,
        )

        # 2. Ensure a live session exists
        self._ensure_session()

        # 3. Execute turn
        turn_result: TurnResult = self._session.run_turn(  # type: ignore[union-attr]
            text=user_text,
            ui_callback=self._ui_callback,
        )

        # 4. If session retired mid-turn, record the error and try once more
        if turn_result.should_retire:
            self._session = None
            try:
                self._ensure_session()
                turn_result = self._session.run_turn(  # type: ignore[union-attr]
                    text=user_text,
                    ui_callback=self._ui_callback,
                )
            except Exception as e:
                return RunResult(
                    session_id=self.session_id,
                    thread_id="",
                    turn_id="",
                    final_text=None,
                    error=f"Session restart failed: {e}",
                )

        # 5. Update session thread_id in DB (in case it just started)
        if turn_result.thread_id:
            self._db.update_session_thread(self.session_id, turn_result.thread_id)

        # 6. Write projected assistant/tool messages to DB
        self._persist_turn_messages(turn_result)

        return RunResult(
            session_id=self.session_id,
            thread_id=turn_result.thread_id,
            turn_id=turn_result.turn_id,
            final_text=turn_result.final_text,
            messages=turn_result.messages,
            interrupted=turn_result.interrupted,
            error=turn_result.error,
            persisted=True,
        )

    def close(self):
        if self._session:
            self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_session(self):
        if self._session is None or self._session.is_retired:
            self._session = AppServerSession(
                workspace=self.workspace,
                approval_callback=self._approval_callback,
                codex_command=self._codex_command,
            )

    def _persist_turn_messages(self, turn_result: TurnResult):
        """Write projected messages to DB exactly once."""
        thread_id = turn_result.thread_id
        turn_id = turn_result.turn_id

        for msg in turn_result.messages:
            role = msg.get("role", "")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            reasoning = msg.get("_reasoning")

            # Derive a stable provider_item_id from tool_call_id if available
            provider_item_id = None
            if tool_call_id:
                provider_item_id = tool_call_id
            elif tool_calls and isinstance(tool_calls, list) and tool_calls:
                provider_item_id = tool_calls[0].get("id")

            self._db.append_message(
                session_id=self.session_id,
                role=role,
                content=str(content) if content is not None else None,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
                reasoning=reasoning,
                codex_thread_id=thread_id,
                codex_turn_id=turn_id,
                provider_item_id=provider_item_id,
            )
