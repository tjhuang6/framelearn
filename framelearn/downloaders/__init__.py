"""Online video downloaders for FrameLearn."""

from framelearn.downloaders.online import DownloadedVideo, download_video
from framelearn.downloaders.url_parser import (
    detect_platform,
    extract_url,
    extract_video_id,
    is_supported_video_url,
)

__all__ = [
    "DownloadedVideo",
    "detect_platform",
    "download_video",
    "extract_url",
    "extract_video_id",
    "is_supported_video_url",
]
