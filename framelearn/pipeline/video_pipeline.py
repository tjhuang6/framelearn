"""Main video processing pipeline."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


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
        self.output_dir = Path(output_dir) if output_dir else None

        if not self.video_path.exists():
            raise FileNotFoundError(f"视频文件不存在：{video_path}")

    def run(self) -> PipelineResult:
        """Execute the full pipeline."""
        # TODO: implement in Task #29
        raise NotImplementedError("Pipeline not yet implemented")
