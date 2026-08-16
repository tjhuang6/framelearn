"""Distribute a flat list of candidate frames across SRT chunks.

The heuristic extractor produces frames for the whole video. Each frame
has a ``timestamp_sec``. Here we bucket those frames by which SRTChunk
they fall into, so Stage1 can see only the frames that are relevant to
its current chunk (keeps the prompt small and keeps context usage low).

Boundary handling: a frame exactly at ``chunk.end_sec`` is assigned to
the NEXT chunk. The chunker itself places boundaries on segment
``start_sec``, so exact-boundary collisions are rare — when they happen
the frame is visible to both chunks' Stage1 calls anyway (one of them
will likely keep it, the other discard).
"""

from __future__ import annotations

from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.srt_chunker import SRTChunk


class FrameDistributor:
    """Group candidate frames by chunk, capping each chunk's count."""

    def __init__(self, max_per_chunk: int = 50):
        if max_per_chunk <= 0:
            raise ValueError(f"max_per_chunk must be > 0, got {max_per_chunk}")
        self.max_per_chunk = max_per_chunk

    def distribute(
        self,
        chunks: list[SRTChunk],
        frames: list[CandidateFrame],
    ) -> dict[int, list[CandidateFrame]]:
        """Return ``{chunk_index: [CandidateFrame, ...]}``.

        Every chunk index in the input appears in the output, even if its
        bucket is empty. Order within a bucket is preserved (input order,
        which is already ascending by timestamp from the heuristic
        extractor).
        """
        result: dict[int, list[CandidateFrame]] = {c.index: [] for c in chunks}
        if not chunks or not frames:
            return result

        for frame in frames:
            ts = frame.timestamp_sec
            for chunk in chunks:
                # Inclusive lower, exclusive upper — boundary frame goes
                # to the next chunk.
                if chunk.start_sec <= ts < chunk.end_sec:
                    result[chunk.index].append(frame)
                    break
            else:
                # Frame outside all chunk ranges — assign to the nearest
                # chunk (by absolute distance to midpoint). This happens
                # when the heuristic extractor returned a frame past the
                # last chunk's end_sec (rare; usually the last chunk
                # extends to the end of the video).
                nearest = min(
                    chunks,
                    key=lambda c: abs(ts - (c.start_sec + c.end_sec) / 2),
                )
                result[nearest.index].append(frame)

        # Cap each chunk at max_per_chunk — keep timestamps evenly spread.
        for idx in result:
            bucket = result[idx]
            if len(bucket) > self.max_per_chunk:
                result[idx] = _evenly_subsample(bucket, self.max_per_chunk)

        return result


def _evenly_subsample(
    items: list[CandidateFrame], k: int
) -> list[CandidateFrame]:
    """Pick ``k`` items evenly spaced through ``items`` (assumed sorted)."""
    if k <= 0 or not items:
        return []
    if k == 1:
        return [items[0]]
    if k >= len(items):
        return list(items)
    step = (len(items) - 1) / (k - 1)
    indices = sorted({round(i * step) for i in range(k)})
    return [items[i] for i in indices]