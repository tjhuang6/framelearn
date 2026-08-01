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
    def extract_audio(video_path: str, output_path: str) -> bool:
        """Extract audio track to m4a format.

        Args:
            video_path: Path to input video
            output_path: Path to output audio file (.m4a)

        Returns:
            True if successful, False otherwise
        """
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-i", video_path,
                    "-vn",  # no video
                    "-acodec", "aac",
                    "-ar", "16000",  # 16kHz sample rate
                    "-y",  # overwrite
                    output_path,
                ],
                check=True,
                capture_output=True,
                text=True,
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
                [
                    "ffmpeg",
                    "-i", video_path,
                    "-vf", f"select='gt(scene,{scene_threshold})',scale=1280:-1",
                    "-vsync", "vfr",
                    "-q:v", "2",
                    "-y",
                    str(scene_dir / "frame_%04d.jpg"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            pass  # If scene detection fails, rely on fallback

        # Fallback timing frames
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-i", video_path,
                    "-vf", f"fps=1/{fallback_interval},scale=1280:-1",
                    "-q:v", "2",
                    "-y",
                    str(fallback_dir / "fallback_%04d.jpg"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            pass

        # Collect all frames
        all_frames = sorted(scene_dir.glob("*.jpg")) + sorted(fallback_dir.glob("*.jpg"))

        # Sort by timestamp (embedded in filename or file creation time)
        all_frames.sort()

        # Limit to max_frames
        return all_frames[:max_frames]
