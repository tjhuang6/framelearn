"""Filesystem helpers shared across the pipeline."""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically.

    The content is written to a temporary sibling file and then renamed
    over the target, so an interrupted pipeline can never leave a
    half-written Markdown / manifest / report behind.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
