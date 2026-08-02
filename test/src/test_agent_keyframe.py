"""Unit tests for AgentKeyframeSelector."""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from framelearn.pipeline.agent_keyframe_selector import AgentKeyframeSelector
from framelearn.pipeline.asr_adapter import TranscriptSegment


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def make_segment(text: str, start: float, end: float) -> TranscriptSegment:
    return TranscriptSegment(text=text, start=start, end=end)


def make_selector() -> AgentKeyframeSelector:
    sel = AgentKeyframeSelector.__new__(AgentKeyframeSelector)
    sel.vision_mode = "appserver"
    sel.vision_provider = "deepseek"
    sel.vision_model = "deepseek-reasoner"
    return sel


# ------------------------------------------------------------------
# Heuristic pre-filter
# ------------------------------------------------------------------

class TestHeuristicFilter:
    def setup_method(self):
        self.sel = make_selector()

    def test_detects_visual_keywords(self):
        assert self.sel._heuristic_needs_frame("看这张图") is True
        assert self.sel._heuristic_needs_frame("如图所示") is True
        assert self.sel._heuristic_needs_frame("看一下代码") is True
        assert self.sel._heuristic_needs_frame("这里的屏幕") is True
        assert self.sel._heuristic_needs_frame("PPT第三页") is True

    def test_skips_narration_only(self):
        assert self.sel._heuristic_needs_frame("今天我们来学习") is False
        assert self.sel._heuristic_needs_frame("大家好，欢迎来到本节课") is False
        assert self.sel._heuristic_needs_frame("好的，让我们继续") is False


# ------------------------------------------------------------------
# LLM decision mocking
# ------------------------------------------------------------------

class TestLLMDecision:
    def setup_method(self):
        self.sel = make_selector()

    def test_decide_need_frame_true(self):
        seg = make_segment("如图所示，这是架构图", 10.0, 15.0)
        self.sel._call_text_llm = Mock(
            return_value=json.dumps({"need_frame": True, "reason": "提到图片"})
        )
        decision = self.sel._decide(seg)
        assert decision.need_frame is True
        assert decision.timestamp == 10.0

    def test_decide_need_frame_false(self):
        seg = make_segment("今天讲Python基础", 20.0, 25.0)
        self.sel._call_text_llm = Mock(
            return_value=json.dumps({"need_frame": False, "reason": "纯口头讲解"})
        )
        decision = self.sel._decide(seg)
        assert decision.need_frame is False

    def test_decide_llm_failure_fallback(self):
        """LLM 失败时，启发式已过滤，默认 need_frame=True。"""
        seg = make_segment("看图说话", 5.0, 10.0)
        self.sel._call_text_llm = Mock(side_effect=Exception("timeout"))
        decision = self.sel._decide(seg)
        assert decision.need_frame is True  # fallback

    def test_evaluate_keep_true(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xd9")  # minimal JPEG header+end
        self.sel._call_vision_llm = Mock(
            return_value=json.dumps({"keep": True, "reason": "PPT内容"})
        )
        ev = self.sel._evaluate(frame, "展示架构")
        assert ev.keep is True

    def test_evaluate_discard(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xd9")
        self.sel._call_vision_llm = Mock(
            return_value=json.dumps({"keep": False, "reason": "人脸特写"})
        )
        ev = self.sel._evaluate(frame, "讲师正在讲")
        assert ev.keep is False

    def test_evaluate_llm_failure_defaults_keep(self, tmp_path):
        """评估失败时默认保留（不丢帧）。"""
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xd9")
        self.sel._call_vision_llm = Mock(side_effect=Exception("timeout"))
        ev = self.sel._evaluate(frame, "看图")
        assert ev.keep is True


# ------------------------------------------------------------------
# Full select() loop
# ------------------------------------------------------------------

class TestAgentKeyframeSelectorSelect:
    def setup_method(self):
        self.sel = make_selector()

    def test_select_skips_segments_without_visual_keywords(self, tmp_path):
        """无视觉关键词的段落不触发截帧。"""
        segments = [
            make_segment("今天讲Python", 0.0, 5.0),
            make_segment("欢迎大家", 5.0, 10.0),
        ]
        self.sel._call_text_llm = Mock()  # should not be called

        result = self.sel.select("v.mp4", segments, tmp_path)

        self.sel._call_text_llm.assert_not_called()
        assert result == []

    def test_select_captures_and_keeps_frame(self, tmp_path):
        """段落有视觉关键词 → 截帧 → LLM 判断保留。"""
        segments = [make_segment("如图所示", 30.0, 35.0)]

        self.sel._call_text_llm = Mock(
            return_value=json.dumps({"need_frame": True, "reason": "提到图"})
        )
        self.sel._call_vision_llm = Mock(
            return_value=json.dumps({"keep": True, "reason": "PPT"})
        )

        with patch("framelearn.pipeline.agent_keyframe_selector.FFmpegHelper") as mock_ff:
            mock_ff.capture_single_frame.return_value = True
            # Simulate the frame file existing after capture
            expected_frame = tmp_path / "frame_00h00m30s.jpg"
            expected_frame.write_bytes(b"\xff\xd8\xff\xd9")

            result = self.sel.select("v.mp4", segments, tmp_path)

        assert len(result) == 1
        _, ts = result[0]
        assert ts == 30.0

    def test_select_discards_low_value_frame(self, tmp_path):
        """LLM 评估无价值时，删除帧并不加入结果。"""
        segments = [make_segment("看图", 60.0, 65.0)]

        self.sel._call_text_llm = Mock(
            return_value=json.dumps({"need_frame": True, "reason": "提到图"})
        )
        self.sel._call_vision_llm = Mock(
            return_value=json.dumps({"keep": False, "reason": "空白屏"})
        )

        with patch("framelearn.pipeline.agent_keyframe_selector.FFmpegHelper") as mock_ff:
            mock_ff.capture_single_frame.return_value = True
            frame = tmp_path / "frame_00h01m00s.jpg"
            frame.write_bytes(b"\xff\xd8\xff\xd9")

            result = self.sel.select("v.mp4", segments, tmp_path)

        assert result == []
        assert not frame.exists()  # deleted

    def test_select_deduplicates_nearby_timestamps(self, tmp_path):
        """existing_keyframes 中已有 ±2 秒内的帧时，即使 LLM 同意也不重复截帧。"""
        segments = [make_segment("如图", 30.5, 35.0)]
        existing = [(tmp_path / "existing.jpg", 30.0)]
        (tmp_path / "existing.jpg").write_bytes(b"\xff\xd8\xff\xd9")

        self.sel._call_text_llm = Mock(
            return_value=json.dumps({"need_frame": True, "reason": "图"})
        )
        # Even if LLM says yes, dedup should prevent a new frame at 30.5
        # (within 2s of existing 30.0)
        with patch("framelearn.pipeline.agent_keyframe_selector.FFmpegHelper") as mock_ff:
            mock_ff.capture_single_frame.return_value = True

            result = self.sel.select("v.mp4", segments, tmp_path, existing_keyframes=existing)

        # capture_single_frame should NOT have been called (dedup skipped it)
        mock_ff.capture_single_frame.assert_not_called()
        # existing frame is preserved
        assert len(result) == 1
        assert result[0][1] == 30.0

    def test_select_merges_existing_keyframes(self, tmp_path):
        """结果包含 existing_keyframes（无新增时）。"""
        segments = [make_segment("今天讲Python", 0.0, 5.0)]  # no visual keywords
        existing = [(tmp_path / "frame.jpg", 45.0)]
        (tmp_path / "frame.jpg").write_bytes(b"\xff\xd8\xff\xd9")

        result = self.sel.select("v.mp4", segments, tmp_path, existing_keyframes=existing)

        assert len(result) == 1
        assert result[0][1] == 45.0
