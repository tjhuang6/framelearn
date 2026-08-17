"""Regression tests for two production failures:

1. Keyframe cache never validated: manifests were saved WITH a
   heuristic-frames digest folded into the cache key, but the
   pre-extraction validation in VideoPipeline passes no digest — the
   two sides computed keys under different rules and never matched.
2. One anchor exceeding the retake budget aborted the whole run
   ("锚点 ... 超过 retake 上限，拒绝使用未验证帧"). The archived design
   requires conservative retention instead.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from framelearn.pipeline.cache_manifest import (
    CacheManifest,
    ConfigSnapshot,
    InputFileInfo,
    create_manifest,
)
import framelearn.pipeline.vision_frame_evaluator as vfe_module
from framelearn.pipeline.vision_frame_evaluator import (
    AnchorFrame,
    VisionFrameEvaluator,
)


# ── 1. Cache manifest symmetry ─────────────────────────────────────


def _fake_input_file(tmp_path: Path, name: str, content: bytes) -> InputFileInfo:
    path = tmp_path / name
    path.write_bytes(content)
    return InputFileInfo.from_path(path)


def _config_get(key, default=None):
    return {
        "heuristic.scene_threshold": 0.4,
        "heuristic.similarity_threshold": 0.7,
        "heuristic.max_frames": 25,
    }.get(key, default)


def test_saved_manifest_with_digest_validates_without_caller_digest(tmp_path):
    """The production bug: save folds a frames digest into the key, the
    pre-extraction check has none — validation must still succeed when
    input/config/subtitle are unchanged."""
    video_info = _fake_input_file(tmp_path, "video.mp4", b"v")
    subtitle_info = _fake_input_file(tmp_path, "subtitle.srt", b"s")

    saved = CacheManifest(
        input_file=video_info,
        subtitle_file=subtitle_info,
        config=ConfigSnapshot.from_config(
            _config_get, "keyframe", "n/a", "n/a"
        ),
        heuristic_frames_digest="d0e117aba6aa6b65",
    )
    saved.cache_key = saved.compute_cache_key()

    ok = saved.validate(
        video_path=tmp_path / "video.mp4",
        subtitle_path=tmp_path / "subtitle.srt",
        config_get_fn=_config_get,
        mode="keyframe",
        asr_provider="n/a",
        asr_model="n/a",
        # no digest on the caller side — the VideoPipeline case
        heuristic_frames_digest="",
    )
    assert ok is True


def test_validation_still_detects_config_changes(tmp_path):
    video_info = _fake_input_file(tmp_path, "video.mp4", b"v")
    saved = CacheManifest(
        input_file=video_info,
        config=ConfigSnapshot.from_config(
            _config_get, "keyframe", "n/a", "n/a"
        ),
        heuristic_frames_digest="abc",
    )
    saved.cache_key = saved.compute_cache_key()

    def other_config(key, default=None):
        return {
            "heuristic.scene_threshold": 0.2,  # changed
            "heuristic.similarity_threshold": 0.7,
            "heuristic.max_frames": 25,
        }.get(key, default)

    assert (
        saved.validate(
            video_path=tmp_path / "video.mp4",
            subtitle_path=None,
            config_get_fn=other_config,
            mode="keyframe",
            asr_provider="n/a",
            asr_model="n/a",
        )
        is False
    )


def test_validation_still_detects_input_changes(tmp_path):
    video_info = _fake_input_file(tmp_path, "video.mp4", b"v1")
    saved = CacheManifest(
        input_file=video_info,
        config=ConfigSnapshot.from_config(
            _config_get, "keyframe", "n/a", "n/a"
        ),
        heuristic_frames_digest="abc",
    )
    saved.cache_key = saved.compute_cache_key()

    (tmp_path / "video.mp4").write_bytes(b"v2")  # changed input
    assert (
        saved.validate(
            video_path=tmp_path / "video.mp4",
            subtitle_path=None,
            config_get_fn=_config_get,
            mode="keyframe",
            asr_provider="n/a",
            asr_model="n/a",
        )
        is False
    )


def test_manifest_without_digest_round_trips(tmp_path):
    """Subtitle-mode manifests have no digest on either side."""
    video_info = _fake_input_file(tmp_path, "video.mp4", b"v")
    saved = CacheManifest(
        input_file=video_info,
        config=ConfigSnapshot.from_config(
            _config_get, "subtitle", "dashscope", "model-x"
        ),
    )
    saved.cache_key = saved.compute_cache_key()

    assert (
        saved.validate(
            video_path=tmp_path / "video.mp4",
            subtitle_path=None,
            config_get_fn=_config_get,
            mode="subtitle",
            asr_provider="dashscope",
            asr_model="model-x",
        )
        is True
    )


# ── 2. Retake limit → conservative retention ───────────────────────


class _StubEvaluator(VisionFrameEvaluator):
    """Returns scripted decisions without calling any LLM."""

    def __init__(self, decisions_per_round, max_retakes=1):
        # Bypass provider config loading entirely.
        self.max_retakes = max_retakes
        self.max_retries = 0
        self.max_tokens = 1024
        self.batch_size = 8
        self._decisions = list(decisions_per_round)

    async def _evaluate_batch(
        self, items, raw_dump_path=None, dump_only_on_failure=True
    ):
        decisions = self._decisions.pop(0)
        out = []
        for item, (retake, retake_ts) in zip(items, decisions):
            out.append(
                vfe_module.FrameEvaluation(
                    anchor_id=item.anchor_id,
                    srt_id=item.srt_id,
                    frame_path=item.frame_path,
                    timestamp=item.timestamp,
                    keep_image=not retake,
                    content_type="other",
                    caption="",
                    text_representation="",
                    reason="scripted",
                    retake=retake,
                    retake_timestamp=retake_ts,
                )
            )
        return out


def _item(anchor_id="c5_a1", ts=1525.3):
    return AnchorFrame(
        anchor_id=anchor_id,
        srt_id=1,
        frame_path="/tmp/frame.jpg",
        timestamp=ts,
        subtitle_text="卷积",
    )


def test_retake_budget_exhausted_keeps_frame_instead_of_failing(
    tmp_path, monkeypatch
):
    """Exactly the production failure: max_retakes=1, the model asks for
    a retake twice in a row → must keep the frame, not raise."""
    monkeypatch.setattr(
        vfe_module.FFmpegHelper,
        "capture_single_frame",
        staticmethod(lambda *_a, **_k: True),
    )
    evaluator = _StubEvaluator(
        decisions_per_round=[
            [(True, 1526.0)],   # round 1: retake (budget 1 → 0)
            [(True, 1527.0)],   # round 2: retake again → budget 0
        ],
        max_retakes=1,
    )
    result = asyncio.run(evaluator.evaluate([_item()], "video.mp4", tmp_path))
    assert len(result) == 1
    assert result[0].retake is False
    assert result[0].keep_image is True


def test_retake_then_accept_flows_through(tmp_path, monkeypatch):
    """Normal path: first retake accepted, second evaluation keeps."""
    monkeypatch.setattr(
        vfe_module.FFmpegHelper,
        "capture_single_frame",
        staticmethod(lambda *_a, **_k: True),
    )
    evaluator = _StubEvaluator(
        decisions_per_round=[
            [(True, 1526.0)],
            [(False, None)],
        ],
        max_retakes=1,
    )
    result = asyncio.run(evaluator.evaluate([_item()], "video.mp4", tmp_path))
    assert result[0].keep_image is True
    assert result[0].retake is False


def test_immediate_retake_with_zero_budget_keeps_frame(tmp_path):
    """max_retakes=0 and the very first decision is a retake."""
    evaluator = _StubEvaluator(
        decisions_per_round=[[(True, 1526.0)]],
        max_retakes=0,
    )
    result = asyncio.run(evaluator.evaluate([_item()], "video.mp4", tmp_path))
    assert result[0].keep_image is True
    assert result[0].retake is False
