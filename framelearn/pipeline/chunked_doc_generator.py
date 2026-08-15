"""Orchestrate the chunked SRT-clean → heuristic-frame → vision-2-stage flow.

Per video this issues 3 batches of LLM calls (one text-clean per chunk,
one Stage1 per chunk, one Stage2 per chunk) regardless of video length.
The text LLM and vision LLM calls within a stage run concurrently,
bounded by ``[chunking] concurrency``.

Single-chunk failures never abort the pipeline — we drop the failed
chunk's blog text / decisions and keep going.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

from framelearn.config import get as config_get
from framelearn.pipeline.frame_distributor import FrameDistributor
from framelearn.pipeline.heuristic_frame_extractor import (
    CandidateFrame,
    HeuristicFrameExtractor,
)
from framelearn.pipeline.md_assembler import MDAssembler
from framelearn.pipeline.run_report import get_reporter
from framelearn.pipeline.srt_chunker import SRTChunk, SRTChunker
from framelearn.pipeline.text_cleaner import TextCleaner
from framelearn.pipeline.vision_stage1 import VisionStage1, extract_new_frames
from framelearn.pipeline.vision_stage2 import VisionStage2, FrameDecision


@dataclass
class ChunkedDocResult:
    output_dir: Path
    srt_picture_path: Path
    blog_path: Path
    chunks_total: int = 0
    chunks_succeeded: int = 0


class ChunkedDocGenerator:
    """Run the full chunked pipeline.

    Parameters come from settings.toml unless overridden in the constructor.
    """

    def __init__(
        self,
        segment_minutes: int | None = None,
        max_images_per_chunk: int | None = None,
        concurrency: int | None = None,
    ):
        self.segment_minutes = (
            segment_minutes
            if segment_minutes is not None
            else int(config_get("chunking.segment_minutes", 30))
        )
        self.max_images_per_chunk = (
            max_images_per_chunk
            if max_images_per_chunk is not None
            else int(config_get("chunking.max_images_per_chunk", 50))
        )
        self.concurrency = (
            concurrency
            if concurrency is not None
            else int(config_get("chunking.concurrency", 5))
        )

    async def generate(
        self,
        video_path: str,
        srt_segments: list,
        output_dir: Path,
        video_title: str = "视频讲义",
        pre_extracted_frames: list | None = None,
    ) -> ChunkedDocResult:
        """Run all stages. Returns paths to the two Markdown files.

        ``srt_segments`` must be a list of objects with ``start``/``end``/
        ``text`` attributes (TranscriptSegment-compatible).

        ``pre_extracted_frames``, if provided, bypasses the heuristic
        ffmpeg/pHash step. Used by :class:`VideoPipeline` to honour the
        keyframe manifest cache: a previous run's frames are reused as
        long as the manifest still validates.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        src_dir = output_dir / "src"
        src_dir.mkdir(exist_ok=True)
        temp_dir = output_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
        temp_frames = temp_dir / "frames"

        chunks_total = 0

        # 1. Chunk + clean SRT (in parallel across chunks).
        print(f"🔪 切段（每段 {self.segment_minutes} 分钟）+ 文本清洗...")
        chunker = SRTChunker(segment_minutes=self.segment_minutes)
        chunks = chunker.chunk(srt_segments)
        chunks_total = len(chunks)

        cleaner = TextCleaner(concurrency=self.concurrency)
        cleaned_chunks = await cleaner.clean_all(chunks)

        # Flatten cleaned chunks into one segment list for the heuristic
        # step's downstream use and for the SRT-style output.
        big_cleaned: list = []
        for c in cleaned_chunks:
            big_cleaned.extend(c.segments)

        # 2. Heuristic frame extraction (whole video, no LLM).
        #    If the caller already has a validated set, reuse it.
        if pre_extracted_frames:
            print(f"⏭️  复用已缓存的 {len(pre_extracted_frames)} 个启发式帧...")
            get_reporter().record_cache_hit(
                "chunked_doc.heuristic_cache",
                f"命中启发式帧缓存（{len(pre_extracted_frames)} 帧），跳过 ffmpeg 场景检测",
            )
            frames = list(pre_extracted_frames)
        else:
            print("🎞️  启发式截帧（ffmpeg + pHash）...")
            heuristic = HeuristicFrameExtractor()
            try:
                frames = heuristic.extract(video_path, temp_frames)
            except Exception as e:
                get_reporter().record_fallback(
                    "chunked_doc.heuristic_failed",
                    f"启发式截帧失败（{e}），Stage1 将无图输入",
                )
                frames = []

        # Copy kept heuristic frames into the output's src/ directory so
        # the assembled Markdown references resolve.
        for f in frames:
            shutil.copy(f.path, src_dir / Path(f.path).name)

        # 3. Distribute frames into chunks (re-chunk using the cleaned
        # segments so each chunk's frame bucket lines up with the cleaned
        # text we'll send to Stage1).
        print("📦 帧分配到 chunk...")
        distributor = FrameDistributor(max_per_chunk=self.max_images_per_chunk)
        frames_by_chunk = distributor.distribute(cleaned_chunks, frames)

        # 4. Stage1 + Stage2 per chunk (sequential per chunk, parallel
        # across chunks up to ``concurrency``).
        print("🤖 Stage1（文本+图）+ ffmpeg 新截 + Stage2（看图）...")
        sem = asyncio.Semaphore(self.concurrency)

        async def _run_chunk(idx: int, chunk: SRTChunk):
            async with sem:
                return await self._process_chunk(
                    chunk, frames_by_chunk.get(idx, []), video_path, temp_frames
                )

        results = await asyncio.gather(
            *(_run_chunk(i, c) for i, c in enumerate(cleaned_chunks)),
            return_exceptions=False,
        )
        blog_markdowns = [r[0] for r in results]
        all_decisions: list[FrameDecision] = []
        for r in results:
            all_decisions.extend(r[1])
        chunks_succeeded = sum(1 for r in results if r[1] is not None)

        # 5. Assemble Markdown.
        print("📝 拼装 Markdown...")
        assembler = MDAssembler()
        srt_p, blog_p = assembler.write(
            output_dir,
            big_cleaned,
            blog_markdowns,
            all_decisions,
            video_title=video_title,
        )

        return ChunkedDocResult(
            output_dir=output_dir,
            srt_picture_path=srt_p,
            blog_path=blog_p,
            chunks_total=chunks_total,
            chunks_succeeded=chunks_succeeded,
        )

    async def _process_chunk(
        self,
        chunk: SRTChunk,
        heuristic_frames: list[CandidateFrame],
        video_path: str,
        temp_frames: Path,
    ) -> tuple[str, list[FrameDecision]]:
        """Stage1 → ffmpeg new frames → Stage2 for one chunk.

        Returns ``(blog_markdown, decisions)``. ``decisions`` is ``[]`` on
        chunk-level failure so the assembler still gets a slot.
        """
        try:
            stage1 = VisionStage1(max_images=self.max_images_per_chunk)
            s1_out = await stage1.process(chunk, heuristic_frames)

            # Capture any new frames Stage1 requested.
            new_frames = extract_new_frames(
                s1_out.selected_timestamps,
                video_path,
                chunk_index=chunk.index,
                output_dir=temp_frames,
            )

            # All frames Stage1+Stage2 will see, in display order.
            all_frames = list(heuristic_frames) + new_frames

            # Build the srt_id-per-frame map: each frame is associated with
            # the srt_id Stage1 picked for it. New frames inherit the
            # srt_id of their SelectionTimestamp.
            srt_id_per_frame: list[int] = []
            for f in heuristic_frames:
                # Stage1 may have re-stamped the timestamp but kept the
                # heuristic frame — find the matching SelectedTimestamp by
                # source_frame_path, fallback to chunk-relative index.
                match = next(
                    (
                        s
                        for s in s1_out.selected_timestamps
                        if s.source_frame_path == f.path
                    ),
                    None,
                )
                srt_id_per_frame.append(match.srt_id if match else 0)
            for j, f in enumerate(new_frames):
                # New frames correspond positionally to needs_extract=True
                # entries in s1_out.selected_timestamps.
                new_selected = [
                    s
                    for s in s1_out.selected_timestamps
                    if s.needs_extract
                ]
                if j < len(new_selected):
                    srt_id_per_frame.append(new_selected[j].srt_id)
                else:
                    srt_id_per_frame.append(0)

            stage2 = VisionStage2()
            decisions = await stage2.process(chunk, all_frames, srt_id_per_frame)

            return s1_out.blog_markdown, decisions

        except Exception as e:
            get_reporter().record_fallback(
                "chunked_doc.chunk_failed",
                f"chunk {chunk.index} 处理失败（{e}），整段缺失",
            )
            return "", []