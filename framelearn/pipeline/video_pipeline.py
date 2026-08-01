"""Main video processing pipeline."""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from framelearn.config import get as config_get
from framelearn.pipeline.asr_adapter import ASRAdapter
from framelearn.pipeline.doc_generator import DocumentGenerator
from framelearn.pipeline.ffmpeg_helper import FFmpegHelper
from framelearn.pipeline.keyframe_dedup import KeyframeDeduplicator
from framelearn.pipeline.subtitle_cleaner import SubtitleCleaner


@dataclass
class PipelineResult:
    """Result of video processing pipeline."""
    output_dir: Path
    markdown_path: Path
    keyframes: list[Path]
    subtitle_text: str
    error: Optional[str] = None


class VideoPipeline:
    """Orchestrates video → audio → ASR → keyframes → document generation."""

    def __init__(self, video_path: str, output_dir: Optional[str] = None):
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

    def run(self) -> PipelineResult:
        """Execute the full pipeline."""
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
            # Step 1: Extract audio
            print("🎵 提取音轨...")
            audio_path = temp_dir / "audio.m4a"
            if not FFmpegHelper.extract_audio(str(self.video_path), str(audio_path)):
                return self._error_result("音轨提取失败")

            # Step 2: Transcribe audio
            print("🎤 语音识别中...")
            try:
                asr = ASRAdapter(provider="siliconflow")
                transcript = asr.transcribe(str(audio_path))
            except Exception as e:
                return self._error_result(f"语音识别失败：{e}")

            # Step 3: Clean subtitle
            print("✨ 清洗字幕...")
            cleaner = SubtitleCleaner()
            cleaned_subtitle = cleaner.clean(transcript.full_text)

            # Save subtitle
            subtitle_path = src_dir / "subtitle.txt"
            subtitle_path.write_text(cleaned_subtitle, encoding="utf-8")

            # Step 4: Extract keyframes
            print("🖼️  提取关键帧...")
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

            # Copy to output
            final_frames = []
            for i, frame in enumerate(unique_frames, 1):
                dest = src_dir / f"frame_{i:03d}.jpg"
                shutil.copy(frame, dest)
                final_frames.append(dest)

            print(f"✅ 保留 {len(final_frames)} 个关键帧")

            # Step 6: Generate document
            print("📝 生成教材...")
            generator = DocumentGenerator()
            try:
                markdown = generator.generate(
                    keyframes=final_frames,
                    subtitle=cleaned_subtitle,
                    video_title=self.video_path.stem,
                )
            except Exception as e:
                return self._error_result(f"文档生成失败：{e}")

            # Save markdown
            md_path = self.output_dir / "index.md"
            md_path.write_text(markdown, encoding="utf-8")

            print(f"✅ 教材已生成：{md_path}")

            return PipelineResult(
                output_dir=self.output_dir,
                markdown_path=md_path,
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
