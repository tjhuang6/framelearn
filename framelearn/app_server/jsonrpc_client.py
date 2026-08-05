"""JSON-RPC stdio client for Codex app-server.

Handles the raw transport layer only:
- Spawning the codex app-server subprocess
- Writing requests to stdin
- Dispatching stdout messages into three queues:
    pending    — responses to our requests (matched by id)
    notifications — server-sent events (no id)
    server_requests — server-initiated requests (have id + method)
- Reading stderr independently for diagnostics
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
from typing import Optional


# Patterns to redact from stderr before surfacing to users
_REDACT_PATTERNS = [
    re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key[=:]\s*)\S+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
]

_STDERR_MAX_LINES = 500


def _redact(line: str) -> str:
    for pattern in _REDACT_PATTERNS:
        line = pattern.sub(r"\g<1>[REDACTED]", line)
    return line


class RpcError(RuntimeError):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(f"RPC {code}: {message}")
        self.code = code
        self.data = data


class JsonRpcStdioClient:
    """Low-level JSON-RPC client over subprocess stdin/stdout.

    This layer is intentionally narrow — it knows nothing about sessions,
    approval policies, or message projection. It only handles transport.
    """

    def __init__(
        self,
        command: tuple[str, ...] = ("codex", "app-server"),
        env: Optional[dict] = None,
    ):
        spawn_env = self._build_env(env)

        self.proc = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=spawn_env,
        )

        self._next_id = 1
        self._next_id_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()

        self.notifications: queue.Queue[dict] = queue.Queue()
        self.server_requests: queue.Queue[dict] = queue.Queue()
        self.stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()

        threading.Thread(target=self._read_stdout, daemon=True, name="rpc-stdout").start()
        threading.Thread(target=self._read_stderr, daemon=True, name="rpc-stderr").start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        """Send a JSON-RPC request and block until the response arrives."""
        with self._next_id_lock:
            request_id = self._next_id
            self._next_id += 1

        result_queue: queue.Queue[dict] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = result_queue

        self._send({"id": request_id, "method": method, "params": params or {}})

        try:
            response = result_queue.get(timeout=timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"'{method}' timed out after {timeout}s")

        if "error" in response:
            err = response["error"]
            raise RpcError(err.get("code", -1), err.get("message", ""), err.get("data"))

        return response.get("result") or {}

    def notify(self, method: str, params: Optional[dict] = None):
        """Send a JSON-RPC notification (no response expected)."""
        self._send({"method": method, "params": params or {}})

    def respond(self, request_id, result: dict):
        """Respond to a server-initiated request."""
        self._send({"id": request_id, "result": result})

    def respond_error(self, request_id, code: int = -32601, message: str = "Unsupported method"):
        """Respond with an error to a server-initiated request.

        Always call this for unknown server requests — Codex will wait forever
        if no response is sent.
        """
        self._send({"id": request_id, "error": {"code": code, "message": message}})

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def last_stderr(self, n: int = 20) -> str:
        with self._stderr_lock:
            return "\n".join(self.stderr_lines[-n:])

    def close(self):
        """Terminate the subprocess gracefully, then forcibly if needed."""
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send(self, obj: dict):
        if self.proc.stdin is None or self.proc.stdin.closed:
            raise RuntimeError("stdin is unavailable — subprocess may have exited")
        wire = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(wire)
        self.proc.stdin.flush()

    def _read_stdout(self):
        assert self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                msg = json.loads(stripped)
            except Exception:
                continue
            self._dispatch(msg)

    def _dispatch(self, msg: dict):
        has_id = "id" in msg
        has_method = "method" in msg
        has_result_or_error = "result" in msg or "error" in msg

        if has_id and has_result_or_error and not has_method:
            # Response to one of our requests
            with self._pending_lock:
                waiter = self._pending.pop(msg["id"], None)
            if waiter:
                waiter.put_nowait(msg)
        elif has_id and has_method:
            # Server-initiated request — must be responded to
            self.server_requests.put(msg)
        elif has_method:
            # Notification (no id)
            self.notifications.put(msg)

    def _read_stderr(self):
        assert self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            line = _redact(raw.decode("utf-8", "replace").rstrip())
            with self._stderr_lock:
                self.stderr_lines.append(line)
                if len(self.stderr_lines) > _STDERR_MAX_LINES:
                    self.stderr_lines = self.stderr_lines[-_STDERR_MAX_LINES:]

    @staticmethod
    def _build_env(override: Optional[dict]) -> dict:
        """Build subprocess environment using allowlist.

        Only passes essential system variables and Codex-specific config.
        Blocks all API keys, secrets, and cloud credentials by default.

        Allowlist categories:
        1. Core system: PATH, HOME, USER, SHELL, TMPDIR, etc.
        2. Locale/display: LANG, LC_*, TERM, DISPLAY
        3. Development tools: Git, SSH, GPG agent sockets
        4. Codex-specific: CODEX_HOME, CODEX_*
        5. Explicitly allowed via override parameter
        """
        # Allowlist of environment variables safe to pass to Codex subprocess
        _ALLOWED_KEYS = {
            # Core system
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "TMPDIR",
            "TEMP",
            "TMP",
            # Locale and display
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "LC_MESSAGES",
            "TERM",
            "DISPLAY",
            "COLORTERM",
            # Development tools
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            "GPG_AGENT_INFO",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
            # Node.js
            "NODE_ENV",
            # Python
            "PYTHONPATH",
            "PYTHONIOENCODING",
            "VIRTUAL_ENV",
            # XDG
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
        }

        # Build environment from allowlist
        env = {}
        for key in _ALLOWED_KEYS:
            if key in os.environ:
                env[key] = os.environ[key]

        # Include any CODEX_* variables
        for key, value in os.environ.items():
            if key.startswith("CODEX_"):
                env[key] = value

        # Apply explicit overrides (caller takes responsibility)
        if override:
            env.update(override)

        return env
