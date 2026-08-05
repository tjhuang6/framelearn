"""Keyframe deduplication using perceptual hashing."""

from pathlib import Path

import imagehash
from PIL import Image

from framelearn.pipeline.run_report import get_reporter


class KeyframeDeduplicator:
    """Remove visually similar keyframes using perceptual hashing."""

    def __init__(self, similarity_threshold: float = 0.9):
        """
        Args:
            similarity_threshold: Frames with similarity > this are considered duplicates (0.0-1.0)
        """
        self.similarity_threshold = similarity_threshold

    def deduplicate(
        self,
        frames: list[tuple[Path, float]],
        max_frames: int = 100,
    ) -> list[tuple[Path, float]]:
        """Remove duplicate frames and limit count.

        Args:
            frames: List of (frame_path, timestamp_seconds) tuples
            max_frames: Maximum number of frames to keep

        Returns:
            Deduplicated list of (frame_path, timestamp_seconds) tuples
        """
        if not frames:
            return []

        unique_frames = []
        seen_hashes = []

        for frame_path, timestamp in frames:
            try:
                img = Image.open(frame_path)
                phash = imagehash.phash(img)

                # Check similarity with all previously seen frames
                is_duplicate = False
                for seen_hash in seen_hashes:
                    similarity = 1.0 - (phash - seen_hash) / 64.0  # Hamming distance normalized
                    if similarity > self.similarity_threshold:
                        is_duplicate = True
                        break

                if not is_duplicate:
                    unique_frames.append((frame_path, timestamp))
                    seen_hashes.append(phash)

                    if len(unique_frames) >= max_frames:
                        break

            except Exception as e:
                # If hashing fails, skip this frame — but record it so the
                # skip is visible in the run report instead of silently
                # disappearing.
                get_reporter().record_skipped_frame(
                    "keyframe_dedup",
                    f"帧 {frame_path.name} 计算 pHash 失败，已跳过：{e}",
                    detail={"frame": str(frame_path), "timestamp": timestamp},
                )
                continue

        # Ensure at least 1 frame
        if not unique_frames and frames:
            unique_frames = [frames[0]]

        return unique_frames
