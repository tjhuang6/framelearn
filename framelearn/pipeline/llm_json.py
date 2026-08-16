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
    """Extract the first JSON object from an LLM response.

    Accepts a bare object or one wrapped in ```json fences. Returns
    ``None`` when no valid JSON object can be found.
    """
    if not raw:
        return None

    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    candidate = fenced.group(1) if fenced else raw

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None
    return data


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
