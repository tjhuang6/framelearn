"""Run report — tracks fault-tolerance events for a single pipeline run.

The pipeline has several paths that intentionally degrade instead of
crashing (skip an unhashable frame, fall back to a heuristic when an LLM
call fails, keep going after a DashScope chunk fails, etc.). Those paths
used to only ``print()`` a warning, which means the information is lost
the moment nobody is watching stdout.

This module gives every one of those paths a single place to report what
happened. At the end of a run, the collected events are:

- exposed as ``PipelineResult.warnings`` (flat, human-readable strings)
- written to ``<output_dir>/run-report.json`` for post-hoc inspection

Usage mirrors ``framelearn.privacy_tracker``:

    reporter = RunReporter(video_name="lecture.mp4")
    set_reporter(reporter)
    ...
    get_reporter().record_skipped_frame("keyframe_dedup", "...")
    ...
    reporter.write_report(output_dir / "run-report.json")
    reset_reporter()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class RunEvent:
    """A single fault-tolerance event."""
    stage: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class RunReporter:
    """Collects fault-tolerance events during a single pipeline run.

    Four buckets are tracked, matching what operators need to audit after
    a run:

    - failed_segments: a chunk/segment that failed and was dropped
      (ASR chunk, doc-generation segment, ...)
    - fallbacks: a decision/evaluation that fell back to a default instead
      of the intended logic (LLM call failed → heuristic/default used)
    - skipped_frames: a keyframe that was dropped due to a processing
      error (phash failure, capture failure, ...)
    - cache_hits: cache reuse events (subtitle/keyframe/segment cache)
    """

    def __init__(self, video_name: str = ""):
        self.video_name = video_name
        self.failed_segments: list[RunEvent] = []
        self.fallbacks: list[RunEvent] = []
        self.skipped_frames: list[RunEvent] = []
        self.cache_hits: list[RunEvent] = []

    # ── recorders ────────────────────────────────────────────────────

    def record_failed_segment(
        self, stage: str, index: Any, error: str, detail: Optional[dict] = None
    ) -> None:
        d = {"index": index, "error": error, **(detail or {})}
        event = RunEvent(stage=stage, message=f"[{stage}] 分段 {index} 失败：{error}", detail=d)
        self.failed_segments.append(event)

    def record_fallback(self, stage: str, message: str, detail: Optional[dict] = None) -> None:
        event = RunEvent(stage=stage, message=f"[{stage}] {message}", detail=detail or {})
        self.fallbacks.append(event)

    def record_skipped_frame(self, stage: str, message: str, detail: Optional[dict] = None) -> None:
        event = RunEvent(stage=stage, message=f"[{stage}] {message}", detail=detail or {})
        self.skipped_frames.append(event)

    def record_cache_hit(self, stage: str, message: str, detail: Optional[dict] = None) -> None:
        event = RunEvent(stage=stage, message=f"[{stage}] {message}", detail=detail or {})
        self.cache_hits.append(event)

    # ── readout ──────────────────────────────────────────────────────

    def get_warnings(self) -> list[str]:
        """Flat, chronological list of human-readable warnings.

        Includes failed segments, fallbacks, and skipped frames. Cache
        hits are informational (not a degradation) and are excluded.
        """
        events = self.failed_segments + self.fallbacks + self.skipped_frames
        events.sort(key=lambda e: e.timestamp)
        return [e.message for e in events]

    def has_degradation(self) -> bool:
        return bool(self.failed_segments or self.fallbacks or self.skipped_frames)

    def to_dict(self, status: str = "success", error: Optional[str] = None) -> dict[str, Any]:
        return {
            "video": self.video_name,
            "generated_at": datetime.now().isoformat(),
            "status": status,
            "error": error,
            "summary": {
                "failed_segments": len(self.failed_segments),
                "fallbacks": len(self.fallbacks),
                "skipped_frames": len(self.skipped_frames),
                "cache_hits": len(self.cache_hits),
            },
            "failed_segments": [asdict(e) for e in self.failed_segments],
            "fallbacks": [asdict(e) for e in self.fallbacks],
            "skipped_frames": [asdict(e) for e in self.skipped_frames],
            "cache_hits": [asdict(e) for e in self.cache_hits],
        }

    def write_report(self, path: Path, status: str = "success", error: Optional[str] = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(status=status, error=error), f, indent=2, ensure_ascii=False)


# ── global accessor (mirrors framelearn.privacy_tracker) ──────────────

_current_reporter: Optional[RunReporter] = None


def get_reporter() -> RunReporter:
    """Get the current global reporter (a throwaway one if none is set).

    Falling back to a throwaway instance means call sites never need to
    special-case "reporter not configured" — events just go nowhere.
    """
    global _current_reporter
    if _current_reporter is None:
        return RunReporter()
    return _current_reporter


def set_reporter(reporter: Optional[RunReporter]) -> None:
    global _current_reporter
    _current_reporter = reporter


def reset_reporter() -> None:
    global _current_reporter
    _current_reporter = None
