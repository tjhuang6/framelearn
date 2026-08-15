# chunked-doc-gen Specification

## Purpose
为长视频（>30 分钟）的字幕-关键帧-文档生成提供分块批量处理能力：通过把完整 SRT 切分为固定时长的段，并对每段执行 1 次文本清洗 + 1 次纯文本视觉模型调用 + 1 次看图视觉模型调用，把单视频的 LLM 调用次数从 ~270 降到 O(段数)，同时支持双 Markdown 输出（SRT 式 + 博客式）。

## ADDED Requirements
### Requirement: SRT 按视频时长切段
系统 SHALL 提供 SRTChunker，把 `list[TranscriptSegment]` 按配置的视频时长（默认 30 分钟）切分为 `list[SRTChunk]`。切段边界 MUST 落在某条字幕段的 `start_sec` 上（不在字幕段中间断开）。最后一段 MAY 短于配置时长。

#### Scenario: 30 分钟视频切 1 段
- **WHEN** 输入 30 分钟视频对应的 SRT
- **THEN** SRTChunker 输出 1 个 SRTChunk，包含全部字幕段

#### Scenario: 60 分钟视频切 2 段
- **WHEN** 输入 60 分钟视频对应的 SRT
- **THEN** SRTChunker 输出 2 个 SRTChunk，第 1 段覆盖 [0, 30 分钟) 的字幕段，第 2 段覆盖 [30 分钟, 60 分钟)

#### Scenario: 段边界落在字幕段 start_sec 上
- **WHEN** 切段边界附近有连续字幕段
- **THEN** 边界处不会出现"半条字幕段被切走"的情况；整条字幕段完整属于某一段

### Requirement: 文本 LLM 批量清洗口水词
系统 SHALL 提供 TextCleaner，对每个 SRTChunk 调一次文本 LLM，删除指定口水词（默认 ["那么", "就是说", "大家注意", "咱们", "啊", "嗯", "这个", "那个"]），但 MUST 保持字幕段的 id 和时间戳不变，ONLY 修改 text 内容。多个段 SHALL 并发处理（受 `concurrency` 配置限制）。

#### Scenario: 清洗单段
- **WHEN** TextCleaner.clean_chunk() 被调用
- **THEN** 返回的 SRTChunk 与输入有相同的 segments 数量，每个 segment 的 id 和 start_sec/end_sec 不变，text 内容已去除口水词

#### Scenario: 多段并发清洗
- **WHEN** TextCleaner.clean_all() 被调用且 chunk 数 > concurrency 配置
- **THEN** 任意时刻最多有 `concurrency` 个 LLM 请求在飞

#### Scenario: 单段失败降级
- **WHEN** 某段 LLM 调用重试 2 次仍失败
- **THEN** 系统保留该段原文（不清洗），不影响其他段，记录 warning 日志

### Requirement: 视觉模型纯文本阶段（Stage1）
系统 SHALL 提供 VisionStage1，对每个 SRTChunk 调用视觉模型（纯文本模式，无图片输入），返回两个输出：(1) 该段的博客式 Markdown 段落；(2) 最多 50 个候选时间戳（`CandidateTimestamp`），每个含 `srt_id` / `timestamp` / `reason`。Prompt MUST 包含硬下限关键词规则（"看"/"如图"/"代码"等）确保召回。

#### Scenario: Stage1 输出格式
- **WHEN** VisionStage1.process(chunk) 被调用
- **THEN** 返回 VisionStage1Output，含 blog_markdown（字符串）和 candidate_timestamps（≤ 50 个）

#### Scenario: Stage1 失败降级
- **WHEN** Stage1 LLM 调用重试 2 次仍失败
- **THEN** blog_markdown 降级为该段 cleaned SRT 的纯文本拼接（不带时间戳），candidate_timestamps 为空列表

### Requirement: FFmpeg 在候选时间戳处精准截帧
系统 SHALL 在每个候选时间戳处用 FFmpeg 精准截取单帧图像（`capture_single_frame`），每段最多 50 张。截取的帧 SHALL 保存到 `temp/frames/chunk_<i>/frame_<j>.jpg`。

#### Scenario: 截帧成功
- **WHEN** 提供 video_path 和 timestamp（秒）
- **THEN** FFmpegHelper.capture_single_frame() 输出对应秒数的 jpg 文件

#### Scenario: 单帧截取失败
- **WHEN** FFmpeg 在某 timestamp 报错
- **THEN** 系统跳过该时间戳，不影响其他帧

### Requirement: 视觉模型看图阶段（Stage2）
系统 SHALL 提供 VisionStage2，对每个 SRTChunk 调视觉模型（看图模式），输入为 (cleaned SRT chunk + 最多 50 张候选帧 + 候选时间戳信息)，输出为每张帧的 `FrameDecision { keep: bool, reason: str }`。

#### Scenario: Stage2 输出格式
- **WHEN** VisionStage2.process(chunk, candidates, frames_dir) 被调用
- **THEN** 返回 list[FrameDecision]，长度等于输入 candidates 长度，每个 decision 有 keep 字段

#### Scenario: Stage2 失败降级
- **WHEN** Stage2 LLM 调用重试 2 次仍失败
- **THEN** 所有候选帧 keep=true（保守策略：保留全部）

### Requirement: 双 Markdown 输出
系统 SHALL 输出两个 Markdown 文件：
- `output_a.md`：cleaned SRT 原结构 + 在每个 kept 帧对应 srt_id 后插入 `![](src/frame_xxx.jpg)` 引用
- `output_b.md`：各段博客 markdown 拼接 + 同样的图片引用插入

#### Scenario: Markdown A 保留 SRT 结构
- **WHEN** 输出 output_a.md
- **THEN** 每条字幕段以带时间戳的引用块（`>`）呈现，顺序与原 SRT 一致

#### Scenario: Markdown A 图片插入位置
- **WHEN** 某 FrameDecision.keep=true 且 decision.srt_id=42
- **THEN** output_a.md 中 id=42 的字幕段之后插入对应图片引用

#### Scenario: Markdown B 包含博客内容
- **WHEN** 输出 output_b.md
- **THEN** 文件包含各段 blog_markdown 拼接的内容，图片插入位置与 Markdown A 一致

### Requirement: 段级并行与错误隔离
系统 SHALL 让 N 个 chunk 的文本清洗和视觉模型调用并发执行，但任一段失败 MUST NOT 影响其他段。每个 LLM 调用 SHALL 有指数退避重试（最多 2 次重试）。

#### Scenario: 单段失败不影响整体
- **WHEN** 第 3 段文本清洗失败
- **THEN** 第 1、2、4 段正常完成，pipeline 最终仍输出两个 Markdown（可能缺失第 3 段内容）

#### Scenario: 重试机制
- **WHEN** LLM 调用返回 429 或 5xx
- **THEN** 系统按指数退避重试（1s、2s、4s），最多 2 次，第 3 次失败触发降级

### Requirement: 配置驱动
段大小（`segment_minutes`）、每批图片数（`max_images_per_chunk`）、并发数（`concurrency`）、口水词清单（`filler_words`）MUST 全部从 settings.toml 读取，不可硬编码。配置变更 MUST 触发缓存失效。

#### Scenario: 默认配置
- **WHEN** settings.toml 缺少 `[chunking]` / `[text_clean]` / `[doc_gen]` 段
- **THEN** 系统使用默认值：segment_minutes=30, max_images_per_chunk=50, concurrency=5, filler_words=[内置清单]

#### Scenario: 配置变更触发重跑
- **WHEN** settings.toml 中 segment_minutes 从 30 改为 15
- **THEN** 缓存 manifest 哈希变化，下次 run 完整重跑

### Requirement: 缓存 manifest
系统 SHALL 在 output_dir 写 `manifest.json`，SHA256 哈希包含：视频文件、SRT 内容、`[chunking]` 配置、`[text_clean]` 配置、`[doc_gen]` 配置、`[vision]` 配置。任一变化 MUST 触发完整重跑。

#### Scenario: manifest 哈希稳定
- **WHEN** 同一视频同一配置运行两次
- **THEN** 两次 manifest 哈希相同，第二次跳过实际工作（直接复用输出）

#### Scenario: SRT 变更触发重跑
- **WHEN** 视频相同但 SRT 内容变化（重新 ASR）
- **THEN** manifest 哈希变化，重新跑文本清洗 + 后续阶段
