"""Tests for the blog-anchor-pipeline modules."""

from pathlib import Path

from framelearn.pipeline.asr_adapter import TranscriptSegment
from framelearn.pipeline.blog_generator import (
    BLOG_GENERATOR_PROMPT,
    _parse_blog_output,
    _parse_blog_output_lenient,
    build_annotated_srt,
)
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.md_assembler import MDAssembler
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.pipeline.vision_frame_evaluator import (
    AnchorFrame,
    FrameEvaluation,
    _parse_evaluations,
    _parse_evaluations_lenient,
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


def test_parse_blog_output_lenient_repairs_timestamp_mismatch():
    raw = (
        '{"blog_markdown": "正文 [[FRAME:a1@53.0]]", '
        '"frame_requests": [{"anchor_id": "a1", "srt_id": 1, '
        '"timestamp": 53.5, "request_type": "new_capture", '
        '"source_frame_path": null, "reason": "x"}]}'
    )
    out = _parse_blog_output_lenient(raw, _chunk(), [])
    assert out is not None
    assert out.frame_requests[0].timestamp == 53.0


def test_parse_blog_output_lenient_accepts_missing_frame_requests_field():
    raw = '{"blog_markdown": "没有任何锚点的完整讲稿"}'
    out = _parse_blog_output_lenient(raw, _chunk(), [])
    assert out is not None
    assert out.frame_requests == []


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


def test_parse_evaluations_lenient_fills_missing_decisions_with_none():
    first = AnchorFrame(
        anchor_id="a1",
        srt_id=1,
        frame_path="src/a.jpg",
        timestamp=53.0,
        subtitle_text="字幕一",
    )
    second = AnchorFrame(
        anchor_id="a2",
        srt_id=2,
        frame_path="src/b.jpg",
        timestamp=54.0,
        subtitle_text="字幕二",
    )
    raw = (
        '{"decisions": [{"anchor_id": "a1", "frame": "src/a.jpg", '
        '"retake": false, "retake_timestamp": null, "keep_image": true, '
        '"content_type": "diagram", "caption": "", '
        '"text_representation": "", "reason": "ok"}]}'
    )
    parsed = _parse_evaluations_lenient(raw, [first, second])
    assert parsed is not None
    assert parsed[0] is not None and parsed[0].anchor_id == "a1"
    assert parsed[1] is None


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


def test_parse_evaluations_accepts_anchor_id_without_global_prefix():
    item = AnchorFrame(
        anchor_id="c1_a1",
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
    assert parsed[0].anchor_id == "c1_a1"
    assert parsed[0].caption == "说明"


def test_blog_prompt_is_faithful_transcript_not_summary():
    """Prompt must ask for polished transcript, not a condensed blog."""
    prompt = BLOG_GENERATOR_PROMPT
    assert "像在看原视频" in prompt
    assert "只润色，不总结" in prompt
    assert "保留老师" in prompt
    assert "第三人称" in prompt
    assert "宁长勿短" in prompt
    assert "不要重排、合并、提炼或压缩" in prompt


def test_blog_generator_raises_instead_of_degrading(monkeypatch):
    import asyncio

    import pytest

    import framelearn.pipeline.blog_generator as module
    from framelearn.errors import GenerationError
    from framelearn.provider_adapter import ProviderConfig

    monkeypatch.setattr(
        module,
        "load_text_config",
        lambda: ProviderConfig(
            provider="openai",
            api_key="sk-test",
            model="unknown-model",
            base_url="https://example.invalid/v1",
        ),
    )

    async def bad_response(*args, **kwargs):
        return "这不是 JSON"

    monkeypatch.setattr(module, "call_llm_async", bad_response)

    with pytest.raises(GenerationError):
        asyncio.run(module.BlogGenerator(max_retries=0).generate(_chunk(), []))


def test_blog_generator_max_calls_is_total_attempts(monkeypatch):
    import framelearn.pipeline.blog_generator as module
    from framelearn.provider_adapter import ProviderConfig

    monkeypatch.setattr(
        module,
        "config_get",
        lambda key, default=None: {
            "blog_gen.max_calls": 5,
            "blog_gen.max_tokens": 16384,
        }.get(key, default),
    )
    monkeypatch.setattr(
        module,
        "load_text_config",
        lambda: ProviderConfig(
            provider="openai",
            api_key="sk-test",
            model="unknown-model",
            base_url="https://example.invalid/v1",
        ),
    )

    generator = module.BlogGenerator()
    assert generator.max_retries == 4
    assert generator.max_retries + 1 == 5


def test_vision_evaluator_max_calls_is_total_attempts(monkeypatch):
    import framelearn.pipeline.vision_frame_evaluator as module
    from framelearn.provider_adapter import ProviderConfig

    monkeypatch.setattr(
        module,
        "config_get",
        lambda key, default=None: {
            "blog_gen.vision_max_calls": 6,
            "blog_gen.vision_max_tokens": 8192,
            "blog_gen.vision_batch_size": 8,
            "blog_gen.max_retakes": 1,
        }.get(key, default),
    )
    monkeypatch.setattr(
        module,
        "load_vision_config",
        lambda: ProviderConfig(
            provider="openai",
            api_key="sk-test",
            model="unknown-model",
            base_url="https://example.invalid/v1",
        ),
    )

    evaluator = module.VisionFrameEvaluator()
    assert evaluator.max_retries == 5
    assert evaluator.max_retries + 1 == 6
