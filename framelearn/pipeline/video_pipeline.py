"""Main video processing pipeline."""

import asyncio
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from framelearn.config import get as config_get
from framelearn.pipeline.asr_adapter import ASRAdapter, TranscriptSegment
from framelearn.pipeline.cache_manifest import create_manifest, CacheManifest
from framelearn.pipeline.ffmpeg_helper import FFmpegHelper
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.run_report import RunReporter, get_reporter, set_reporter, reset_reporter
from framelearn.pipeline.srt_parser import parse_srt_segments, parse_subtitle_file
from framelearn.pipeline.subtitle_cleaner import SubtitleCleaner

if TYPE_CHECKING:
    from framelearn.privacy_tracker import PrivacyTracker


_TIMESTAMP_RE = re.compile(
    r"(?P<h>\d+)h(?P<m>\d+)m(?P<s>\d+)s(?P<ms>\d+)?ms?"
)


def _parse_frame_timestamp(frame_path: Path) -> float:
    """Extract the embedded timestamp from a frame's filename.

    Heuristic extractor writes filenames like
    ``frame_00h01m30s250ms_scene_001.jpg``; Stage1's new frames use
    ``extra_frame_000.jpg`` (no timestamp embedded). For the latter we
    fall back to 0.0 — they're associated with SRT segments by the
    assembler anyway.
    """
    name = frame_path.stem
    match = _TIMESTAMP_RE.search(name)
    if not match:
        return 0.0
    h = int(match.group("h"))
    m = int(match.group("m"))
    s = int(match.group("s"))
    ms = int(match.group("ms") or 0)
    return h * 3600 + m * 60 + s + ms / 1000.0


def _synthesize_timed_segments(
    text: str, duration_sec: float
) -> list[TranscriptSegment]:
    """Create timestamped segments for transcript text without timing data.

    Splits by line and distributes the known media duration across the
    lines proportionally to their character count. This is a best-effort
    fallback for SiliconFlow ASR and plain ``.txt`` subtitle inputs; SRT /
    DashScope inputs should always provide real segments.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    if duration_sec <= 0:
        duration_sec = max(4.5, len(lines) * 4.5)

    weights = [max(1.0, float(len(line))) for line in lines]
    total_weight = sum(weights)
    starts: list[float] = [0.0]
    for weight in weights[:-1]:
        starts.append(starts[-1] + duration_sec * weight / total_weight)

    segments: list[TranscriptSegment] = []
    for i, (line, start) in enumerate(zip(lines, starts)):
        end = starts[i + 1] if i + 1 < len(starts) else duration_sec
        segments.append(
            TranscriptSegment(text=line, start=start, end=max(start + 0.1, end))
        )
    return segments


@dataclass
class PipelineResult:
    """Result of video processing pipeline."""
    output_dir: Path
    srt_picture_path: Path
    blog_path: Path
    keyframes: list[Path]
    subtitle_text: str
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def markdown_path(self) -> Path:
        """Backward-compat alias for the blog output.

        Older callers (e.g. ``router._run_pipeline``) printed
        ``result.markdown_path``; keep that working but prefer the new
        ``srt_picture_path`` / ``blog_path`` attributes.
        """
        return self.blog_path


class VideoPipeline:
    """Orchestrates video → audio → ASR → keyframes → document generation."""

    def __init__(self, video_path: str, output_dir: Optional[str] = None, subtitle_path: Optional[str] = None):
        self.video_path = Path(video_path)

        if not self.video_path.exists():
            raise FileNotFoundError(f"视频文件不存在：{video_path}")

        # Output directory: config or specified
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            base_output = Path(config_get("video.output_dir", "./output"))
            video_title = self.video_path.stem
            self.output_dir = base_output / video_title

        self.keep_temp = config_get("video.keep_temp_files", False)
        self.subtitle_path = Path(subtitle_path) if subtitle_path else None

    def run(self) -> PipelineResult:
        """Execute the full pipeline."""
        from framelearn.privacy_tracker import PrivacyTracker, set_tracker, reset_tracker
        from framelearn.config import get as config_get

        # Initialize privacy tracker
        privacy_hints_enabled = config_get("privacy.privacy_hints", False)
        tracker = PrivacyTracker(enabled=privacy_hints_enabled)
        set_tracker(tracker)

        # Initialize run reporter — collects every fault-tolerance event
        # (failed segments, fallbacks, skipped frames, cache hits) so the
        # degradation is visible in PipelineResult.warnings and in
        # <output_dir>/run-report.json, instead of only ever hitting stdout.
        reporter = RunReporter(video_name=self.video_path.name)
        set_reporter(reporter)

        try:
            result = self._run_internal(tracker)
            result.warnings = reporter.get_warnings()
            status = "error" if result.error else "success"
            reporter.write_report(
                self.output_dir / "run-report.json",
                status=status,
                error=result.error,
            )
            return result
        finally:
            # Show privacy summary at the end
            tracker.show_summary()
            reset_tracker()
            reset_reporter()

    def _run_internal(self, tracker: 'PrivacyTracker') -> PipelineResult:
        """Internal run method with privacy tracking."""
        print(f"📹 开始处理视频：{self.video_path.name}")

        # Step 0: Check FFmpeg
        if not FFmpegHelper.check_installed():
            return PipelineResult(
                output_dir=self.output_dir,
                srt_picture_path=Path(),
                blog_path=Path(),
                keyframes=[],
                subtitle_text="",
                error="FFmpeg/FFprobe 未安装，请先安装：brew install ffmpeg",
            )

        # Create output directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        src_dir = self.output_dir / "src"
        src_dir.mkdir(exist_ok=True)

        temp_dir = Path(tempfile.mkdtemp(prefix="framelearn_"))

        try:
            # Step 1 & 2: Transcribe audio (or load existing subtitle)
            print("🎤 语音识别中...")
            if self.subtitle_path is not None:
                print(f"⏭️  使用已有字幕：{self.subtitle_path}")
                from framelearn.pipeline.asr_adapter import TranscriptResult

                segments, full_text = parse_subtitle_file(self.subtitle_path)
                is_timed = self.subtitle_path.suffix.lower() in (".srt", ".vtt")
                transcript = TranscriptResult(
                    segments=segments,
                    full_text=full_text,
                    has_timestamps=is_timed and bool(segments),
                    srt=(
                        self.subtitle_path.read_text(encoding="utf-8")
                        if self.subtitle_path.suffix.lower() == ".srt"
                        else None
                    ),
                )
            else:
                # Check for cached subtitle with manifest validation
                cached_srt = src_dir / "subtitle.srt"
                cached_txt = src_dir / "subtitle.txt"
                manifest_path = src_dir / "subtitle_manifest.json"
                
                # Load and validate manifest
                use_cache = False
                if cached_srt.exists() and cached_txt.exists() and manifest_path.exists():
                    manifest = CacheManifest.load(manifest_path)
                    if manifest:
                        # Validate against current video file and config
                        # For subtitle, we only care about video file and ASR config
                        asr = ASRAdapter()
                        use_cache = manifest.validate(
                            video_path=self.video_path,
                            subtitle_path=self.subtitle_path,
                            config_get_fn=config_get,
                            mode="subtitle",
                            asr_provider=asr.provider,
                            asr_model=asr.model,
                        )
                        if not use_cache:
                            print("⚠️  字幕缓存失效（输入或配置已变更）")
                            get_reporter().record_fallback(
                                "video_pipeline.subtitle_cache",
                                "字幕缓存失效（输入或配置已变更），将重新转录",
                            )
                    else:
                        print("⚠️  字幕 manifest 损坏")
                        get_reporter().record_fallback(
                            "video_pipeline.subtitle_cache",
                            "字幕 manifest 损坏，将重新转录",
                        )
                
                if use_cache:
                    print("⏭️  使用已缓存字幕...")
                    get_reporter().record_cache_hit(
                        "video_pipeline.subtitle_cache",
                        "命中字幕缓存，跳过 ASR",
                    )
                    from framelearn.pipeline.asr_adapter import TranscriptResult
                    cached_srt_text = cached_srt.read_text(encoding="utf-8")
                    transcript = TranscriptResult(
                        segments=parse_srt_segments(cached_srt_text),
                        full_text=cached_txt.read_text(encoding="utf-8"),
                        has_timestamps=True,
                        srt=cached_srt_text,
                    )
                else:
                    # Extract audio first (only needed for ASR)
                    print("🎵 提取音轨...")
                    audio_path = temp_dir / "audio.m4a"
                    if not FFmpegHelper.extract_audio(str(self.video_path), str(audio_path)):
                        return self._error_result("音轨提取失败")

                    try:
                        asr = ASRAdapter()  # reads provider from settings.toml
                        
                        # Track ASR usage
                        if asr.provider == "dashscope":
                            tracker.add_service(
                                "oss_upload",
                                "阿里云 OSS（临时音频切片，任务完成后删除）"
                            )
                            tracker.add_service(
                                "asr_dashscope",
                                f"阿里云 DashScope ASR ({asr.model})"
                            )
                        elif asr.provider == "siliconflow":
                            tracker.add_service(
                                "asr_siliconflow",
                                f"硅基流动 SenseVoice ({asr.model})"
                            )
                        
                        transcript = asr.transcribe(str(audio_path), output_dir=self.output_dir)
                        
                        # Create subtitle manifest after successful ASR
                        subtitle_manifest = create_manifest(
                            video_path=self.video_path,
                            subtitle_path=None,
                            config_get_fn=config_get,
                            mode="subtitle",
                            asr_provider=asr.provider,
                            asr_model=asr.model,
                        )
                        subtitle_manifest.save(src_dir / "subtitle_manifest.json")
                        print(f"✅ 字幕 manifest 已保存")
                    except Exception as e:
                        return self._error_result(f"语音识别失败：{e}")

            # Step 3: Clean subtitle
            print("✨ 清洗字幕...")
            cleaner = SubtitleCleaner()
            cleaned_subtitle = cleaner.clean(transcript.full_text)

            # Save subtitle text
            subtitle_path = src_dir / "subtitle.txt"
            subtitle_path.write_text(cleaned_subtitle, encoding="utf-8")

            # Save SRT if available (dashscope has timestamps)
            srt_path: Path | None = None
            if transcript.has_timestamps and transcript.srt:
                srt_path = src_dir / "subtitle.srt"
                srt_path.write_text(transcript.srt, encoding="utf-8")
                print(f"✅ 字幕文件：{srt_path}")

            # Step 4: Build pre-extracted frame list from cache (optional).
            #
            # With the chunked pipeline, the heuristic extractor and
            # Stage1's new-frame capture both run inside
            # ChunkedDocGenerator. We just check the keyframe cache here
            # so a re-run with unchanged inputs can skip ffmpeg.
            print("🖼️  检查关键帧缓存...")

            cached_frames = sorted(src_dir.glob("*.jpg"))
            keyframe_manifest_path = src_dir / "keyframe_manifest.json"

            pre_extracted_frames: list | None = None
            if cached_frames and keyframe_manifest_path.exists():
                kf_manifest = CacheManifest.load(keyframe_manifest_path)
                # Build CandidateFrame list anyway; the validator decides
                # whether the cache is fresh enough to trust it.
                candidate_list: list = []
                for frame_path in cached_frames:
                    timestamp = _parse_frame_timestamp(frame_path)
                    candidate_list.append(
                        CandidateFrame(
                            path=str(frame_path),
                            timestamp_sec=timestamp,
                            source="heuristic",
                        )
                    )
                if kf_manifest and kf_manifest.validate(
                    video_path=self.video_path,
                    subtitle_path=self.subtitle_path,
                    config_get_fn=config_get,
                    mode="keyframe",
                    asr_provider="n/a",
                    asr_model="n/a",
                ):
                    print(f"⏭️  使用已缓存的 {len(cached_frames)} 个关键帧...")
                    get_reporter().record_cache_hit(
                        "video_pipeline.keyframe_cache",
                        f"命中关键帧缓存（{len(cached_frames)} 帧），跳过启发式截帧",
                    )
                    pre_extracted_frames = candidate_list
                else:
                    print("⚠️  关键帧缓存失效（输入或配置已变更）")
                    get_reporter().record_fallback(
                        "video_pipeline.keyframe_cache",
                        "关键帧缓存失效（输入或配置已变更），将重新提取",
                    )
                    pre_extracted_frames = None

            # Step 4-6: Hand off to the chunked doc generator.
            #
            # The new flow combines heuristic keyframe extraction + text
            # cleaning + two vision-model stages inside one orchestrator.
            # We no longer call the per-segment agent_keyframe_selector or
            # the legacy document_generator — see openspec change
            # ``chunked-llm-doc-gen`` for the full rationale.
            print("🚀 启动分块文档生成（启发式 + 视觉两阶段）...")

            # Track which LLM services we'll touch.
            from framelearn.provider_adapter import load_text_config, load_vision_config
            try:
                text_cfg = load_text_config()
                tracker.add_service(
                    "text_api_docgen",
                    f"Text API 文档生成 ({text_cfg.provider}/{text_cfg.model})"
                )
            except Exception:
                tracker.add_service("text_api_docgen", "Text API 文档生成")
            try:
                vision_cfg = load_vision_config()
                tracker.add_service(
                    "vision_api_docgen",
                    f"Vision API 文档生成 ({vision_cfg.provider}/{vision_cfg.model})"
                )
            except Exception:
                tracker.add_service("vision_api_docgen", "Vision API 文档生成")

            # Pick the segment list for ChunkedDocGenerator. We prefer the
            # ASR transcript's segments (they have precise start/end times
            # for chunking). If ASR didn't produce timestamped segments
            # (e.g. siliconflow backend or a plain .txt subtitle), synthesize
            # timestamped segments proportional to the media duration so the
            # chunker still has something to group.
            segments_for_pipeline: list[TranscriptSegment] = [
                seg for seg in transcript.segments if seg.start is not None
            ]
            if not segments_for_pipeline and cleaned_subtitle:
                duration_sec = FFmpegHelper.get_duration(str(self.video_path))
                segments_for_pipeline = _synthesize_timed_segments(
                    cleaned_subtitle, duration_sec
                )
                if segments_for_pipeline:
                    get_reporter().record_fallback(
                        "video_pipeline.synthetic_segments",
                        "字幕缺少时间戳，已按媒体时长合成近似分段",
                        detail={"segment_count": len(segments_for_pipeline)},
                    )

            from framelearn.pipeline.chunked_doc_generator import (
                ChunkedDocGenerator,
            )

            try:
                doc_gen = ChunkedDocGenerator()
                doc_result = asyncio.run(
                    doc_gen.generate(
                        video_path=str(self.video_path),
                        srt_segments=segments_for_pipeline,
                        output_dir=self.output_dir,
                        video_title=self.video_path.stem,
                        pre_extracted_frames=pre_extracted_frames,
                    )
                )
            except Exception as e:
                return self._error_result(f"分块文档生成失败：{e}")

            # Persist a fresh keyframe manifest so the next run can
            # short-circuit the heuristic extractor. The digest covers
            # whatever frames ChunkedDocGenerator just produced/copied
            # into src/.
            produced_frames = sorted(src_dir.glob("*.jpg"))
            produced_candidates = [
                CandidateFrame(
                    path=str(p),
                    timestamp_sec=_parse_frame_timestamp(p),
                    source="heuristic",
                )
                for p in produced_frames
            ]
            new_kf_manifest = create_manifest(
                video_path=self.video_path,
                subtitle_path=self.subtitle_path,
                config_get_fn=config_get,
                mode="keyframe",
                asr_provider="n/a",
                asr_model="n/a",
                heuristic_frames=produced_candidates,
            )
            new_kf_manifest.save(keyframe_manifest_path)
            print(f"✅ 关键帧 manifest 已保存（{len(produced_candidates)} 帧摘要）")

            print(f"✅ SRT 版讲义：{doc_result.srt_picture_path}")
            print(f"✅ 博客版讲义：{doc_result.blog_path}")

            return PipelineResult(
                output_dir=self.output_dir,
                srt_picture_path=doc_result.srt_picture_path,
                blog_path=doc_result.blog_path,
                keyframes=produced_frames,
                subtitle_text=cleaned_subtitle,
                error=None,
            )

        except Exception as e:
            return self._error_result(f"未知错误：{e}")

        finally:
            # Cleanup temp files
            if not self.keep_temp:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _error_result(self, error_msg: str) -> PipelineResult:
        return PipelineResult(
            output_dir=self.output_dir,
            srt_picture_path=Path(),
            blog_path=Path(),
            keyframes=[],
            subtitle_text="",
            error=error_msg,
        )
