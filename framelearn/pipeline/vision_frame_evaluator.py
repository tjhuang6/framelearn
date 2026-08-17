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
from framelearn.errors import ConfigurationError, GenerationError
from framelearn.llm.catalog import get_model_capabilities
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
      "anchor_id": "c1_a1",
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
- anchor_id 必须原样复制输入标记中的完整 id（例如 c1_a1），不能省略前缀写成 a1
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


def _parse_evaluation_decision(
    decision: object, item: AnchorFrame
) -> FrameEvaluation | None:
    """Parse one vision decision. Returns None when invalid."""
    if not isinstance(decision, dict):
        return None

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

    return FrameEvaluation(
        anchor_id=item.anchor_id,
        srt_id=item.srt_id,
        frame_path=str(decision.get("frame", item.frame_path)),
        timestamp=item.timestamp,
        keep_image=keep,
        content_type=content_type,
        caption=str(decision.get("caption", "")),
        text_representation=str(decision.get("text_representation", "")),
        reason=str(decision.get("reason", "")),
        retake=retake,
        retake_timestamp=retake_timestamp,
    )


def _match_anchor_item(
    anchor_id: object, item_map: dict[str, AnchorFrame]
) -> AnchorFrame | None:
    """Match a model-returned anchor id to a batch item.

    Qwen3-VL often drops the global ``c<chunk>_`` prefix and returns just
    ``a1``. Exact matches win; otherwise a unique ``_<id>`` suffix match is
    accepted (within one batch all ids belong to the same chunk).
    """
    if not isinstance(anchor_id, str) or not anchor_id:
        return None
    if anchor_id in item_map:
        return item_map[anchor_id]

    suffix = f"_{anchor_id}"
    matches = [item for aid, item in item_map.items() if aid.endswith(suffix)]
    return matches[0] if len(matches) == 1 else None


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
    by_anchor: dict[str, FrameEvaluation] = {}
    seen: set[str] = set()
    for decision in decisions:
        anchor_id = (
            decision.get("anchor_id") if isinstance(decision, dict) else None
        )
        item = _match_anchor_item(anchor_id, item_map)
        if item is None or item.anchor_id in seen:
            return None
        seen.add(item.anchor_id)

        evaluation = _parse_evaluation_decision(decision, item)
        if evaluation is None:
            return None
        by_anchor[item.anchor_id] = evaluation

    # Always return in input order; the caller zips this list with ``items``.
    return [by_anchor[item.anchor_id] for item in items]


def _parse_evaluations_lenient(
    raw: str, items: list[AnchorFrame]
) -> list[FrameEvaluation | None] | None:
    """Parse partial/incomplete vision responses.

    Models sometimes omit a decision when shown many frames. The caller
    retries only the missing anchors until every item has a valid decision.
    """
    data = parse_json_object(raw)
    if data is None:
        return None

    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        return None

    item_map = {item.anchor_id: item for item in items}
    by_anchor: dict[str, FrameEvaluation] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        item = _match_anchor_item(decision.get("anchor_id"), item_map)
        if item is None or item.anchor_id in by_anchor:
            continue
        evaluation = _parse_evaluation_decision(decision, item)
        if evaluation is not None:
            by_anchor[item.anchor_id] = evaluation

    result = [by_anchor.get(item.anchor_id) for item in items]
    if not any(evaluation is not None for evaluation in result):
        return None
    return result


def _resolve_vision_max_retries(
    *,
    max_retries: int | None,
    max_calls: int | None,
) -> int:
    """Resolve vision retry budget from ``max_calls`` (total attempts).

    ``max_calls`` is the user-facing setting: total model calls including
    the first attempt. ``max_retries`` remains available for code/tests.
    """
    if max_calls is not None:
        if max_calls < 1:
            raise ConfigurationError(
                "blog_gen.vision_max_calls 必须大于等于 1"
            )
        return max_calls - 1

    if max_retries is not None:
        if max_retries < 0:
            raise ConfigurationError("max_retries 不能小于 0")
        return max_retries

    calls = int(config_get("blog_gen.vision_max_calls", 3))
    if calls < 1:
        raise ConfigurationError("blog_gen.vision_max_calls 必须大于等于 1")
    return calls - 1


class VisionFrameEvaluator:
    """Validate candidate frames with the vision model, including retakes."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        max_retakes: int | None = None,
        max_retries: int | None = None,
        timeout: int = 600,
        max_tokens: int | None = None,
        batch_size: int | None = None,
        max_calls: int | None = None,
    ):
        self.config = config or load_vision_config()
        self.max_retakes = (
            max_retakes
            if max_retakes is not None
            else int(config_get("blog_gen.max_retakes", 1))
        )
        self.timeout = timeout
        self.max_retries = _resolve_vision_max_retries(
            max_retries=max_retries,
            max_calls=max_calls,
        )
        configured_max_tokens = (
            max_tokens
            if max_tokens is not None
            else int(config_get("blog_gen.vision_max_tokens", 8192))
        )
        capabilities = get_model_capabilities(self.config.model)
        if (
            capabilities
            and capabilities.max_tokens
            and configured_max_tokens > capabilities.max_tokens
        ):
            raise ConfigurationError(
                "blog_gen.vision_max_tokens 配置错误："
                f"{configured_max_tokens} 超过模型 {self.config.model} "
                f"的最大输出 {capabilities.max_tokens}，请改小该值"
            )
        self.max_tokens = configured_max_tokens
        self.batch_size = (
            batch_size
            if batch_size is not None
            else int(config_get("blog_gen.vision_batch_size", 8))
        )
        if self.batch_size <= 0:
            raise ValueError("blog_gen.vision_batch_size 必须大于 0")

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
            # Split large batches. A 20-image request produces a very long
            # JSON decision list and the model often omits entries; smaller
            # batches keep each response short and much more reliable.
            evaluations: list[FrameEvaluation] = []
            for start in range(0, len(pending), self.batch_size):
                batch = pending[start : start + self.batch_size]
                evaluations.extend(await self._evaluate_batch(batch))

            next_round: list[AnchorFrame] = []

            for evaluation, item in zip(evaluations, pending):
                if evaluation.retake and retake_budget <= 0:
                    # Retake budget exhausted. Per the blog-anchor design
                    # (openspec archive: "retake 超限 | keep_image=true
                    # 保守保留"), conservatively keep the current frame
                    # instead of failing the whole run: one picky anchor
                    # must not abort 34 other chunks.
                    get_reporter().record_fallback(
                        "vision_frame_evaluator.retake_limit",
                        (
                            f"锚点 {item.anchor_id} 超过 retake 上限"
                            f"（{self.max_retakes}），已保守保留当前帧"
                        ),
                        detail={
                            "anchor_id": item.anchor_id,
                            "timestamp": item.timestamp,
                        },
                    )
                    evaluation.retake = False
                    evaluation.retake_timestamp = None
                    evaluation.keep_image = True
                    if not evaluation.reason:
                        evaluation.reason = "retake 超限，保守保留当前帧"
                    final.append(evaluation)
                    continue

                if evaluation.retake:
                    retake_ts = evaluation.retake_timestamp or evaluation.timestamp
                    chunk_dir = temp_frames / "retakes"
                    chunk_dir.mkdir(parents=True, exist_ok=True)
                    target = chunk_dir / (
                        f"{item.anchor_id}_retake_{item.srt_id:03d}_{retake_ts:.3f}.jpg"
                    )
                    # FFmpeg runs synchronously; offload it so one chunk's
                    # retake cannot block the other concurrent chunks.
                    ok = await asyncio.to_thread(
                        FFmpegHelper.capture_single_frame,
                        video_path,
                        retake_ts,
                        str(target),
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
                        raise GenerationError(
                            f"retake 截帧失败：{item.anchor_id}@{retake_ts}s"
                        )
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
    ) -> list[FrameEvaluation]:
        """Evaluate a batch; retry/repair until every item has a decision.

        There is deliberately no ``keep everything`` fallback. If the
        vision model cannot produce a valid decision for every item after
        the retry budget, the whole run fails with
        :class:`GenerationError`.
        """

        def build_segments(batch: list[AnchorFrame]) -> list[dict]:
            segments = [{"type": "text", "text": EVALUATOR_PROMPT}]
            for item in batch:
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
            return segments

        def repair_segments(raw_response: str) -> list[dict]:
            return [
                {
                    "type": "text",
                    "text": (
                        "你上一次的输出无法被程序解析为要求的 JSON。"
                        "请只输出修正后的完整 JSON，不要输出解释。\n\n"
                        f"<RAW>{raw_response}</RAW>"
                    ),
                }
            ]

        current_items = list(items)
        last_error: Exception | None = None
        last_raw_response: str | None = None

        for attempt in range(self.max_retries + 1):
            try:
                if last_raw_response is None:
                    response = await call_llm_async_interleaved(
                        build_segments(current_items),
                        self.config,
                        max_tokens=self.max_tokens,
                        timeout=self.timeout,
                    )
                else:
                    response = await call_llm_async_interleaved(
                        repair_segments(last_raw_response),
                        self.config,
                        max_tokens=self.max_tokens,
                        timeout=self.timeout,
                    )

                parsed = _parse_evaluations(response, current_items)
                if parsed is not None:
                    return parsed

                partial = _parse_evaluations_lenient(response, current_items)
                if partial is None:
                    last_raw_response = response
                    raise ValueError(
                        "VisionFrameEvaluator response did not match schema"
                    )

                missing = [
                    item
                    for item, evaluation in zip(current_items, partial)
                    if evaluation is None
                ]
                if not missing:
                    get_reporter().record_repair(
                        "vision_frame_evaluator.partial_schema",
                        (
                            f"视觉模型 JSON 不完整，已恢复全部 "
                            f"{len(current_items)} 个决策"
                        ),
                        detail={"recovered": len(current_items)},
                    )
                    return [evaluation for evaluation in partial if evaluation is not None]

                get_reporter().record_repair(
                    "vision_frame_evaluator.partial_schema",
                    (
                        f"视觉模型漏了 {len(missing)}/{len(current_items)} 个决策，"
                        "将只重试缺失项"
                    ),
                    detail={
                        "recovered": len(current_items) - len(missing),
                        "total": len(current_items),
                    },
                )
                last_raw_response = None
                current_items = missing
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)
                continue
            except ConfigurationError:
                raise
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

        get_reporter().record_fallback(
            "vision_frame_evaluator.generation_failed",
            f"视觉验图失败（{last_error}），已停止运行",
            detail={"pending_items": len(current_items)},
        )
        raise GenerationError(
            f"视觉模型重试 {self.max_retries + 1} 次后仍有 "
            f"{len(current_items)} 个候选帧无法得到有效决策（{last_error}）"
        )
