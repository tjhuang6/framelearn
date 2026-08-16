# 提案：博客锚点流水线（blog-anchor-pipeline）

## 问题

当前 `chunked_doc_generator.py` 让**视频模型**承担两份工作：

1. 阅读整段 cleaned SRT + 候选图，生成博客长文；
2. 同时输出帧的保留/删除/补截决策。

这导致：

- 博客长文用 vision token 生成，成本高、速度慢；
- Stage1 输出 schema 同时包含长文和多条时间戳决策，解析不稳定；
- 文本模型只做“删口水词”，没有发挥它生成博客长文的能力；
- Stage1 必须看到真实图片路径，因此文本生成和 FFmpeg 截帧无法并行。

## 目标

按照 `0816.md` 对齐后的流程，把职责拆开：

- **FFmpeg**：生成启发式候选帧（只依赖视频，可与 ASR 并行）；
- **程序**：先按时长切 chunk，再把候选帧标记插入每个 chunk（不覆盖 raw SRT）；
- **文本模型 BlogGenerator**：阅读带帧标记的 annotated SRT chunk，生成博客文本，并在文本中输出 `[[FRAME:<anchor_id>@<timestamp>]]` 锚点和 `frame_requests`；
- **程序**：校验锚点，把已有的候选帧绑定到锚点；没有合适帧时用 FFmpeg 按精准时间戳补截；非法锚点删除并记录；
- **视频模型 VisionFrameEvaluator**：只看少量候选帧，输出 `retake` / `keep_image` / `content_type` / `caption` / `text_representation`；
- **MDAssembler**：按决策替换锚点并输出 `blog.md`；`srt_picture.md` 继续由原始 SRT 结构 + 保留帧生成。

## 关键决策

1. **切块顺序采用方案 A**：先用 raw SRT 按时长切 chunk，再向每个 chunk 插入候选帧标记。SRTChunker 不处理图片标记行。
2. **不覆盖 raw SRT**：程序生成 annotated SRT-MD 仅作为 BlogGenerator 输入，raw SRT 仍用于缓存和 `srt_picture.md`。
3. **文本模型只能引用真实候选帧路径**；请求新时间戳通过 `frame_requests` 表达，由程序校验合法性。
4. **已有帧的时间戳不可被修改**。若文本模型想换时间点，必须请求 `new_capture`。
5. **视觉模型只做验图**，不生成博客长文。
6. **`keep_image=true` 时图片一定保留**；`caption` / `text_representation` 只是图片下方的补充内容。`keep_image=false` 时删除整个锚点。

## 处理流水线

```text
video
  │
  ├── ASR ──→ raw SRT ────────────────────────┐
  │                                            │
  └── FFmpeg 启发式截帧 ───────────────────────┤
                                               ▼
                                        程序：SRTChunker 先切 chunk
                                               │
                                               ▼
                                        程序：向 chunk 插入候选帧标记
                                               │
                                               ▼
                                        BlogGenerator（文本模型）
                                               │
                                               ▼
                                        程序校验锚点 / FFmpeg 补截
                                               │
                                               ▼
                                        VisionFrameEvaluator（视频模型）
                                        支持 retake 循环
                                               │
                                               ▼
                                          MDAssembler
```

## 输出

- `blog.md`：文本模型生成的博客文本，锚点替换为保留图片或删除。
- `srt_picture.md`：保持 raw SRT 顺序结构，在对应 `srt_id` 后插入保留图片。
- `run-report.json`：记录非法锚点、补截失败、retake 次数、视觉降级等事件。

## 非目标

- 本版本不实现“删除图片但保留文字”的替代逻辑；`keep_image=false` 一律删除锚点。
- 不合并 TextCleaner 与 BlogGenerator；当前版本直接使用 raw SRT 结构生成 `srt_picture.md`。
- 不实现流式输出。
- 不引入 OCR 专用模型。

## 风险与对策

| 风险 | 对策 |
|------|------|
| 文本模型伪造 `anchor_id` / 时间戳 / 候选路径 | 程序只接受与 `frame_requests` 一致的锚点；非法锚点删除并记录 |
| 文本模型重排语序后锚点错位 | 锚点由模型生成时写入对应句子，程序按 `anchor_id` 替换，不按时间排序 |
| 候选帧时间差过大 | 时间差 > 容差时 FFmpeg 按精准时间戳补截 |
| 视频模型判断要 retake | 最多重试 `blog_gen.max_retakes` 次，超限按保守策略保留或丢弃 |
| BlogGenerator 失败 | 单 chunk 降级为原始字幕拼接，不中断其他 chunk |
| 视觉模型失败 | 保守保留该帧，写入 run-report |
