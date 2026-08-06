# keyframe-vision-agent-eval Specification

## Purpose
为每个候选关键帧提供由 Vision 模型驱动的迭代评估能力：模型可在做出最终保留或丢弃决策之前，主动请求在不同时间点重新截帧，从而从根本上改善低质量首帧导致错误评估的问题。
## Requirements
### Requirement: Vision Agent 通过 tool call 驱动截帧与决策
系统 SHALL 向 Vision 模型暴露两个工具：`capture_frame(timestamp)` 和 `decide(keep, reason)`。模型 MUST 通过调用 `decide` 结束评估循环；在此之前可调用 `capture_frame` 零次或任意多次。系统 SHALL 不接受自由文本 JSON 作为最终决策，决策 MUST 来自 `decide` tool call。

#### Scenario: 模型对首帧满意，直接决策
- **WHEN** Vision Agent 收到首帧图像与字幕上下文
- **THEN** Agent 调用 `decide(keep=true/false, reason=...)` 结束循环，系统按决策保留或丢弃该帧

#### Scenario: 模型对首帧不满意，请求重新截帧
- **WHEN** Vision Agent 判断当前帧不具代表性（如过渡动画、模糊画面）
- **THEN** Agent 调用 `capture_frame(timestamp)` 指定新时间点，系统截取新帧并将图像返回给 Agent，循环继续

#### Scenario: 模型多次重新截帧后做出决策
- **WHEN** Agent 在多次调用 `capture_frame` 后找到满意的帧
- **THEN** Agent 调用 `decide` 提交最终决策，系统以该决策为准

### Requirement: 循环重试次数上限
系统 SHALL 对单次评估循环的 `capture_frame` 调用次数施加可配置上限（默认值 SHALL 不超过 5 次）。达到上限后，系统 MUST 强制 Agent 调用 `decide` 或代替 Agent 作出保守决策（保留）。

#### Scenario: 达到重试上限
- **WHEN** Agent 已调用 `capture_frame` 达到配置的最大次数
- **THEN** 系统不再执行新的截帧请求，以最后一次评估结果或保守默认值（keep=true）作为最终决策

### Requirement: Agent 循环失败时的 fallback
当 Agent 循环因 API 错误、超时或工具调用格式异常而中断时，系统 SHALL 降级至纯文字评估（`_evaluate_text_only`），并在日志中标注 fallback 原因。

#### Scenario: Vision API 调用失败
- **WHEN** `capture_frame` 或 `decide` 的底层 Vision API 请求返回错误
- **THEN** 系统捕获异常，调用文字 fallback 评估，不向调用方抛出异常

#### Scenario: Agent 未在规定步数内调用 decide
- **WHEN** Agent 耗尽重试次数后仍未发出 `decide` tool call
- **THEN** 系统以 keep=true（保守保留）作为最终决策，并记录警告日志

### Requirement: 决策输入包含字幕上下文
系统 SHALL 在每轮 Agent 循环的消息中携带原始字幕片段文本，Vision Agent MUST 能够同时参考图像内容与字幕进行判断。

#### Scenario: 字幕与画面共同触发保留决策
- **WHEN** 字幕包含"如图""看代码"等指向画面的表达，画面为 PPT 或代码页
- **THEN** Agent 调用 `decide(keep=true, reason=...)` 保留该帧

#### Scenario: 字幕与画面不匹配时做出丢弃决策
- **WHEN** 字幕描述某操作，但画面仅为讲师人脸或空白屏
- **THEN** Agent 可调用 `capture_frame` 尝试更好的时间点，或直接调用 `decide(keep=false, reason=...)`

