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
from framelearn.errors import GenerationError
from framelearn.pipeline.blog_generator import (
    ANCHOR_RE,
    BlogGenerator,
    BlogGeneratorOutput,
    FrameRequest,
)
from framelearn.pipeline.frame_distributor import FrameDistributor
from framelearn.pipeline.heuristic_frame_extractor import (
    CandidateFrame,
    HeuristicFrameExtractor,
)
from framelearn.pipeline.md_assembler import MDAssembler
from framelearn.pipeline.run_report import (
    RunReporter,
    get_reporter,
    reset_reporter,
    set_reporter,
)
from framelearn.pipeline.srt_chunker import SRTChunk, SRTChunker
from framelearn.pipeline.vision_frame_evaluator import (
    AnchorFrame,
    FrameEvaluation,
    VisionFrameEvaluator,
)


@dataclass
class _ChunkPipelineResult:
    """Intermediate result for one concurrently processed chunk."""

    chunk_index: int
    blog_output: BlogGeneratorOutput
    evaluations: list[FrameEvaluation]
    reporter: RunReporter | None = field(default=None)


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


def _resolve_chunk_anchors(
    chunk: SRTChunk,
    requests: list[FrameRequest],
    frames: list[CandidateFrame],
    video_path: str,
    temp_frames: Path,
    frame_match_tolerance: float,
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
                    f"锚点 {request.anchor_id} 引用了不存在的候选帧，已停止运行",
                    detail={"source_frame_path": request.source_frame_path},
                )
                raise GenerationError(
                    f"锚点 {request.anchor_id} 引用了不存在的候选帧："
                    f"{request.source_frame_path}"
                )
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
            if (
                abs(nearest.timestamp_sec - request.timestamp)
                <= frame_match_tolerance
            ):
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
                f"锚点 {request.anchor_id} 精准补截失败，已停止运行",
                detail={"timestamp": request.timestamp},
            )
            raise GenerationError(
                f"锚点 {request.anchor_id} 在 {request.timestamp:.3f}s "
                "精准补截失败"
            )
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


async def _process_chunk_async(
    chunk: SRTChunk,
    chunk_frames: list[CandidateFrame],
    video_path: str,
    temp_frames: Path,
    blog_generator: BlogGenerator,
    evaluator: VisionFrameEvaluator,
    frame_match_tolerance: float,
    raw_dump_path: Path | None = None,
    dump_only_on_failure: bool = True,
) -> _ChunkPipelineResult:
    """Run text generation -> anchor resolution -> vision review for one chunk.

    No degraded fallback is produced here. Any failure propagates and
    aborts the whole run.
    """
    chunk_index = chunk.index

    # 4. Text model: faithful blog prose + frame anchors.
    output = await blog_generator.generate(
        chunk,
        chunk_frames,
        raw_dump_path=raw_dump_path,
        dump_only_on_failure=dump_only_on_failure,
    )
    output = _globalize_chunk_anchors(chunk_index, output)

    # 5. Validate anchors / bind frames. FFmpeg is synchronous, so run it
    # in a worker thread.
    eval_items = await asyncio.to_thread(
        _resolve_chunk_anchors,
        chunk,
        output.frame_requests,
        chunk_frames,
        video_path,
        temp_frames,
        frame_match_tolerance,
        local_offset=1,
    )

    # 6. Vision model validates frames, including retakes.
    if not eval_items:
        evaluations: list[FrameEvaluation] = []
    else:
        evaluations = await evaluator.evaluate(
            eval_items,
            video_path,
            temp_frames,
            raw_dump_path=raw_dump_path,
            dump_only_on_failure=dump_only_on_failure,
        )

    return _ChunkPipelineResult(
        chunk_index=chunk_index,
        blog_output=output,
        evaluations=evaluations,
    )


def _process_chunk_worker(
    job: tuple[
        SRTChunk,
        list[CandidateFrame],
        str,
        Path,
        float,
        Path | None,
        bool,
    ],
) -> _ChunkPipelineResult:
    """Process one chunk inside a ProcessPoolExecutor worker.

    Each worker gets its own ``RunReporter``; the parent replays those
    events into the global reporter after the pool finishes. Workers
    also append raw LLM responses to ``job[5]`` (an absolute
    ``output/temp/raw_responses.jsonl`` path created by the parent),
    giving the operator a single interleaved transcript of every
    attempt across every chunk.
    """
    (
        chunk,
        chunk_frames,
        video_path,
        temp_frames,
        frame_match_tolerance,
        raw_dump_path,
        dump_only_on_failure,
    ) = job
    reporter = RunReporter(video_name="")
    set_reporter(reporter)
    try:
        blog_generator = BlogGenerator()
        evaluator = VisionFrameEvaluator()
        result = asyncio.run(
            _process_chunk_async(
                chunk,
                chunk_frames,
                video_path,
                temp_frames,
                blog_generator,
                evaluator,
                frame_match_tolerance,
                raw_dump_path,
                dump_only_on_failure,
            )
        )
        result.reporter = reporter
        return result
    finally:
        reset_reporter()


def _merge_chunk_reporter(result: _ChunkPipelineResult) -> None:
    """Replay events recorded in a worker process into the global reporter."""
    if result.reporter is None:
        return
    target = get_reporter()
    target.failed_segments.extend(result.reporter.failed_segments)
    target.fallbacks.extend(result.reporter.fallbacks)
    target.skipped_frames.extend(result.reporter.skipped_frames)
    target.cache_hits.extend(result.reporter.cache_hits)


class ChunkedDocGenerator:
    """Run the anchored blog pipeline."""

    def __init__(
        self,
        segment_minutes: float | None = None,
        max_images_per_chunk: int | None = None,
        concurrency: int | None = None,
        parallel_mode: str | None = None,
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
        mode = str(
            parallel_mode
            if parallel_mode is not None
            else config_get("chunking.parallel_mode", "async")
        ).strip().lower()
        if mode in ("async", "asyncio"):
            self.parallel_mode = "async"
        elif mode in ("process", "processes", "multiprocessing"):
            self.parallel_mode = "process"
        else:
            raise ValueError(
                "chunking.parallel_mode 必须是 'async' 或 'process'，"
                f"当前值：{mode!r}"
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
            # 0. Post-mortem dump for raw LLM responses. Controlled by
            # ``[blog_gen] dump_raw_responses`` and
            # ``dump_raw_on_success`` — both default to capturing only
            # failed attempts so a clean run produces an empty (or
            # absent) file. The path is recorded in run-report.json so
            # the operator can inspect it after the fact.
            raw_dump_path: Path | None = None
            if bool(config_get("blog_gen.dump_raw_responses", True)):
                raw_dump_path = temp_dir / "raw_responses.jsonl"
                if raw_dump_path.exists():
                    # Fresh run: previous content is stale and would mix
                    # chunk indices across runs.
                    raw_dump_path.unlink()
            dump_only_on_failure = not bool(
                config_get("blog_gen.dump_raw_on_success", False)
            )

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
                    get_reporter().record_repair(
                        "chunked_doc.heuristic_unavailable",
                        f"启发式截帧不可用（{e}），BlogGenerator 将使用精准补截",
                    )
                    frames = []

            # 3. Distribute frames into chunks.
            distributor = FrameDistributor(max_per_chunk=self.max_images_per_chunk)
            frames_by_chunk = distributor.distribute(chunks, frames)

            # 4-6. Process every chunk end-to-end in parallel. The async
            # backend bounds the number of chunks in flight with a
            # semaphore; the process backend uses ProcessPoolExecutor so
            # CPU-heavy chunk work can use multiple cores.
            if self.parallel_mode == "process":
                print(
                    f"多进程并行处理 {chunks_total} 个 chunk"
                    f"（进程数 {self.concurrency}）..."
                )
                chunk_results = await asyncio.to_thread(
                    self._run_chunks_in_process_pool,
                    chunks,
                    frames_by_chunk,
                    video_path,
                    temp_frames,
                    raw_dump_path,
                    dump_only_on_failure,
                )
            else:
                print(
                    f"并行处理 {chunks_total} 个 chunk"
                    f"（并发 {self.concurrency}）..."
                )
                blog_generator = BlogGenerator()
                evaluator = VisionFrameEvaluator()
                sem = asyncio.Semaphore(self.concurrency)

                async def _process_chunk(chunk: SRTChunk) -> _ChunkPipelineResult:
                    chunk_frames = frames_by_chunk.get(chunk.index, [])
                    async with sem:
                        return await _process_chunk_async(
                            chunk,
                            chunk_frames,
                            video_path,
                            temp_frames,
                            blog_generator,
                            evaluator,
                            self.frame_match_tolerance,
                            raw_dump_path,
                            dump_only_on_failure,
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

    def _run_chunks_in_process_pool(
        self,
        chunks: list[SRTChunk],
        frames_by_chunk: dict[int, list[CandidateFrame]],
        video_path: str,
        temp_frames: Path,
        raw_dump_path: Path | None = None,
        dump_only_on_failure: bool = True,
    ) -> list[_ChunkPipelineResult]:
        """Run every chunk in its own process, bounded by ``concurrency``.

        Uses the ``spawn`` context for consistent behaviour across macOS /
        Linux and to avoid forking a process that already has threads (the
        heuristic extractor may have just used ``asyncio.to_thread``).
        """
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor, as_completed

        jobs = [
            (
                chunk,
                frames_by_chunk.get(chunk.index, []),
                str(Path(video_path).resolve()),
                Path(temp_frames).resolve(),
                self.frame_match_tolerance,
                raw_dump_path,
                dump_only_on_failure,
            )
            for chunk in chunks
        ]
        workers = max(1, min(self.concurrency, len(jobs)))
        pool = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
        )
        future_to_index = {
            pool.submit(_process_chunk_worker, job): index
            for index, job in enumerate(jobs)
        }
        results: list[_ChunkPipelineResult | None] = [None] * len(jobs)
        try:
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                results[index] = future.result()
        except BaseException:
            # A fatal config/auth error must stop the whole run. Cancel
            # queued jobs immediately instead of letting every chunk hit
            # the same broken endpoint.
            for future in future_to_index:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        merged: list[_ChunkPipelineResult] = []
        for result in results:
            if result is None:
                raise RuntimeError("多进程 chunk 结果不完整")
            merged.append(result)
            _merge_chunk_reporter(result)
        return merged
