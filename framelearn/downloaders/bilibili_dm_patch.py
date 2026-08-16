"""
Patch yt-dlp's Bilibili extractor to inject the dm_img_* / web_location
risk-control parameters required by Bilibili's wbi/playurl gateway.

Ported from the BiliNote project:
``backend/app/downloaders/bilibili_dm_patch.py``.

Background
----------
Bilibili's ``x/player/wbi/playurl`` gateway may reject requests that omit
the browser fingerprint params ``dm_img_list`` / ``dm_img_str`` /
``dm_cover_img_str`` / ``dm_img_inter`` + ``web_location`` with HTTP 412.
The patch injects dummy-but-well-formed values *before* wbi signing.
"""

from __future__ import annotations

import base64
import logging
import random
import string

logger = logging.getLogger(__name__)


def build_dm_img_params() -> dict:
    """Return dummy ``dm_img_*`` / ``web_location`` params the gateway expects."""
    return {
        "web_location": 1550101,
        "dm_img_list": "[]",
        "dm_img_str": base64.b64encode(
            "".join(random.choices(string.printable, k=random.randint(16, 64))).encode()
        )[:-2].decode(),
        "dm_cover_img_str": base64.b64encode(
            "".join(random.choices(string.printable, k=random.randint(32, 128))).encode()
        )[:-2].decode(),
        "dm_img_inter": '{"ds":[],"wh":[6093,6631,31],"of":[430,760,380]}',
    }


def apply_bilibili_dm_img_patch() -> bool:
    """Monkey-patch ``BilibiliBaseIE._download_playinfo``.

    Idempotent and defensive: returns ``True`` if the patch is in place,
    ``False`` if yt-dlp's internals could not be patched.  Never raises —
    downloaders remain functional without the patch.
    """
    try:
        from yt_dlp.extractor.bilibili import BilibiliBaseIE
    except Exception as e:  # noqa: BLE001 - defensive against any yt-dlp layout change
        logger.warning("Bilibili dm_img patch skipped, cannot import extractor: %s", e)
        return False

    original = BilibiliBaseIE._download_playinfo
    if getattr(original, "_bili_dm_patched", False):
        return True

    def _patched_download_playinfo(
        self, bvid, cid, headers=None, query=None, *args, **kwargs
    ):
        # Newer yt-dlp versions pass additional keyword arguments (fatal,
        # ...) that must be forwarded untouched.
        merged_query = {**build_dm_img_params(), **(query or {})}
        call_kwargs = dict(kwargs)
        call_kwargs["headers"] = headers
        call_kwargs["query"] = merged_query
        return original(self, bvid, cid, *args, **call_kwargs)

    _patched_download_playinfo._bili_dm_patched = True
    BilibiliBaseIE._download_playinfo = _patched_download_playinfo
    logger.info("Applied Bilibili wbi/playurl dm_img patch to yt-dlp BilibiliBaseIE")
    return True
