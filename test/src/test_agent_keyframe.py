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
        frame.write_bytes(b"\xff\xd8\xff\xd9")
        with patch(
            "framelearn.pipeline.agent_keyframe_selector.VisionAgentEvaluator"
        ) as MockEval:
            MockEval.return_value.evaluate.return_value = MagicMock(keep=True, reason="PPT内容")
            ev = self.sel._evaluate(frame, "展示架构", "v.mp4", tmp_path, 30.0)
        assert ev.keep is True

    def test_evaluate_discard(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xd9")
        with patch(
            "framelearn.pipeline.agent_keyframe_selector.VisionAgentEvaluator"
        ) as MockEval:
            MockEval.return_value.evaluate.return_value = MagicMock(keep=False, reason="人脸特写")
            ev = self.sel._evaluate(frame, "讲师正在讲", "v.mp4", tmp_path, 30.0)
        assert ev.keep is False

    def test_evaluate_agent_failure_falls_back_to_text(self, tmp_path):
        """Vision agent 抛异常时 fallback 至文字评估，默认保留。"""
        frame = tmp_path / "frame.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xd9")
        with patch(
            "framelearn.pipeline.agent_keyframe_selector.VisionAgentEvaluator",
            side_effect=RuntimeError("api error"),
        ):
            self.sel._call_text_llm = Mock(side_effect=Exception("timeout"))
            ev = self.sel._evaluate(frame, "看图", "v.mp4", tmp_path, 30.0)
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
        """段落有视觉关键词 → 截帧 → agent 判断保留。"""
        segments = [make_segment("如图所示", 30.0, 35.0)]

        self.sel._call_text_llm = Mock(
            return_value=json.dumps({"need_frame": True, "reason": "提到图"})
        )

        with patch("framelearn.pipeline.agent_keyframe_selector.FFmpegHelper") as mock_ff:
            mock_ff.capture_single_frame.return_value = True
            expected_frame = tmp_path / "frame_00h00m30s.jpg"
            expected_frame.write_bytes(b"\xff\xd8\xff\xd9")

            with patch(
                "framelearn.pipeline.agent_keyframe_selector.VisionAgentEvaluator"
            ) as MockEval:
                MockEval.return_value.evaluate.return_value = MagicMock(keep=True, reason="PPT")
                result = self.sel.select("v.mp4", segments, tmp_path)

        assert len(result) == 1
        _, ts = result[0]
        assert ts == 30.0

    def test_select_discards_low_value_frame(self, tmp_path):
        """agent 评估无价值时，删除帧并不加入结果。"""
        segments = [make_segment("看图", 60.0, 65.0)]

        self.sel._call_text_llm = Mock(
            return_value=json.dumps({"need_frame": True, "reason": "提到图"})
        )

        with patch("framelearn.pipeline.agent_keyframe_selector.FFmpegHelper") as mock_ff:
            mock_ff.capture_single_frame.return_value = True
            # 新命名格式：毫秒精度 + 来源标记 + 序号
            frame = tmp_path / "frame_00h01m00s000ms_agent_001.jpg"
            frame.write_bytes(b"\xff\xd8\xff\xd9")

            with patch(
                "framelearn.pipeline.agent_keyframe_selector.VisionAgentEvaluator"
            ) as MockEval:
                MockEval.return_value.evaluate.return_value = MagicMock(keep=False, reason="空白屏")
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


# ------------------------------------------------------------------
# VisionAgentEvaluator — tool-calling loop (tasks 4.1–4.4)
# ------------------------------------------------------------------

def _make_tool_response(tool_name: str, arguments: dict, call_id: str = "call_001") -> dict:
    """Build a fake OpenAI tool-call response body."""
    import json as _json
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": _json.dumps(arguments),
                    },
                }],
            }
        }]
    }


class TestVisionAgentEvaluator:
    def _make_evaluator(self, tmp_path, max_retries: int = 3):
        from framelearn.pipeline.vision_agent import VisionAgentEvaluator
        from framelearn.provider_adapter import ProviderConfig
        ev = VisionAgentEvaluator.__new__(VisionAgentEvaluator)
        ev.max_retries = max_retries
        ev._config = ProviderConfig(
            provider="siliconflow",
            api_key="sk-test",
            model="Qwen/test",
            base_url="https://api.siliconflow.cn/v1/",
        )
        return ev

    def _make_frame(self, tmp_path, name: str = "frame.jpg") -> "Path":
        f = tmp_path / name
        f.write_bytes(b"\xff\xd8\xff\xd9")
        return f

    # 4.1: happy path — model calls decide on first turn
    def test_happy_path_direct_decide(self, tmp_path):
        """模型首帧直接调用 decide → keep=True。"""
        frame = self._make_frame(tmp_path)
        ev = self._make_evaluator(tmp_path)

        with patch(
            "framelearn.pipeline.vision_agent.call_llm_with_tools",
            return_value=_make_tool_response("decide", {"keep": True, "reason": "PPT内容"}),
        ):
            result = ev.evaluate(frame, "如图所示", "v.mp4", tmp_path, 30.0)

        assert result.keep is True
        assert result.reason == "PPT内容"

    # 4.2: one re-capture then decide
    def test_one_recapture_then_decide(self, tmp_path):
        """模型调用一次 capture_frame，再调用 decide → keep=True。"""
        frame = self._make_frame(tmp_path)
        ev = self._make_evaluator(tmp_path)

        responses = [
            _make_tool_response("capture_frame", {"timestamp": 32.0}, call_id="c1"),
            _make_tool_response("decide", {"keep": True, "reason": "代码页面"}, call_id="c2"),
        ]

        new_frame = tmp_path / "frame_agent_00h00m32s.jpg"
        new_frame.write_bytes(b"\xff\xd8\xff\xd9")

        with patch(
            "framelearn.pipeline.vision_agent.call_llm_with_tools",
            side_effect=responses,
        ):
            with patch(
                "framelearn.pipeline.vision_agent.FFmpegHelper.capture_single_frame",
                return_value=True,
            ):
                result = ev.evaluate(frame, "看代码", "v.mp4", tmp_path, 30.0)

        assert result.keep is True
        assert result.reason == "代码页面"

    # 4.3: max retries reached → conservative keep
    def test_max_retries_forces_keep(self, tmp_path):
        """模型持续调用 capture_frame 超过上限，强制 keep=True 退出。"""
        frame = self._make_frame(tmp_path)
        ev = self._make_evaluator(tmp_path, max_retries=2)

        # Always respond with capture_frame
        capture_response = _make_tool_response("capture_frame", {"timestamp": 5.0})
        new_frame = tmp_path / "frame_agent_00h00m05s.jpg"
        new_frame.write_bytes(b"\xff\xd8\xff\xd9")

        with patch(
            "framelearn.pipeline.vision_agent.call_llm_with_tools",
            return_value=capture_response,
        ):
            with patch(
                "framelearn.pipeline.vision_agent.FFmpegHelper.capture_single_frame",
                return_value=True,
            ):
                result = ev.evaluate(frame, "看图", "v.mp4", tmp_path, 3.0)

        assert result.keep is True
        assert "最大重试次数" in result.reason

    # 4.4: call_llm_with_tools raises → fallback to _evaluate_text_only
    def test_agent_exception_triggers_selector_fallback(self, tmp_path):
        """call_llm_with_tools 抛出异常时，_evaluate() fallback 至文字评估。"""
        frame = self._make_frame(tmp_path)
        sel = make_selector()

        with patch(
            "framelearn.pipeline.vision_agent.call_llm_with_tools",
            side_effect=RuntimeError("network error"),
        ):
            with patch.dict("os.environ", {"VISION_API_KEY": "sk-test"}):
                # text fallback also fails → default keep=True
                sel._call_text_llm = Mock(side_effect=Exception("timeout"))
                result = sel._evaluate(frame, "看图", "v.mp4", tmp_path, 5.0)

        assert result.keep is True


# ------------------------------------------------------------------
# provider_adapter — tool-calling interface (tasks 4.5–4.6)
# ------------------------------------------------------------------

class TestCallLlmWithTools:
    def _make_config(self, provider: str = "siliconflow") -> "ProviderConfig":
        from framelearn.provider_adapter import ProviderConfig, PROVIDERS
        p = PROVIDERS[provider]
        return ProviderConfig(
            provider=provider,
            api_key="sk-test",
            model=p["default_model"],
            base_url=p["base_url"],
        )

    # 4.5: OpenAI path injects tools field
    def test_openai_path_injects_tools_field(self, tmp_path):
        """OpenAI-compatible 路径应在请求 body 中包含 tools 和 tool_choice 字段。"""
        import httpx
        from framelearn.provider_adapter import call_llm_with_tools

        tools = [{"type": "function", "function": {"name": "decide", "parameters": {}}}]
        messages = [{"role": "user", "content": "test"}]
        config = self._make_config("siliconflow")

        fake_response = {
            "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": []}}]
        }

        with patch("framelearn.provider_adapter.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=fake_response),
            )
            call_llm_with_tools(messages, tools, config)

        sent_body = mock_post.call_args.kwargs["json"]
        assert "tools" in sent_body
        assert sent_body["tools"] == tools
        assert sent_body["tool_choice"] == "required"

    # 4.6: google and claude providers raise NotImplementedError
    def test_gemini_raises_not_implemented(self):
        """Google Gemini provider 应抛出 NotImplementedError。"""
        from framelearn.provider_adapter import ProviderConfig, PROVIDERS, call_llm_with_tools
        config = ProviderConfig(
            provider="gemini",
            api_key="key",
            model=PROVIDERS["gemini"]["default_model"],
            base_url=PROVIDERS["gemini"]["base_url"],
        )
        with pytest.raises(NotImplementedError):
            call_llm_with_tools([{"role": "user", "content": "x"}], [], config)

    def test_claude_raises_not_implemented(self):
        """Claude provider 应抛出 NotImplementedError。"""
        from framelearn.provider_adapter import ProviderConfig, PROVIDERS, call_llm_with_tools
        config = ProviderConfig(
            provider="claude",
            api_key="key",
            model=PROVIDERS["claude"]["default_model"],
            base_url=PROVIDERS["claude"]["base_url"],
        )
        with pytest.raises(NotImplementedError):
            call_llm_with_tools([{"role": "user", "content": "x"}], [], config)
