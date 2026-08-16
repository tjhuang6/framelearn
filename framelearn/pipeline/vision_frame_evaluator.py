"""VisionFrameEvaluator — the vision model validates frames selected by text.

The text model only knows timestamps, not pixels. This module shows each
candidate frame to the vision model and receives:

- ``retake`` / ``retake_timestamp``: ask FFmpeg for a better frame
- ``keep_image``: keep the image or delete the anchor
- ``content_type``: what kind of visual content it is
- ``caption`` / ``text_representation``: optional content placed below a
  kept image

``retake=true`` loops back through FFmpeg capture and re-evaluation,
bounded by ``blog_gen.max_retakes``.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from pathlib import Path

from framelearn.config import get as config_get
from framelearn.pipeline.ffmpeg_helper import FFmpegHelper
from framelearn.pipeline.llm_json import parse_bool, parse_json_object
from framelearn.pipeline.run_report import get_reporter
from framelearn.provider_adapter import (
    ProviderConfig,
    call_llm_async_interleaved,
    load_vision_config,
)


CONTENT_TYPES = {
    "text_slide",
    "terminal",
    "code",
    "diagram",
    "formula",
    "table",
    "screenshot",
    "face",
    "blank",
    "transition",
    "other",
}

EVALUATOR_PROMPT = """你是视频教材关键帧评估助手。你会看到若干候选帧，每张帧前面都有一个标记块，说明它对应的锚点和字幕上下文。

对每张图输出一个决策：

{{
  "decisions": [
    {{
      "anchor_id": "a1",
      "frame": "<frame path>",
      "retake": false,
      "retake_timestamp": null,
      "keep_image": true,
      "content_type": "diagram",
      "caption": "图片说明（保留图片时可写）",
      "text_representation": "图片中的文字内容（可选，纯文字图时尽量提取）",
      "reason": "简短理由"
    }}
  ]
}}

判断标准：
- diagram / formula / table / screenshot / code → 通常 keep_image=true
- text_slide / terminal → keep_image=true，并尽量在 text_representation 中提取图中的文字
- face / blank / transition → keep_image=false
- 模糊、过暗、不完整、时间点不对 → retake=true，并给出 retake_timestamp
- content_type 只能是：text_slide, terminal, code, diagram, formula, table, screenshot, face, blank, transition, other
- retake=true 时忽略 keep_image；retake_timestamp 必须是数字
- 不要输出 markdown 之外的解释文字
"""


@dataclass
class AnchorFrame:
    anchor_id: str
    srt_id: int
    frame_path: str
    timestamp: float
    subtitle_text: str


@dataclass
class FrameEvaluation:
    anchor_id: str
    srt_id: int
    frame_path: str
    timestamp: float
    keep_image: bool
    content_type: str
    caption: str
    text_representation: str
    reason: str
    retake: bool = False
    retake_timestamp: float | None = None


def _parse_evaluations(
    raw: str, items: list[AnchorFrame]
) -> list[FrameEvaluation] | None:
    data = parse_json_object(raw)
    if data is None:
        return None

    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        return None
    if len(decisions) != len(items):
        return None

    item_map = {item.anchor_id: item for item in items}
    parsed: list[FrameEvaluation] = []
    seen: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            return None
        anchor_id = decision.get("anchor_id")
        if anchor_id not in item_map or anchor_id in seen:
            return None
        seen.add(anchor_id)

        keep = parse_bool(decision.get("keep_image", False), field="keep_image")
        retake = parse_bool(decision.get("retake", False), field="retake")
        if keep is None or retake is None:
            return None

        content_type = str(decision.get("content_type", "other"))
        if content_type not in CONTENT_TYPES:
            content_type = "other"

        retake_timestamp_raw = decision.get("retake_timestamp")
        retake_timestamp: float | None = None
        if retake:
            try:
                retake_timestamp = float(retake_timestamp_raw)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(retake_timestamp) or retake_timestamp < 0:
                return None

        frame_path = str(decision.get("frame", item_map[anchor_id].frame_path))
        parsed.append(
            FrameEvaluation(
                anchor_id=anchor_id,
                srt_id=item_map[anchor_id].srt_id,
                frame_path=frame_path,
                timestamp=item_map[anchor_id].timestamp,
                keep_image=keep,
                content_type=content_type,
                caption=str(decision.get("caption", "")),
                text_representation=str(decision.get("text_representation", "")),
                reason=str(decision.get("reason", "")),
                retake=retake,
                retake_timestamp=retake_timestamp,
            )
        )
    return parsed


def _fallback_keep(item: AnchorFrame) -> FrameEvaluation:
    return FrameEvaluation(
        anchor_id=item.anchor_id,
        srt_id=item.srt_id,
        frame_path=item.frame_path,
        timestamp=item.timestamp,
        keep_image=True,
        content_type="other",
        caption="",
        text_representation="",
        reason="vision fallback keep",
    )


class VisionFrameEvaluator:
    """Validate candidate frames with the vision model, including retakes."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        max_retakes: int | None = None,
        max_retries: int = 2,
        timeout: int = 600,
    ):
        self.config = config or load_vision_config()
        self.max_retakes = (
            max_retakes
            if max_retakes is not None
            else int(config_get("blog_gen.max_retakes", 1))
        )
        self.max_retries = max_retries
        self.timeout = timeout

    async def evaluate(
        self,
        items: list[AnchorFrame],
        video_path: str,
        temp_frames: Path,
    ) -> list[FrameEvaluation]:
        """Evaluate a list of anchor frames.

        Retake requests are handled inside this method: the frame is
        captured again at the requested timestamp and re-evaluated.
        """
        if not items:
            return []

        pending = list(items)
        final: list[FrameEvaluation] = []
        retake_budget = self.max_retakes

        while pending:
            evaluations = await self._evaluate_batch(pending)
            next_round: list[AnchorFrame] = []

            for evaluation, item in zip(evaluations, pending):
                if evaluation is None:
                    final.append(_fallback_keep(item))
                    continue

                if evaluation.retake and retake_budget <= 0:
                    get_reporter().record_fallback(
                        "vision_frame_evaluator.retake_limit",
                        f"锚点 {item.anchor_id} 超过 retake 上限，保守保留",
                    )
                    final.append(_fallback_keep(item))
                    continue

                if evaluation.retake:
                    retake_ts = evaluation.retake_timestamp or evaluation.timestamp
                    chunk_dir = temp_frames / "retakes"
                    chunk_dir.mkdir(parents=True, exist_ok=True)
                    target = chunk_dir / (
                        f"{item.anchor_id}_retake_{item.srt_id:03d}_{retake_ts:.3f}.jpg"
                    )
                    ok = FFmpegHelper.capture_single_frame(
                        video_path, retake_ts, str(target)
                    )
                    if not ok:
                        get_reporter().record_skipped_frame(
                            "vision_frame_evaluator.retake",
                            f"retake 截帧失败：{item.anchor_id}@{retake_ts}s",
                            detail={
                                "anchor_id": item.anchor_id,
                                "timestamp": retake_ts,
                            },
                        )
                        final.append(_fallback_keep(item))
                        continue
                    next_round.append(
                        AnchorFrame(
                            anchor_id=item.anchor_id,
                            srt_id=item.srt_id,
                            frame_path=str(target),
                            timestamp=retake_ts,
                            subtitle_text=item.subtitle_text,
                        )
                    )
                else:
                    final.append(evaluation)

            if next_round:
                retake_budget -= 1
            pending = next_round

        return final

    async def _evaluate_batch(
        self, items: list[AnchorFrame]
    ) -> list[FrameEvaluation | None]:
        prompt = EVALUATOR_PROMPT
        segments = [
            {"type": "text", "text": prompt},
        ]
        for item in items:
            segments.append(
                {
                    "type": "text",
                    "text": (
                        f"\n锚点：[[FRAME:{item.anchor_id}@{item.timestamp}]]\n"
                        f"上下文字幕：{item.subtitle_text}\n"
                        f"帧路径：{item.frame_path}\n"
                    ),
                }
            )
            segments.append({"type": "image", "path": item.frame_path})

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_llm_async_interleaved(
                    segments,
                    self.config,
                    max_tokens=4096,
                    timeout=self.timeout,
                )
                parsed = _parse_evaluations(response, items)
                if parsed is None:
                    raise ValueError("VisionFrameEvaluator response did not match schema")
                return parsed
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

        get_reporter().record_fallback(
            "vision_frame_evaluator.fallback",
            f"视觉验图失败（{last_error}），保守保留 {len(items)} 张候选帧",
        )
        return [_fallback_keep(item) for item in items]
