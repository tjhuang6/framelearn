"""Main video processing pipeline."""

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from framelearn.config import get as config_get
from framelearn.pipeline.asr_adapter import ASRAdapter
from framelearn.pipeline.cache_manifest import create_manifest, CacheManifest
from framelearn.pipeline.doc_generator import DocumentGenerator
from framelearn.pipeline.ffmpeg_helper import FFmpegHelper
from framelearn.pipeline.keyframe_dedup import KeyframeDeduplicator
from framelearn.pipeline.run_report import RunReporter, get_reporter, set_reporter, reset_reporter
from framelearn.pipeline.subtitle_cleaner import SubtitleCleaner

if TYPE_CHECKING:
    from framelearn.privacy_tracker import PrivacyTracker


@dataclass
class PipelineResult:
    """Result of video processing pipeline."""
    output_dir: Path
    markdown_path: Path
    keyframes: list[Path]
    subtitle_text: str
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


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
        
        print(f"📹 开始处理视频：{self.video_path.name}")

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
                markdown_path=Path(),
                keyframes=[],
                subtitle_text="",
                error="FFmpeg 未安装，请先安装：brew install ffmpeg",
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
                raw_subtitle = self.subtitle_path.read_text(encoding="utf-8")
                # Strip SRT/VTT formatting if needed — just keep plain text
                if self.subtitle_path.suffix in (".srt", ".vtt"):
                    raw_subtitle = SubtitleCleaner.strip_timestamps(raw_subtitle)
                transcript = TranscriptResult(
                    segments=[],
                    full_text=raw_subtitle,
                    has_timestamps=self.subtitle_path.suffix in (".srt", ".vtt"),
                    srt=self.subtitle_path.read_text(encoding="utf-8") if self.subtitle_path.suffix == ".srt" else None,
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
                    transcript = TranscriptResult(
                        segments=[],
                        full_text=cached_txt.read_text(encoding="utf-8"),
                        has_timestamps=True,
                        srt=cached_srt.read_text(encoding="utf-8"),
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
            if transcript.has_timestamps and transcript.srt:
                srt_path = src_dir / "subtitle.srt"
                srt_path.write_text(transcript.srt, encoding="utf-8")
                print(f"✅ 字幕文件：{srt_path}")

            # Step 4: Extract keyframes
            print("🖼️  提取关键帧...")

            # Check for cached keyframes with manifest validation
            cached_frames = sorted(src_dir.glob("frame_*.jpg"))
            keyframe_manifest_path = src_dir / "keyframe_manifest.json"
            
            use_keyframe_cache = False
            if cached_frames and keyframe_manifest_path.exists():
                kf_manifest = CacheManifest.load(keyframe_manifest_path)
                if kf_manifest:
                    use_keyframe_cache = kf_manifest.validate(
                        video_path=self.video_path,
                        subtitle_path=self.subtitle_path,
                        config_get_fn=config_get,
                        mode="keyframe",
                        asr_provider="n/a",
                        asr_model="n/a",
                    )
                    if not use_keyframe_cache:
                        print("⚠️  关键帧缓存失效（输入或配置已变更）")
                        get_reporter().record_fallback(
                            "video_pipeline.keyframe_cache",
                            "关键帧缓存失效（输入或配置已变更），将重新提取",
                        )
            
            if use_keyframe_cache and cached_frames:
                print(f"⏭️  使用已缓存的 {len(cached_frames)} 个关键帧...")
                get_reporter().record_cache_hit(
                    "video_pipeline.keyframe_cache",
                    f"命中关键帧缓存（{len(cached_frames)} 帧），跳过提取与去重",
                )
                final_frames = cached_frames
                final_frames_with_time = []
                for frame_path in cached_frames:
                    # Parse timestamp from filename with new format:
                    # frame_00h01m30s250ms_scene_001.jpg or frame_00h01m30s250ms_interval_001.jpg
                    name = frame_path.stem  # "frame_00h01m30s250ms_scene_001"
                    parts = name.split("_", 1)
                    if len(parts) < 2:
                        continue
                    time_part = parts[1].split("_")[0]  # "00h01m30s250ms"
                    # Extract h, m, s, ms
                    time_part = time_part.replace("ms", "")
                    h_part, rest = time_part.split("h")
                    m_part, rest = rest.split("m")
                    s_part = rest.split("s")[0]
                    ms_part = rest.split("s")[1] if "s" in rest and rest.split("s")[1] else "0"
                    h, m, s, ms = int(h_part), int(m_part), int(s_part), int(ms_part)
                    timestamp = h * 3600 + m * 60 + s + ms / 1000.0
                    final_frames_with_time.append((frame_path, timestamp))
            else:
                frames_dir = temp_dir / "frames"
                raw_frames = FFmpegHelper.extract_keyframes(
                    str(self.video_path),
                    str(frames_dir),
                    scene_threshold=config_get("video.scene_threshold", 0.3),
                    fallback_interval=config_get("video.fallback_interval", 30),
                    max_frames=config_get("video.max_keyframes", 100) * 2,  # Extract more, deduplicate later
                )

                # Step 5: Deduplicate keyframes
                print("🔍 关键帧去重...")
                dedup = KeyframeDeduplicator(similarity_threshold=0.9)
                unique_frames = dedup.deduplicate(
                    raw_frames,
                    max_frames=config_get("video.max_keyframes", 100),
                )

                # Copy to output (keep timestamp in filename)
                final_frames = []
                final_frames_with_time = []
                for frame_path, timestamp in unique_frames:
                    dest = src_dir / frame_path.name
                    shutil.copy(frame_path, dest)
                    final_frames.append(dest)
                    final_frames_with_time.append((dest, timestamp))
                
                # Create keyframe manifest
                keyframe_manifest = create_manifest(
                    video_path=self.video_path,
                    subtitle_path=self.subtitle_path,
                    config_get_fn=config_get,
                    mode="keyframe",
                    asr_provider="n/a",
                    asr_model="n/a",
                )
                keyframe_manifest.save(src_dir / "keyframe_manifest.json")
                print(f"✅ 关键帧 manifest 已保存")

            print(f"✅ 保留 {len(final_frames)} 个关键帧")

            # Step 5.5: Agent keyframe selection (optional)
            if config_get("agent.keyframe_selection", False):
                print("🤖 Agent 关键帧选择...")
                from framelearn.pipeline.agent_keyframe_selector import AgentKeyframeSelector
                
                # Track vision API usage
                vision_mode = config_get("vision.vision_mode", "appserver")
                if vision_mode == "api":
                    vision_provider = config_get("vision.vision_provider", "unknown")
                    vision_model = config_get("vision.vision_model", "unknown")
                    tracker.add_service(
                        "vision_api_keyframe",
                        f"Vision API 关键帧分析 ({vision_provider}/{vision_model})"
                    )
                
                selector = AgentKeyframeSelector()
                final_frames_with_time = selector.select(
                    video_path=str(self.video_path),
                    segments=transcript.segments if transcript else [],
                    output_dir=src_dir,
                    existing_keyframes=final_frames_with_time,
                )
                final_frames = [p for p, _ in final_frames_with_time]
                print(f"✅ Agent 选择后：{len(final_frames)} 个关键帧")

            # Step 6: Generate documents
            generator = DocumentGenerator()
            doc_mode = config_get("doc_generation.mode", "visual_script")

            # Track document generation API usage
            from framelearn.provider_adapter import load_text_config
            try:
                text_config = load_text_config()
                tracker.add_service(
                    "text_api_docgen",
                    f"Text API 文档生成 ({text_config.provider}/{text_config.model})"
                )
            except Exception:
                tracker.add_service("text_api_docgen", "Text API 文档生成")

            # Pass ASR info to generator for manifest
            asr_provider = "unknown"
            asr_model = "unknown"
            if not self.subtitle_path:
                try:
                    asr = ASRAdapter()
                    asr_provider = asr.provider
                    asr_model = asr.model
                except Exception:
                    pass

            # Collect SRT text for precise time-based segmentation
            srt_content = None
            if self.subtitle_path and self.subtitle_path.suffix == ".srt":
                srt_content = self.subtitle_path.read_text(encoding="utf-8")
            elif transcript.has_timestamps and transcript.srt:
                srt_content = transcript.srt

            print("📝 生成课堂笔记...")
            try:
                notes_md = generator.generate(
                    keyframes=final_frames_with_time,
                    subtitle=cleaned_subtitle,
                    video_title=self.video_path.stem,
                    mode="notes",
                    srt_text=srt_content,
                    output_dir=self.output_dir,
                    video_path=self.video_path,
                    subtitle_path=self.subtitle_path,
                    asr_provider=asr_provider,
                    asr_model=asr_model,
                )
            except Exception as e:
                return self._error_result(f"笔记生成失败：{e}")

            print(f"📖 生成{doc_mode}版...")
            try:
                main_md = generator.generate(
                    keyframes=final_frames_with_time,
                    subtitle=cleaned_subtitle,
                    video_title=self.video_path.stem,
                    mode=doc_mode,
                    srt_text=srt_content,
                    output_dir=self.output_dir,
                    video_path=self.video_path,
                    subtitle_path=self.subtitle_path,
                    asr_provider=asr_provider,
                    asr_model=asr_model,
                )
            except Exception as e:
                return self._error_result(f"文档生成失败：{e}")

            # Save both versions
            notes_path = self.output_dir / "notes.md"
            notes_path.write_text(notes_md, encoding="utf-8")

            main_path = self.output_dir / "index.md"
            main_path.write_text(main_md, encoding="utf-8")

            print(f"✅ 讲稿已生成：{main_path}")
            print(f"✅ 笔记已生成：{notes_path}")

            return PipelineResult(
                output_dir=self.output_dir,
                markdown_path=main_path,
                keyframes=final_frames,
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
            markdown_path=Path(),
            keyframes=[],
            subtitle_text="",
            error=error_msg,
        )
