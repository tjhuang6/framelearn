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
    def capture_single_frame(
        video_path: str,
        timestamp: float,
        output_path: str,
    ) -> bool:
        """Capture a single frame at the given timestamp.

        Used by AgentKeyframeSelector to capture frames on demand.

        Args:
            video_path: Path to input video
            timestamp: Timestamp in seconds
            output_path: Path to save the captured frame (.jpg)

        Returns:
            True if successful, False otherwise
        """
        h = int(timestamp // 3600)
        m = int((timestamp % 3600) // 60)
        s = timestamp % 60
        ts_str = f"{h:02d}:{m:02d}:{s:06.3f}"

        try:
            subprocess.run(
                ["ffmpeg", "-ss", ts_str, "-i", video_path,
                 "-vframes", "1", "-vf", "scale=1280:-1",
                 "-q:v", "2", "-y", output_path],
                check=True, capture_output=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False
        video_path: str,
        output_dir: str,
        scene_threshold: float = 0.3,
        fallback_interval: int = 30,
        max_frames: int = 100,
    ) -> list[tuple[Path, float]]:
        """Extract keyframes using scene detection + fallback timing.

        Args:
            video_path: Path to input video
            output_dir: Directory to save frames
            scene_threshold: Scene change threshold (0.0-1.0, lower = more sensitive)
            fallback_interval: Seconds between fallback frames
            max_frames: Maximum number of frames to extract

        Returns:
            List of (frame_path, timestamp_seconds) tuples
        """
        import re
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        scene_dir = output_dir / "scene"
        fallback_dir = output_dir / "fallback"
        scene_dir.mkdir(exist_ok=True)
        fallback_dir.mkdir(exist_ok=True)

        # Scene detection with showinfo to capture timestamps
        scene_frames = []
        try:
            result = subprocess.run(
                ["ffmpeg", "-i", video_path,
                 "-vf", f"select='gt(scene,{scene_threshold})',showinfo,scale=1280:-1",
                 "-vsync", "vfr", "-q:v", "2", "-y",
                 str(scene_dir / "frame_%04d.jpg")],
                capture_output=True, text=True,
            )
            # Parse showinfo output for pts_time
            for line in result.stderr.splitlines():
                match = re.search(r'pts_time:([\d.]+)', line)
                if match:
                    timestamp = float(match.group(1))
                    scene_frames.append(timestamp)
        except subprocess.CalledProcessError:
            pass

        # Fallback timing frames (known timestamps)
        fallback_frames = []
        try:
            # Get video duration first
            duration_result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True, check=True,
            )
            duration = float(duration_result.stdout.strip())

            timestamp = 0.0
            while timestamp < duration:
                fallback_frames.append(timestamp)
                timestamp += fallback_interval

            # Extract fallback frames at those timestamps
            for i, ts in enumerate(fallback_frames, 1):
                subprocess.run(
                    ["ffmpeg", "-ss", str(ts), "-i", video_path,
                     "-vframes", "1", "-vf", "scale=1280:-1",
                     "-q:v", "2", "-y",
                     str(fallback_dir / f"fallback_{i:04d}.jpg")],
                    capture_output=True,
                )
        except (subprocess.CalledProcessError, ValueError):
            pass

        # Merge and rename with timestamps
        all_frames_with_time = []

        # Scene frames
        scene_files = sorted(scene_dir.glob("*.jpg"))
        for i, frame_file in enumerate(scene_files):
            if i < len(scene_frames):
                ts = scene_frames[i]
                h = int(ts // 3600)
                m = int((ts % 3600) // 60)
                s = int(ts % 60)
                new_name = output_dir / f"frame_{h:02d}h{m:02d}m{s:02d}s.jpg"
                frame_file.rename(new_name)
                all_frames_with_time.append((new_name, ts))

        # Fallback frames
        fallback_files = sorted(fallback_dir.glob("*.jpg"))
        for i, frame_file in enumerate(fallback_files):
            if i < len(fallback_frames):
                ts = fallback_frames[i]
                h = int(ts // 3600)
                m = int((ts % 3600) // 60)
                s = int(ts % 60)
                new_name = output_dir / f"frame_{h:02d}h{m:02d}m{s:02d}s.jpg"
                frame_file.rename(new_name)
                all_frames_with_time.append((new_name, ts))

        # Sort by timestamp and limit
        all_frames_with_time.sort(key=lambda x: x[1])
        return all_frames_with_time[:max_frames]
