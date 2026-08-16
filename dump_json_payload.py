"""Dump the exact JSON body sent to the vision API for the first chunk.

Bypasses framelearn.config (tomllib needs 3.11+) by re-implementing
the small SRT-formatting helper and calling _build_srt_md_segments
directly.
"""
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from framelearn.pipeline.vision_stage1 import (
    STAGE1_PROMPT,
    _build_srt_md_segments,
    _format_picture_index,
)
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.provider_adapter import _build_openai_request_interleaved, ProviderConfig


# Real subtitle segments from output/分类任务_30min_2/src/subtitle.srt
SUBTITLE_SEGMENTS = [
    (0.0, 22.5, "宽呢224，所以这张图片就是3×224×224啊，channel H和W。"),
    (22.5, 27.0, "我们在卷积的时候，刚才讲了我们的卷积核，它不是啊，它不是只有一层，它是有个3层的数数字矩阵的。"),
    (27.0, 31.5, "因为它是一张图片嘛，所以它是有3层的。"),
    (31.5, 35.5, "那它在在它卷积的时候，它就不是9个数在相乘，它是多少个数在相乘？"),
    (35.5, 39.0, "是27个数在相乘，对吧？"),
    (39.0, 45.0, "这个数和这个数去相乘，这个数和这个数去相乘，这个数和这个数去相乘，这个和这个对吧？"),
    (45.0, 51.0, "这就是相乘，然后这个数又好哪个？"),
    (51.0, 56.0, "第3层这个数要和这个值去相乘。"),
]


class FakeSeg:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


SRC_DIR = Path("/Users/iwill/Documents/PythonProjects/FrameLearn/output/分类任务_30min_2/src")
jpg_files = sorted(SRC_DIR.glob("*.jpg"))[:3]
import re
def _ts_from_name(name):
    m = re.search(r"(\d+)h(\d+)m(\d+)s(\d+)ms", name)
    if not m:
        return 0.0
    h, mm, s, ms = map(int, m.groups())
    return h * 3600 + mm * 60 + s + ms / 1000.0

frames = [
    CandidateFrame(path=str(p), timestamp_sec=_ts_from_name(p.name), source="heuristic")
    for p in jpg_files
]
segs = [FakeSeg(*t) for t in SUBTITLE_SEGMENTS]

body = _build_srt_md_segments(segs, frames)
pic_index = _format_picture_index(frames)
instruction_text = STAGE1_PROMPT.format(chunk_text=pic_index, max_images=50)
all_segments = [{"type": "text", "text": instruction_text}, *body]

config = ProviderConfig(
    provider="siliconflow",
    base_url="https://api.siliconflow.cn/v1",
    api_key="sk-placeholder",
    model="Qwen/Qwen3-VL-8B-Instruct",
)
url, headers, body_json = _build_openai_request_interleaved(config, all_segments, max_tokens=8192)
print("URL:", url)
print("HEADERS:", json.dumps(headers, indent=2))
print("BODY:")
print(json.dumps(body_json, indent=2, ensure_ascii=False))
