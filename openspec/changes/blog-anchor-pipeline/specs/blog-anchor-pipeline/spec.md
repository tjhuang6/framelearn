# blog-anchor-pipeline Specification

## Purpose
把博客长文生成从视频模型迁移到文本模型。文本模型阅读带候选帧标记的 SRT chunk，输出含 `[[FRAME:<anchor_id>@<timestamp>]]` 锚点的博客文本；程序绑定真实帧，视频模型只做验图与补充说明，最后程序拼装 Markdown。

## ADDED Requirements

### Requirement: 先切块再插入候选帧标记
系统 SHALL 先用 raw SRT 按 `segment_minutes` 切 chunk，再向每个 chunk 插入该 chunk 时间范围内的启发式候选帧标记。系统 MUST NOT 覆盖 raw SRT。

#### Scenario: chunk 内插入帧标记
- **WHEN** 某 chunk 覆盖 `[0, 1800)`，且启发式候选帧时间戳为 `53.0`
- **THEN** BlogGenerator 收到的 annotated chunk 在最近字幕段后包含该帧的 `![picture N @ 53.0s](path)` 标记

#### Scenario: raw SRT 不被修改
- **WHEN** 流水线生成 annotated SRT
- **THEN** 磁盘上的 raw SRT 内容不变

### Requirement: BlogGenerator 输出博客文本与帧请求
系统 SHALL 调用文本模型，输入 annotated SRT chunk，输出 JSON：`blog_markdown` 和 `frame_requests`。博客文本中 SHALL 使用 `[[FRAME:<anchor_id>@<timestamp>]]` 表示配图位置。

#### Scenario: 文本模型复用候选帧
- **WHEN** 文本模型认为某个已有候选帧适合配图
- **THEN** `frame_request.request_type="reuse"` 且 `source_frame_path` 是输入中真实存在的路径

#### Scenario: 文本模型请求新时间戳
- **WHEN** 文本模型需要一张候选帧之外的新截图
- **THEN** `frame_request.request_type="new_capture"`、`source_frame_path=null`，并提供精准 `timestamp`

#### Scenario: 锚点非法
- **WHEN** `frame_request` 引用了不存在的候选路径，或 `anchor_id` 不在 blog 文本中
- **THEN** 该锚点被删除并写入 `run-report.json`

### Requirement: 锚点校验与补截
系统 SHALL 将每个合法 `frame_request` 绑定到一个真实帧。`reuse` 必须复用真实候选帧；`new_capture` 优先在容差内匹配候选帧，否则用 FFmpeg 按精准时间戳补截。

#### Scenario: 容差内匹配候选帧
- **WHEN** 请求时间戳 `633.8`，候选帧时间戳 `633.5`，容差 `2.0`
- **THEN** 绑定 `633.5` 的候选帧，不补截

#### Scenario: 无合适候选帧
- **WHEN** 请求时间戳 `633.8`，最近候选帧时间差大于 `2.0`
- **THEN** FFmpeg 按 `633.8` 补截

#### Scenario: 补截失败
- **WHEN** FFmpeg 无法在请求时间戳截帧
- **THEN** 删除该锚点并记录 skipped frame

### Requirement: VisionFrameEvaluator 验图
系统 SHALL 对每个绑定的帧调用视觉模型，输出 `retake`、`retake_timestamp`、`keep_image`、`content_type`、`caption`、`text_representation`。

#### Scenario: keep_image=true
- **WHEN** 视觉模型判定图片有教学价值
- **THEN** 最终 Markdown 保留该图片，并按字段追加 caption 与 text_representation

#### Scenario: keep_image=false
- **WHEN** 视觉模型判定图片无价值
- **THEN** 最终 Markdown 删除对应锚点，不保留文字替代

#### Scenario: retake=true
- **WHEN** 视觉模型要求重新截帧
- **THEN** FFmpeg 按 `retake_timestamp` 补截，新帧再次交给视觉模型，最多 `max_retakes` 次

### Requirement: 双 Markdown 输出
系统 SHALL 输出 `blog.md` 与 `srt_picture.md`。`blog.md` 的锚点按验图决策替换为图片或删除；`srt_picture.md` 保持 raw SRT 顺序，并在对应 `srt_id` 后插入保留图片。

#### Scenario: blog 锚点替换
- **WHEN** `blog_markdown` 含 `[[FRAME:a1@53.0]]` 且 `keep_image=true`
- **THEN** 输出中该锚点被替换为图片 Markdown

#### Scenario: srt_picture 插图
- **WHEN** 一个保留帧对应全局 srt_id=42
- **THEN** `srt_picture.md` 在第 42 条字幕段后插入该图片

### Requirement: 降级与可观测性
单 chunk 失败 MUST NOT 影响其他 chunk。所有非法锚点、补截失败、retake、视觉降级 SHALL 写入 `run-report.json`。

#### Scenario: 文本模型失败
- **WHEN** 某 chunk BlogGenerator 重试后仍失败
- **THEN** 该 chunk 的 blog 降级为原始字幕拼接，其他 chunk 正常生成

#### Scenario: 视觉模型失败
- **WHEN** VisionFrameEvaluator 重试后仍失败
- **THEN** 保守保留该帧并记录 fallback
