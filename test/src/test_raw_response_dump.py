"""Tests for raw-response post-mortem dump.

When a chunk exhausts its retry budget (or a vision-batch decision
fails), the program now writes every LLM attempt to
``output/temp/raw_responses.jsonl`` and surfaces the path in the
reporter fallback detail. These tests verify both stages.
"""

import asyncio
import json

import pytest

import framelearn.pipeline.blog_generator as blog_module
import framelearn.pipeline.vision_frame_evaluator as vfe_module
from framelearn.errors import GenerationError
from framelearn.pipeline.blog_generator import BlogGenerator
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.pipeline.vision_frame_evaluator import (
    AnchorFrame,
    VisionFrameEvaluator,
)
from test.src.test_blog_anchor_pipeline import _chunk  # noqa: F401


def _stub_text_config(monkeypatch, max_calls):
    from framelearn.provider_adapter import ProviderConfig

    monkeypatch.setattr(
        blog_module,
        "config_get",
        lambda key, default=None: {
            "blog_gen.max_calls": max_calls,
            "blog_gen.max_tokens": 16384,
        }.get(key, default),
    )
    monkeypatch.setattr(
        blog_module,
        "load_text_config",
        lambda: ProviderConfig(
            provider="openai",
            api_key="sk-test",
            model="unknown-model",
            base_url="https://example.invalid/v1",
        ),
    )


def _stub_vision_config(monkeypatch):
    from framelearn.provider_adapter import ProviderConfig

    monkeypatch.setattr(
        vfe_module,
        "config_get",
        lambda key, default=None: {
            "blog_gen.max_retakes": 0,
            "blog_gen.vision_max_calls": 2,
            "blog_gen.vision_max_tokens": 8192,
            "blog_gen.vision_batch_size": 8,
        }.get(key, default),
    )
    monkeypatch.setattr(
        vfe_module,
        "load_vision_config",
        lambda: ProviderConfig(
            provider="openai",
            api_key="sk-test",
            model="unknown-model",
            base_url="https://example.invalid/v1",
        ),
    )


@pytest.fixture
def fresh_reporter():
    """Swap the global reporter for a private one and restore after."""
    from framelearn.pipeline.run_report import (
        RunReporter,
        get_reporter,
        set_reporter,
    )

    saved = get_reporter()
    fresh = RunReporter(video_name="")
    set_reporter(fresh)
    try:
        yield fresh
    finally:
        set_reporter(saved)


def test_blog_generator_failure_writes_raw_dump_with_attempts(
    monkeypatch, tmp_path, fresh_reporter
):
    """All attempts land in the dump; fallback detail has the path."""
    # max_calls=3 -> 1 initial + 2 retries = 3 attempts total.
    _stub_text_config(monkeypatch, max_calls=3)

    async def bad_llm(*args, **kwargs):
        return "这不是 JSON"

    monkeypatch.setattr(blog_module, "call_llm_async", bad_llm)

    dump = tmp_path / "raw_responses.jsonl"

    with pytest.raises(GenerationError):
        asyncio.run(BlogGenerator().generate(_chunk(), [], raw_dump_path=dump))

    assert dump.exists(), "raw_responses.jsonl must exist after failure"
    lines = [
        json.loads(line) for line in dump.read_text(encoding="utf-8").splitlines()
    ]
    # 3 attempts -> 3 schema_mismatch entries + 1 final exhausted entry.
    assert len(lines) == 4
    assert [e["attempt"] for e in lines] == [0, 1, 2, 2]
    assert all(e["parse_error"] in {"schema_mismatch", "exhausted"} for e in lines)
    assert all(e["chunk_index"] == 0 for e in lines)
    assert all(e["response"] == "这不是 JSON" for e in lines)

    fallback = _last_fallback(
        fresh_reporter, "blog_generator.generation_failed"
    )
    assert fallback["detail"]["raw_dump_path"] == str(dump)
    assert fallback["detail"]["attempts"] == 3


def test_blog_generator_success_dumps_only_when_audit_enabled(
    monkeypatch, tmp_path
):
    """Successful runs only land in the dump when audit mode is on.

    Covers the three combinations the user actually controls via
    settings.toml:

    - dump_only_on_failure=True  (default)  -> no entry on success
    - dump_only_on_failure=False            -> one entry on success
    - dump path omitted                     -> no file is written
    """
    _stub_text_config(monkeypatch, max_calls=1)

    valid = (
        '{"blog_markdown": "正文 [[FRAME:a1@53.0]]", '
        '"frame_requests": []}'
    )

    async def good_llm(*args, **kwargs):
        return valid

    monkeypatch.setattr(blog_module, "call_llm_async", good_llm)

    dump = tmp_path / "raw_responses.jsonl"

    # Default: success path skips the dump.
    asyncio.run(BlogGenerator().generate(_chunk(), [], raw_dump_path=dump))
    if dump.exists():
        lines = [
            json.loads(line)
            for line in dump.read_text(encoding="utf-8").splitlines()
        ]
        assert lines == [], "success path must not produce dump entries"

    # Audit on: success path writes one entry.
    asyncio.run(
        BlogGenerator().generate(
            _chunk(),
            [],
            raw_dump_path=dump,
            dump_only_on_failure=False,
        )
    )
    lines = [
        json.loads(line) for line in dump.read_text(encoding="utf-8").splitlines()
    ]
    assert len(lines) == 1
    assert lines[0]["attempt"] == 0
    assert lines[0]["parse_error"] is None
    assert lines[0]["response"] == valid


def test_blog_generator_no_dump_path_does_not_crash(monkeypatch):
    """raw_dump_path is optional — existing callers must still work."""
    _stub_text_config(monkeypatch, max_calls=1)

    async def bad_llm(*args, **kwargs):
        return "garbage"

    monkeypatch.setattr(blog_module, "call_llm_async", bad_llm)
    with pytest.raises(GenerationError):
        asyncio.run(BlogGenerator().generate(_chunk(), []))


def test_vision_evaluator_failure_writes_raw_dump(
    monkeypatch, tmp_path, fresh_reporter
):
    """When the vision model can't produce valid JSON, dump captures it."""
    _stub_vision_config(monkeypatch)

    async def bad_vision(*args, **kwargs):
        return "这不是 JSON"

    monkeypatch.setattr(
        vfe_module, "call_llm_async_interleaved", bad_vision
    )

    dump = tmp_path / "vision_dump.jsonl"

    items = [
        AnchorFrame(
            anchor_id="c5_a1",
            srt_id=1,
            frame_path="/tmp/fake.jpg",
            timestamp=1525.3,
            subtitle_text="x",
        )
    ]
    with pytest.raises(GenerationError):
        asyncio.run(
            VisionFrameEvaluator().evaluate(
                items, "video.mp4", tmp_path, raw_dump_path=dump
            )
        )

    lines = [
        json.loads(line) for line in dump.read_text(encoding="utf-8").splitlines()
    ]
    # 2 attempts -> 2 schema_mismatch entries + 1 final exhausted entry.
    assert len(lines) == 3
    assert all("c5_a1" in line["anchor_ids"] for line in lines)
    fallback = _last_fallback(
        fresh_reporter, "vision_frame_evaluator.generation_failed"
    )
    assert fallback["detail"]["raw_dump_path"] == str(dump)


# ── helpers ────────────────────────────────────────────────────────


def _last_fallback(reporter, stage):
    matches = [e for e in reporter.fallbacks if e.stage == stage]
    assert matches, f"no fallback recorded for stage={stage}"
    last = matches[-1]
    return {
        "message": last.message,
        "detail": last.detail,
    }