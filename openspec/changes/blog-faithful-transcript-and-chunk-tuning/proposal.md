# 提案：博客输出改为忠实润色讲稿，并支持可配置/并行的小粒度 chunk

## 问题

- 当前 `[chunking].segment_minutes = 30`，单个文本 LLM 输入过大，生成质量不稳定，且容易输出高度浓缩的“博客摘要”。
- 当前 BlogGenerator prompt 要求“整理成博客式笔记”，并且明确要求不要保留“主讲人说/他说”等引用，结果丢失讲述人语气、课程节奏和初学者需要的解释过程。
- 长视频（3-4 小时）虽然已经对 chunk 并发调用，但编排仍是“全量文本阶段 → 串行锚点绑定 → 全量视觉阶段”，单 chunk 异常会影响整批，锚点绑定（FFmpeg）也是串行。

## 目标

1. BlogGenerator 从“摘要式博客”改为“忠实润色讲稿”：只轻度润色语言，保留老师第一人称、讲解顺序、例子、强调和现场感。
2. chunk 时长继续由 `settings.toml [chunking].segment_minutes` 控制，默认改为 10 分钟，并允许小数（如 5.5）。
3. 长视频按 chunk 端到端并行处理，并发数由 `[chunking].concurrency` 控制；单个 chunk 失败只降级该 chunk。
4. 默认每个 chunk 的候选帧上限降到 20，缩短文本 prompt 和视觉验图批次。

## 非目标

- 不改变 raw SRT 的存储与 `srt_picture.md` 的生成逻辑。
- 不改变 DashScope ASR 的上传切片策略（`asr.chunk_duration` 仍独立配置）。
- 不引入逐句 LLM 转写或新增 RAG 能力。
