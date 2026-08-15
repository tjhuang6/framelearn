"""Heuristic frame extraction — ffmpeg scene detection + pHash dedup.

This is the "no LLM" first pass. It pulls a coarse set of candidate frames
across the whole video, which the vision model in Stage1 will then review,
drop, augment, and refine. Output is uniform :class:`CandidateFrame`
records so the downstream distributor / vision stages don't care whether a
frame came from heuristics or from Stage1's later extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from framelearn.config import get as config_get
from framelearn.pipeline.ffmpeg_helper import FFmpegHelper
from framelearn.pipeline.keyframe_dedup import KeyframeDeduplicator


@dataclass
class CandidateFrame:
    """A frame candidate, regardless of how it was produced.

    Attributes:
        path: Absolute or relative path to the JPEG file.
        timestamp_sec: Video timestamp (seconds, float).
        source: Where this frame came from. ``"heuristic"`` for the
            first-pass ffmpeg/dedup pass, ``"stage1"`` for frames
            requested by the vision model after seeing the heuristic set.
    """

    path: str
    timestamp_sec: float
    source: str = "heuristic"

    def __post_init__(self):
        # Normalize path to a string so downstream code can pass it
        # directly to FFmpeg / vision APIs.
        if not isinstance(self.path, str):
            self.path = str(self.path)


class HeuristicFrameExtractor:
    """Run ffmpeg scene detection + pHash dedup on the whole video.

    Parameters come from ``settings.toml [heuristic]`` by default:
        scene_threshold = 0.4
        similarity_threshold = 0.95
    """

    def __init__(
        self,
        scene_threshold: float | None = None,
        similarity_threshold: float | None = None,
        max_frames: int | None = None,
    ):
        self.scene_threshold = (
            scene_threshold
            if scene_threshold is not None
            else float(config_get("heuristic.scene_threshold", 0.4))
        )
        self.similarity_threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else float(config_get("heuristic.similarity_threshold", 0.95))
        )
        self.max_frames = (
            max_frames
            if max_frames is not None
            else int(config_get("heuristic.max_frames", 200))
        )

    def extract(self, video_path: str, output_dir: Path) -> list[CandidateFrame]:
        """Extract a deduplicated set of candidate frames.

        Args:
            video_path: Path to the source video.
            output_dir: Directory where intermediate frames are written.
                Caller is responsible for cleanup (typically a temp dir).

        Returns:
            List of :class:`CandidateFrame`, sorted by ``timestamp_sec``.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        raw = FFmpegHelper.extract_keyframes(
            video_path,
            str(output_dir),
            scene_threshold=self.scene_threshold,
            max_frames=self.max_frames,
        )
        dedup = KeyframeDeduplicator(similarity_threshold=self.similarity_threshold)
        unique = dedup.deduplicate(raw, max_frames=self.max_frames)

        candidates = [
            CandidateFrame(
                path=str(path),
                timestamp_sec=timestamp,
                source="heuristic",
            )
            for path, timestamp in unique
        ]
        candidates.sort(key=lambda f: f.timestamp_sec)
        return candidates