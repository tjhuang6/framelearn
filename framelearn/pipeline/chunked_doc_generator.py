"""Orchestrate the anchored blog pipeline.

Flow (aligned with ``0816.md``, implementation option A):

1. Chunk raw SRT by video duration.
2. Extract heuristic frames (cached or fresh).
3. Distribute frames to chunks and insert picture markers after the
   nearest subtitle segment (raw SRT is never modified).
4. Process every chunk concurrently (bounded by ``chunking.concurrency``):
   BlogGenerator writes blog prose + ``[[FRAME:id@timestamp]]`` anchors,
   the program resolves anchors / makes precise FFmpeg captures, then
   VisionFrameEvaluator validates each frame with retakes.
5. MDAssembler replaces anchors and writes ``blog.md`` / ``srt_picture.md``.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from framelearn.config import get as config_get
from framelearn.pipeline.blog_generator import (
    ANCHOR_RE,
    BlogGenerator,
    BlogGeneratorOutput,
    FrameRequest,
    fallback_blog,
)
from framelearn.pipeline.frame_distributor import FrameDistributor
from framelearn.pipeline.heuristic_frame_extractor import (
    CandidateFrame,
    HeuristicFrameExtractor,
)
from framelearn.pipeline.md_assembler import MDAssembler
from framelearn.pipeline.run_report import get_reporter
from framelearn.pipeline.srt_chunker import SRTChunk, SRTChunker
from framelearn.pipeline.vision_frame_evaluator import (
    AnchorFrame,
    FrameEvaluation,
    VisionFrameEvaluator,
    _fallback_keep as vision_fallback_keep,
)


@dataclass
class _ChunkPipelineResult:
    """Intermediate result for one concurrently processed chunk."""

    chunk_index: int
    blog_output: BlogGeneratorOutput
    evaluations: list[FrameEvaluation]


@dataclass
class ChunkedDocResult:
    output_dir: Path
    srt_picture_path: Path
    blog_path: Path
    chunks_total: int = 0
    chunks_succeeded: int = 0
    failed_chunks: list[int] = field(default_factory=list)


def _unique_target(src_dir: Path, name: str) -> Path:
    """Return a non-colliding target path inside ``src_dir``."""
    target = src_dir / name
    if not target.exists():
        return target

    stem = Path(name).stem
    suffix = Path(name).suffix
    for i in range(2, 10_000):
        candidate = src_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"无法为帧 {name} 生成唯一文件名")


def _copy_kept_frame_to_src(frame_path: Path, src_dir: Path) -> Path:
    """Copy one kept frame into ``src_dir``."""
    frame_path = Path(frame_path)
    src_dir = Path(src_dir)
    if frame_path.parent.resolve() == src_dir.resolve():
        return frame_path
    target = _unique_target(src_dir, frame_path.name)
    shutil.copy2(frame_path, target)
    return target


def _globalize_chunk_anchors(
    chunk_index: int, output: BlogGeneratorOutput
) -> BlogGeneratorOutput:
    """Make anchor ids globally unique by prefixing them with the chunk.

    Text models are only shown one chunk at a time, so they usually emit
    ``a1`` in every chunk. Without this step anchors from different chunks
    would overwrite each other in the final evaluation map.
    """
    prefix = f"c{chunk_index}_"

    def replace(match):
        return f"[[FRAME:{prefix}{match.group('anchor_id')}@{match.group('timestamp')}]]"

    blog_markdown = ANCHOR_RE.sub(replace, output.blog_markdown)
    requests = [
        FrameRequest(
            anchor_id=prefix + request.anchor_id,
            srt_id=request.srt_id,
            timestamp=request.timestamp,
            request_type=request.request_type,
            source_frame_path=request.source_frame_path,
            reason=request.reason,
        )
        for request in output.frame_requests
    ]
    return BlogGeneratorOutput(
        blog_markdown=blog_markdown,
        frame_requests=requests,
        degraded=output.degraded,
    )


class ChunkedDocGenerator:
    """Run the anchored blog pipeline."""

    def __init__(
        self,
        segment_minutes: float | None = None,
        max_images_per_chunk: int | None = None,
        concurrency: int | None = None,
    ):
        self.segment_minutes = (
            float(segment_minutes)
            if segment_minutes is not None
            else float(config_get("chunking.segment_minutes", 10))
        )
        self.max_images_per_chunk = (
            max_images_per_chunk
            if max_images_per_chunk is not None
            else int(config_get("chunking.max_images_per_chunk", 20))
        )
        self.concurrency = (
            concurrency
            if concurrency is not None
            else int(config_get("chunking.concurrency", 5))
        )
        self.frame_match_tolerance = float(
            config_get("blog_gen.frame_match_tolerance", 2.0)
        )

    async def generate(
        self,
        video_path: str,
        srt_segments: list,
        output_dir: Path,
        video_title: str = "视频讲义",
        pre_extracted_frames: list | None = None,
    ) -> ChunkedDocResult:
        """Run the full anchored pipeline."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        src_dir = output_dir / "src"
        src_dir.mkdir(exist_ok=True)
        temp_dir = output_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
        temp_frames = temp_dir / "frames"
        keep_temp_frames = bool(config_get("video.keep_temp_files", False))

        try:
            # 1. Chunk raw SRT first (option A).
            print(f"切段（每段 {self.segment_minutes:g} 分钟）...")
            chunker = SRTChunker(segment_minutes=self.segment_minutes)
            chunks = chunker.chunk(srt_segments)
            chunks_total = len(chunks)
            if chunks_total == 0:
                raise ValueError(
                    "字幕没有可用的时间戳分段，无法按时间分块生成文档"
                )

            # 2. Heuristic frames: cache hit, or ffmpeg in a worker thread.
            if pre_extracted_frames:
                print(f"复用已缓存的 {len(pre_extracted_frames)} 个启发式帧...")
                get_reporter().record_cache_hit(
                    "chunked_doc.heuristic_cache",
                    f"命中启发式帧缓存（{len(pre_extracted_frames)} 帧），跳过 ffmpeg 场景检测",
                )
                frames = list(pre_extracted_frames)
            else:
                print("启发式截帧（ffmpeg + pHash）...")
                heuristic = HeuristicFrameExtractor()
                try:
                    frames = await asyncio.to_thread(
                        heuristic.extract, video_path, temp_frames
                    )
                except Exception as e:
                    get_reporter().record_fallback(
                        "chunked_doc.heuristic_failed",
                        f"启发式截帧失败（{e}），BlogGenerator 只能请求新截帧",
                    )
                    frames = []

            # 3. Distribute frames into chunks.
            distributor = FrameDistributor(max_per_chunk=self.max_images_per_chunk)
            frames_by_chunk = distributor.distribute(chunks, frames)

            # 4-6. Process every chunk end-to-end in parallel. The
            # semaphore bounds the number of chunks in flight, so a 3-4
            # hour video becomes e.g. 24×10min chunks with at most
            # ``chunking.concurrency`` text/vision/ffmpeg workers active.
            print(
                f"并行处理 {chunks_total} 个 chunk（并发 {self.concurrency}）..."
            )
            blog_generator = BlogGenerator()
            evaluator = VisionFrameEvaluator()
            sem = asyncio.Semaphore(self.concurrency)

            async def _process_chunk(chunk: SRTChunk) -> _ChunkPipelineResult:
                chunk_index = chunk.index
                chunk_frames = frames_by_chunk.get(chunk_index, [])
                async with sem:
                    # 4. Text model: faithful blog prose + frame anchors.
                    try:
                        output = await blog_generator.generate(chunk, chunk_frames)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        get_reporter().record_fallback(
                            "chunked_doc.blog_generator_exception",
                            (
                                f"chunk {chunk_index} 文本生成异常（{e}），"
                                "降级为原始字幕拼接"
                            ),
                            detail={"chunk_index": chunk_index},
                        )
                        output = fallback_blog(chunk)

                    output = _globalize_chunk_anchors(chunk_index, output)

                    # 5. Validate anchors / bind frames. FFmpeg is
                    # synchronous, so run it in a worker thread.
                    try:
                        eval_items = await asyncio.to_thread(
                            self._resolve_chunk_anchors,
                            chunk,
                            output.frame_requests,
                            chunk_frames,
                            video_path,
                            temp_frames,
                            local_offset=1,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        get_reporter().record_fallback(
                            "chunked_doc.anchor_resolution_failed",
                            (
                                f"chunk {chunk_index} 锚点绑定失败（{e}），"
                                "已丢弃该 chunk 配图"
                            ),
                            detail={"chunk_index": chunk_index},
                        )
                        eval_items = []

                    # 6. Vision model validates frames, including retakes.
                    if not eval_items:
                        evaluations: list[FrameEvaluation] = []
                    else:
                        try:
                            evaluations = await evaluator.evaluate(
                                eval_items, video_path, temp_frames
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            get_reporter().record_fallback(
                                "chunked_doc.vision_evaluator_exception",
                                (
                                    f"chunk {chunk_index} 视觉验图异常（{e}），"
                                    f"保守保留 {len(eval_items)} 张候选帧"
                                ),
                                detail={"chunk_index": chunk_index},
                            )
                            evaluations = [
                                vision_fallback_keep(item) for item in eval_items
                            ]

                    return _ChunkPipelineResult(
                        chunk_index=chunk_index,
                        blog_output=output,
                        evaluations=evaluations,
                    )

            chunk_results = await asyncio.gather(
                *(_process_chunk(c) for c in chunks)
            )

            # Convert local srt_id to global srt_id for assembly.
            blog_markdowns: list[str] = []
            evaluations: list[FrameEvaluation] = []
            chunks_succeeded = 0
            offset = 1
            for chunk, chunk_result in zip(chunks, chunk_results):
                blog_markdowns.append(chunk_result.blog_output.blog_markdown)
                if not chunk_result.blog_output.degraded:
                    chunks_succeeded += 1
                for evaluation in chunk_result.evaluations:
                    evaluation.srt_id += offset - 1
                    evaluations.append(evaluation)
                offset += len(chunk.segments)

            # 7. Copy kept frames to src/ so Markdown references resolve.
            evaluations_by_anchor: dict[str, FrameEvaluation] = {}
            for evaluation in evaluations:
                if not evaluation.keep_image:
                    evaluations_by_anchor[evaluation.anchor_id] = evaluation
                    continue
                source_path = Path(evaluation.frame_path)
                if not source_path.exists():
                    get_reporter().record_skipped_frame(
                        "chunked_doc.copy_kept_frame",
                        f"帧文件不存在，跳过图片引用：{source_path}",
                        detail={"anchor_id": evaluation.anchor_id},
                    )
                    evaluation.keep_image = False
                else:
                    try:
                        final_path = _copy_kept_frame_to_src(source_path, src_dir)
                        evaluation.frame_path = str(final_path)
                    except Exception as e:
                        get_reporter().record_skipped_frame(
                            "chunked_doc.copy_kept_frame",
                            f"复制保留帧失败：{e}",
                            detail={"anchor_id": evaluation.anchor_id},
                        )
                        evaluation.keep_image = False
                evaluations_by_anchor[evaluation.anchor_id] = evaluation

            # 8. Assemble Markdown.
            print("拼装 Markdown...")
            big_raw: list = []
            for chunk in chunks:
                big_raw.extend(chunk.segments)

            assembler = MDAssembler()
            srt_p, blog_p = assembler.write_anchored(
                output_dir,
                big_raw,
                blog_markdowns,
                evaluations_by_anchor,
                video_title=video_title,
            )

            failed_chunks = [
                chunk.index
                for chunk, chunk_result in zip(chunks, chunk_results)
                if chunk_result.blog_output.degraded
            ]
            return ChunkedDocResult(
                output_dir=output_dir,
                srt_picture_path=srt_p,
                blog_path=blog_p,
                chunks_total=chunks_total,
                chunks_succeeded=chunks_succeeded,
                failed_chunks=failed_chunks,
            )
        finally:
            if not keep_temp_frames:
                shutil.rmtree(temp_frames, ignore_errors=True)

    def _resolve_chunk_anchors(
        self,
        chunk: SRTChunk,
        requests: list[FrameRequest],
        frames: list[CandidateFrame],
        video_path: str,
        temp_frames: Path,
        local_offset: int = 1,
    ) -> list[AnchorFrame]:
        """Validate requests and produce concrete ``AnchorFrame`` items."""
        resolved: list[AnchorFrame] = []
        for request in requests:
            local_srt_id = request.srt_id - local_offset + 1
            subtitle = (
                str(getattr(chunk.segments[local_srt_id - 1], "text", "") or "").strip()
                if 1 <= local_srt_id <= len(chunk.segments)
                else ""
            )

            if request.request_type == "reuse":
                match = next(
                    (f for f in frames if f.path == request.source_frame_path),
                    None,
                )
                if match is None:
                    get_reporter().record_fallback(
                        "chunked_doc.invalid_anchor",
                        f"锚点 {request.anchor_id} 引用了不存在的候选帧，已删除",
                        detail={"source_frame_path": request.source_frame_path},
                    )
                    continue
                resolved.append(
                    AnchorFrame(
                        anchor_id=request.anchor_id,
                        srt_id=request.srt_id,
                        frame_path=match.path,
                        timestamp=match.timestamp_sec,
                        subtitle_text=subtitle,
                    )
                )
                continue

            # new_capture
            if frames:
                nearest = min(
                    frames,
                    key=lambda f: abs(f.timestamp_sec - request.timestamp),
                )
                if abs(nearest.timestamp_sec - request.timestamp) <= self.frame_match_tolerance:
                    resolved.append(
                        AnchorFrame(
                            anchor_id=request.anchor_id,
                            srt_id=request.srt_id,
                            frame_path=nearest.path,
                            timestamp=nearest.timestamp_sec,
                            subtitle_text=subtitle,
                        )
                    )
                    continue

            target = (
                temp_frames
                / "precise"
                / f"chunk_{chunk.index}"
                / f"{request.anchor_id}_{request.timestamp:.3f}.jpg"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            from framelearn.pipeline.ffmpeg_helper import FFmpegHelper

            ok = FFmpegHelper.capture_single_frame(
                video_path, request.timestamp, str(target)
            )
            if not ok:
                get_reporter().record_skipped_frame(
                    "chunked_doc.precise_capture",
                    f"锚点 {request.anchor_id} 精准补截失败，已删除",
                    detail={"timestamp": request.timestamp},
                )
                continue
            resolved.append(
                AnchorFrame(
                    anchor_id=request.anchor_id,
                    srt_id=request.srt_id,
                    frame_path=str(target),
                    timestamp=request.timestamp,
                    subtitle_text=subtitle,
                )
            )
        return resolved

