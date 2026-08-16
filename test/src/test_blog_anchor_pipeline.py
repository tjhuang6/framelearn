"""Tests for the blog-anchor-pipeline modules."""

from pathlib import Path

from framelearn.pipeline.asr_adapter import TranscriptSegment
from framelearn.pipeline.blog_generator import (
    BLOG_GENERATOR_PROMPT,
    _parse_blog_output,
    build_annotated_srt,
)
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.md_assembler import MDAssembler
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.pipeline.vision_frame_evaluator import (
    AnchorFrame,
    FrameEvaluation,
    _parse_evaluations,
)


def _chunk():
    return SRTChunk(
        index=0,
        start_sec=0.0,
        end_sec=10.0,
        segments=[
            TranscriptSegment(text="第一条字幕", start=0.0, end=5.0),
            TranscriptSegment(text="第二条字幕", start=5.0, end=10.0),
        ],
    )


def test_build_annotated_srt_inserts_markers_after_nearest_segment():
    frames = [
        CandidateFrame(path="src/a.jpg", timestamp_sec=2.0, source="heuristic"),
        CandidateFrame(path="src/b.jpg", timestamp_sec=7.0, source="heuristic"),
    ]
    text = build_annotated_srt(_chunk(), frames)
    assert "第一条字幕" in text
    assert "第二条字幕" in text
    assert "src/a.jpg" in text
    assert "src/b.jpg" in text
    assert text.index("src/a.jpg") < text.index("第二条字幕")
    assert text.index("src/b.jpg") > text.index("第二条字幕")


def test_parse_blog_output_valid_reuse():
    frame = CandidateFrame(path="src/a.jpg", timestamp_sec=53.0, source="heuristic")
    raw = (
        '{"blog_markdown": "正文 [[FRAME:a1@53.0]]", '
        '"frame_requests": [{"anchor_id": "a1", "srt_id": 1, '
        '"timestamp": 53.0, "request_type": "reuse", '
        '"source_frame_path": "src/a.jpg", "reason": "x"}]}'
    )
    out = _parse_blog_output(raw, _chunk(), [frame])
    assert out is not None
    assert out.frame_requests[0].request_type == "reuse"


def test_parse_blog_output_rejects_unknown_reuse_path():
    raw = (
        '{"blog_markdown": "正文 [[FRAME:a1@53.0]]", '
        '"frame_requests": [{"anchor_id": "a1", "srt_id": 1, '
        '"timestamp": 53.0, "request_type": "reuse", '
        '"source_frame_path": "missing.jpg", "reason": "x"}]}'
    )
    assert _parse_blog_output(raw, _chunk(), []) is None


def test_parse_blog_output_requires_marker_request_consistency():
    raw = (
        '{"blog_markdown": "正文 [[FRAME:a1@53.0]]", '
        '"frame_requests": [{"anchor_id": "a1", "srt_id": 1, '
        '"timestamp": 53.5, "request_type": "new_capture", '
        '"source_frame_path": null, "reason": "x"}]}'
    )
    assert _parse_blog_output(raw, _chunk(), []) is None


def test_parse_evaluations_accepts_keep_fields():
    item = AnchorFrame(
        anchor_id="a1",
        srt_id=1,
        frame_path="src/a.jpg",
        timestamp=53.0,
        subtitle_text="字幕",
    )
    raw = (
        '{"decisions": [{"anchor_id": "a1", "frame": "src/a.jpg", '
        '"retake": false, "retake_timestamp": null, "keep_image": true, '
        '"content_type": "diagram", "caption": "说明", '
        '"text_representation": "", "reason": "ok"}]}'
    )
    parsed = _parse_evaluations(raw, [item])
    assert parsed is not None
    assert parsed[0].keep_image is True
    assert parsed[0].caption == "说明"


def test_parse_evaluations_rejects_string_false():
    item = AnchorFrame(
        anchor_id="a1",
        srt_id=1,
        frame_path="src/a.jpg",
        timestamp=53.0,
        subtitle_text="字幕",
    )
    raw = (
        '{"decisions": [{"anchor_id": "a1", "frame": "src/a.jpg", '
        '"retake": false, "retake_timestamp": null, "keep_image": "false", '
        '"content_type": "diagram", "caption": "", '
        '"text_representation": "", "reason": "x"}]}'
    )
    parsed = _parse_evaluations(raw, [item])
    assert parsed is not None
    assert parsed[0].keep_image is False


def test_md_assembler_replaces_kept_anchor_and_removes_discarded(tmp_path):
    assembler = MDAssembler()
    kept = FrameEvaluation(
        anchor_id="a1",
        srt_id=1,
        frame_path=str(tmp_path / "src" / "a.jpg"),
        timestamp=53.0,
        keep_image=True,
        content_type="diagram",
        caption="说明",
        text_representation="",
        reason="ok",
    )
    discarded = FrameEvaluation(
        anchor_id="a2",
        srt_id=2,
        frame_path=str(tmp_path / "src" / "b.jpg"),
        timestamp=54.0,
        keep_image=False,
        content_type="transition",
        caption="",
        text_representation="",
        reason="bad",
    )
    blog = assembler.assemble_blog_anchored(
        ["正文 [[FRAME:a1@53.0]] 和 [[FRAME:a2@54.0]]"],
        {"a1": kept, "a2": discarded},
        video_title="测试",
    )
    assert "src/a.jpg" in blog
    assert "说明" in blog
    assert "FRAME" not in blog


def test_blog_prompt_is_faithful_transcript_not_summary():
    """Prompt must ask for polished transcript, not a condensed blog."""
    prompt = BLOG_GENERATOR_PROMPT
    assert "像在看原视频" in prompt
    assert "只润色，不总结" in prompt
    assert "保留老师" in prompt
    assert "第三人称" in prompt
    assert "宁长勿短" in prompt
    assert "不要重排、合并、提炼或压缩" in prompt
