"""Shared helpers for parsing LLM JSON responses.

The pipeline has three LLM stages that all return JSON. Keeping the
fence-stripping / object-extraction / boolean-coercion rules in one place
makes the parsers behave consistently instead of each stage accepting a
slightly different dialect.
"""

from __future__ import annotations

import json
import re
from typing import Any


def parse_json_object(raw: str) -> dict[str, Any] | None:
    r"""Extract the first JSON object from an LLM response.

    Accepts a bare object or one wrapped in ``\`\`\`json ... \`\`\``
    fences. The fence-stripping must be outer-fence aware: if the
    model nests another code block inside the JSON value (e.g. a
    Python snippet embedded in ``blog_markdown``), a naïve ``.*?``
    regex will close the outer fence at the inner one and truncate
    the JSON. We anchor on the closing ``}`` of the outermost object
    instead.
    """
    if not raw:
        return None

    # Locate the outermost { ... } by tracking brace depth and string
    # quoting. Anything inside string literals is skipped, so embedded
    # code blocks cannot accidentally end the JSON.
    start = _find_json_object_start(raw)
    if start is None:
        return None
    end = _find_json_object_end(raw, start)
    if end is None:
        return None

    # Strip surrounding ```json / ``` fences if present.
    candidate = raw[start:end + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # One last fallback: grab any balanced { ... } block, in case
        # the response is wrapped in prose rather than clean JSON.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None
    return data


def _find_json_object_start(raw: str) -> int | None:
    """Index of the first ``{`` that begins a top-level JSON object.

    The naïve approach (skip ``\`\`\`json ... \`\`\`` fences first) is
    unsafe here: when the model embeds another code block inside a
    JSON string value (e.g. a Python snippet inside ``blog_markdown``),
    the outer fence parser can be misled by the inner ``\`\`\``. We
    instead just look for the first ``{`` and rely on
    :func:`_find_json_object_end` to skip over string literals.
    """
    idx = raw.find("{")
    return idx if idx != -1 else None


def _find_json_object_end(raw: str, start: int) -> int | None:
    """Index of the matching ``}`` for the object starting at ``start``.

    Tracks nesting depth and skips over string literals (handling both
    ``"`` and ``\\`` escapes) so an embedded ``{`` inside a JSON string
    value does not throw the depth counter off.
    """
    depth = 0
    i = start
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            if depth == 0:
                return i
            i += 1
            continue
        if c == '"':
            # Skip the whole string literal. Handles ``\\\"`` escapes.
            i += 1
            while i < n:
                if raw[i] == "\\":
                    i += 2
                    continue
                if raw[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        i += 1
    return None


def parse_bool(value: Any, *, field: str) -> bool | None:
    """Strictly parse a JSON boolean.

    ``bool("false") == True`` in Python, which turns a sloppy model's
    string response into the opposite decision. Accept only real booleans
    and the explicit strings ``"true"`` / ``"false"``; anything else is a
    schema error (``None``).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None
