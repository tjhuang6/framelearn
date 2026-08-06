## Why

当前 `AgentKeyframeSelector._evaluate()` 对 Vision API 发起单次调用，只接收一个 `{"keep": true/false}` 的 JSON 响应。模型没有能力拒绝一张不好的帧并要求重新截取——如果截到的时刻模糊、处于画面过渡中或不具代表性，流程要么保留一张低质量帧，要么直接丢弃，不做任何补救。将评估步骤改为真正的 Agent 工具调用循环，让 Vision 模型在做出最终决策之前能够自主要求在不同时间点重新截帧。

## What Changes

- 用 Agent 循环替换一次性的 `_evaluate()` Vision API 调用；循环持续运行，直到模型通过 tool call 发出最终的 `keep`/`discard` 决策。
- 向 Vision Agent 暴露两个工具：`capture_frame(timestamp)` 和 `decide(keep, reason)`。
- Vision 模型可在调用 `decide` 之前，零次或多次调用 `capture_frame`（受可配置的最大重试次数限制）。
- Python 不再硬编码单一时间戳——由 Agent 在循环内决定截取哪个时刻。
- 现有的 `_decide()` 文字模型决策步骤和 `_heuristic_needs_frame()` 启发式预过滤保持不变。
- `_evaluate_text_only()` fallback 保留，作为 Agent 循环自身失败时的兜底。

## Capabilities

### New Capabilities
- `keyframe-vision-agent-eval`：Vision 模型驱动一个迭代式"观察 → 截帧 → 决策"循环，用于评估每个候选关键帧，替代原有的单次 JSON 评估。

### Modified Capabilities

## Impact

- `framelearn/pipeline/agent_keyframe_selector.py`：`_evaluate()` 方法及 `_call_vision_llm()` 辅助方法被新的 `_evaluate_agent_loop()` 替换。
- `framelearn/pipeline/ffmpeg_helper.py`：`capture_single_frame()` 需在 Agent 循环内可调用（目前接口已满足，预计无需改动）。
- 新增 `framelearn/pipeline/vision_agent.py`（或在现有模块中扩展）：承载工具调用循环逻辑与工具定义。
- CLI 接口、流水线编排及上游字幕分段逻辑均不受影响。
- 当模型请求重新截帧时，每个字幕片段的 API token 消耗将增加，受最大重试次数限制。
