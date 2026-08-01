"""FFmpeg wrapper for audio extraction and keyframe extraction."""

import shutil
import subprocess
from pathlib import Path


class FFmpegHelper:
    """Wrapper for FFmpeg operations."""

    @staticmethod
    def check_installed() -> bool:
        """Check if ffmpeg is available in PATH."""
        return shutil.which("ffmpeg") is not None

    @staticmethod
    def has_audio_stream(video_path: str) -> bool:
        """Check if video file has an audio stream."""
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "default=nw=1",
             video_path],
            capture_output=True, text=True,
        )
        return "audio" in result.stdout

    @staticmethod
    def find_companion_audio(video_path: str) -> Path | None:
        """Find a companion audio file (mp3/m4a/aac) in the same directory.

        Bilibili downloads often split video and audio into separate files.
        This looks for an audio file with a matching stem prefix.
        """
        video = Path(video_path)
        parent = video.parent
        stem = video.stem

        # Try exact stem match with audio extensions
        for ext in (".mp3", ".m4a", ".aac", ".wav"):
            candidate = parent / f"{stem}{ext}"
            if candidate.exists():
                return candidate

        # Try stem with different quality suffix (e.g., -30080 vs -30280)
        # Strip trailing quality code: "name-30080" → "name"
        base_stem = stem.rsplit("-", 1)[0] if "-" in stem else stem
        for f in parent.iterdir():
            if f.suffix in (".mp3", ".m4a", ".aac") and f.stem.startswith(base_stem):
                return f

        return None

    @staticmethod
    def extract_audio(video_path: str, output_path: str) -> bool:
        """Extract audio track to m4a format.

        If the video has no audio stream, looks for a companion audio file
        in the same directory (common with Bilibili split downloads).

        Args:
            video_path: Path to input video
            output_path: Path to output audio file (.m4a)

        Returns:
            True if successful, False otherwise
        """
        # Check if video has audio stream
        if not FFmpegHelper.has_audio_stream(video_path):
            companion = FFmpegHelper.find_companion_audio(video_path)
            if companion:
                print(f"📎 使用伴随音频文件：{companion.name}")
                # Convert companion audio to m4a at 16kHz
                try:
                    subprocess.run(
                        ["ffmpeg", "-i", str(companion),
                         "-acodec", "aac", "-ar", "16000", "-y", output_path],
                        check=True, capture_output=True, text=True,
                    )
                    return True
                except subprocess.CalledProcessError:
                    return False
            else:
                print("❌ 视频无音轨，且未找到伴随音频文件")
                return False

        try:
            subprocess.run(
                ["ffmpeg", "-i", video_path,
                 "-vn", "-acodec", "aac", "-ar", "16000", "-y", output_path],
                check=True, capture_output=True, text=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    @staticmethod
    def extract_keyframes(
        video_path: str,
        output_dir: str,
        scene_threshold: float = 0.3,
        fallback_interval: int = 30,
        max_frames: int = 100,
    ) -> list[Path]:
        """Extract keyframes using scene detection + fallback timing.

        Args:
            video_path: Path to input video
            output_dir: Directory to save frames
            scene_threshold: Scene change threshold (0.0-1.0, lower = more sensitive)
            fallback_interval: Seconds between fallback frames
            max_frames: Maximum number of frames to extract

        Returns:
            List of paths to extracted frames
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        scene_dir = output_dir / "scene"
        fallback_dir = output_dir / "fallback"
        scene_dir.mkdir(exist_ok=True)
        fallback_dir.mkdir(exist_ok=True)

        # Scene detection frames
        try:
            subprocess.run(
                ["ffmpeg", "-i", video_path,
                 "-vf", f"select='gt(scene,{scene_threshold})',scale=1280:-1",
                 "-vsync", "vfr", "-q:v", "2", "-y",
                 str(scene_dir / "frame_%04d.jpg")],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError:
            pass

        # Fallback timing frames
        try:
            subprocess.run(
                ["ffmpeg", "-i", video_path,
                 "-vf", f"fps=1/{fallback_interval},scale=1280:-1",
                 "-q:v", "2", "-y",
                 str(fallback_dir / "fallback_%04d.jpg")],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError:
            pass

        all_frames = sorted(scene_dir.glob("*.jpg")) + sorted(fallback_dir.glob("*.jpg"))
        all_frames.sort()
        return all_frames[:max_frames]
