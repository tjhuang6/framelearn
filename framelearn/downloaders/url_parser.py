"""URL recognition / normalisation helpers for online video sources.

The platform rules are intentionally the same as the BiliNote project:
YouTube, Bilibili (including ``b23.tv`` short links), Douyin (including
``v.douyin.com`` share links embedded in share text) and Kuaishou
(including ``v.kuaishou.com`` share links).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", re.IGNORECASE)

# Host suffix → canonical platform name.  ``www`` and ``m`` prefixes are
# stripped before matching, so m.youtube.com and music.youtube.com both map
# to ``youtube``.
_PLATFORM_HOST_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("youtube.com", "youtu.be"), "youtube"),
    (("bilibili.com", "b23.tv"), "bilibili"),
    (("douyin.com", "iesdouyin.com"), "douyin"),
    (("kuaishou.com", "gifshow.com", "chenzhongtech.com"), "kuaishou"),
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def extract_url(text: str) -> str | None:
    """Return the first HTTP(S) URL found in free-form share text.

    Douyin/Kuaishou share messages are commonly ``<title> <url> <hint>``;
    the command parser usually already extracted the URL, but traditional
    ``run <share text>`` invocations can contain the whole message.
    """
    text = (text or "").strip()
    match = URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;:!?)]}，。；：！？、】》")


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def detect_platform(url: str) -> str | None:
    """Detect the platform for a video URL.

    The check is deliberately host-based (like BiliNote's
    ``is_supported_video_url``), so it does not make a network request.
    """
    host = _hostname(url)
    if not host:
        return None
    # Bilibili's b23.tv short domain is sometimes shown without www.
    if host == "b23.tv":
        return "bilibili"
    # Short share domains.
    if host.endswith(".douyin.com") or host == "douyin.com":
        return "douyin"
    if host.endswith(".kuaishou.com") or host == "kuaishou.com":
        return "kuaishou"

    for suffixes, platform in _PLATFORM_HOST_RULES:
        for suffix in suffixes:
            if host == suffix or host.endswith("." + suffix):
                return platform
    return None


def is_supported_video_url(url: str) -> bool:
    """Return True when FrameLearn has a downloader for this URL."""
    return detect_platform(url) is not None


def extract_video_id(url: str, platform: str) -> str | None:
    """Extract a platform-specific video ID when it is visible in the URL.

    Mirrors BiliNote's ``app.utils.url_parser.extract_video_id``.
    """
    url = url or ""
    if platform == "bilibili":
        match = re.search(r"BV([0-9A-Za-z]+)", url)
        return f"BV{match.group(1)}" if match else None

    if platform == "youtube":
        match = re.search(
            r"(?:v=|youtu\.be/|shorts/|live/|embed/)([0-9A-Za-z_-]{11})", url
        )
        return match.group(1) if match else None

    if platform == "douyin":
        for pattern in (
            r"/video/(\d+)",
            r"modal_id=(\d+)",
            r"aweme_id=(\d+)",
            r"/share/video/(\d+)",
        ):
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    if platform == "kuaishou":
        match = re.search(r"short-video/(\w+)", url)
        if match:
            return match.group(1)
        match = re.search(r"photoId=(\w+)", url)
        if match:
            return match.group(1)
        match = re.search(r"photoId%3D(\w+)", url)
        if match:
            return match.group(1)
        return None

    return None


def resolve_redirect(
    url: str,
    headers: dict | None = None,
    timeout: float = 20.0,
    proxy: str | None = None,
) -> str:
    """Follow HTTP redirects and return the final URL.

    GET is used instead of HEAD because several short-link services reject
    HEAD or return a different landing page.
    """
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    client_kwargs = {"proxies": proxy} if proxy else {}
    with httpx.Client(
        headers=merged,
        follow_redirects=True,
        timeout=timeout,
        **client_kwargs,
    ) as client:
        response = client.get(url)
        return str(response.url)


def canonical_douyin_url(url: str, proxy: str | None = None) -> str:
    """Return a ``www.douyin.com/video/<id>`` URL suitable for yt-dlp."""
    video_id = extract_video_id(url, "douyin")
    if video_id:
        return f"https://www.douyin.com/video/{video_id}"

    resolved = resolve_redirect(url, proxy=proxy)
    video_id = extract_video_id(resolved, "douyin")
    if not video_id:
        raise ValueError(f"无法从抖音链接中解析视频 ID：{url}")
    return f"https://www.douyin.com/video/{video_id}"
