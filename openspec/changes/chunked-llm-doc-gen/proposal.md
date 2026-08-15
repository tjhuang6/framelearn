# 提案：分块批量 LLM 调用 + 双 Markdown 输出

## 问题

当前 `agent_keyframe_selector.py` + `doc_generator.py` 对 30 分钟视频发出 **~270 次 LLM 调用**：

| 阶段 | 模型 | 调用次数 |
|------|------|---------|
| 启发式过滤 | （无） | 0 |
| 文本决策（每候选段 1 次） | DeepSeek | ~60-100 |
| 视觉评估（工具循环 1-5 次/帧） | Qwen3-VL-8B | ~55-275 |
| 文档生成（每段 notes + visual_script） | Qwen3-VL-8B | 38 |
| **总计** | | **~270** |

四个主要问题：

1. **慢** — 调用串行执行，30 分钟视频常 30+ 分钟
2. **贵** — 270 次调用 × 输入 token，浪费大量费用
3. **输出质量差** — `visual_script.md` 把 SRT 重写成"老师说..."叙事，丢失源 SRT 结构
4. **没利用模型能力** — Qwen3-VL-8B 有 256K context，但当前 `call_llm` 同步逐段调用，吃不满

## 目标

用 **分块批量** 替代逐段循环，把 30 分钟视频的 LLM 调用从 ~270 降到 **3**（文本清洗 1 + 视觉纯文本 1 + 视觉看图 1）。

最终输出 **两个 Markdown**：

- **`output_a.md`** — cleaned SRT 原结构 + 在对应 SRT 行后插入 `![]()` 图片
- **`output_b.md`** — 视觉模型生成的博客式叙述 + 同样的图片

## 处理流水线

```
原始 SRT (608 段)
   ↓ 按 30 分钟切段（视频时长维度）
[文本 LLM × N] 每段独立清洗口水词 → cleaned chunks（可并行）
   ↓ 拼接
大 cleaned SRT
   ↓ 按 30 分钟切段（同样边界，便于协调）
For each chunk:
   [视觉 LLM Call A：纯文本]
     输入：cleaned SRT chunk
     输出：
       ├─ 博客 markdown（这一段，无图）
       └─ 候选时间戳（最多 50 个）[{srt_id, timestamp, reason}]
   ffmpeg 截最多 50 个时间戳 → frames/
   [视觉 LLM Call B：看图]
     输入：cleaned SRT chunk + 最多 50 张帧
     输出：每张 keep / discard
   ↓
全部 chunk 完成（程序化拼装）：
   ├─ 各段博客 markdown 拼接 → Markdown B（博客式）
   └─ 按 srt_id 把保留图插入大 cleaned SRT → Markdown A（SRT 式）
```

## 关键约束

- **段大小**：30 分钟视频时长（settings.toml 配置）
- **每批图片数**：50 张（视觉模型看图上限）
- **视觉模型 context**：Qwen3-VL-8B = 256K
  - 30 分钟 SRT ≈ 50K tokens
  - 50 张图 × 1500 tokens = 75K tokens
  - 合计 ~125K，远低于 256K

## 技术选型

| 环节 | 选型 | 理由 |
|------|------|------|
| 文本清洗 | DeepSeek / 任何文本 LLM | 纯文本任务，选最便宜的 |
| 视觉分析 | Qwen3-VL-8B（已用） | 256K context 装得下整段 |
| 截帧 | FFmpeg `capture_single_frame` | 已实现 |
| 并发 | asyncio + httpx.AsyncClient | 不引入额外依赖 |

## settings.toml 新增

```toml
[chunking]
segment_minutes = 30           # 每段视频时长
max_images_per_chunk = 50      # 每批评估图片数

[doc_gen]
output_a_filename = "output_a.md"   # SRT 式 + 插图
output_b_filename = "output_b.md"   # 博客式 + 插图
```

## 不做的事

- 不保留 `notes.md` / `visual_script.md` 旧双模式 — 只生成新的 `output_a.md` + `output_b.md`
- 不引入 LiteLLM / 官方 SDK — 继续用现有 `provider_adapter.py`，把同步实现换成异步
- 不改 ASR、不改 FFmpeg 场景抽帧 — 只改 LLM 调用方式
- 不实现流式输出 — 离线批处理场景不需要

## 风险与对策

| 风险 | 对策 |
|------|------|
| 单次大调用失败影响更多输出 | 段级重试（最多 2 次）；失败则降级到程序化生成（保留 SRT 原样 + 不插图） |
| 视觉模型选时间戳不准确 | prompt 给硬关键词下限（"看"/"如图"/"代码"等），模型补上限 |
| 256K context 装不下极长视频 | 段大小可配置，>30 分钟自动切多段 |
| 文本 LLM 改写过猛 | prompt 加约束："只删口水词，不重组句序，不删内容词" |
| 异步改造引入并发 bug | `asyncio.Semaphore` 限并发（默认 5），保留同步 fallback |

## 工作量估算

| 阶段 | 时间 |
|------|------|
| 1. SRT 切段 + 异步 LLM 适配 | 2-3 小时 |
| 2. 视觉模型两阶段实现 | 3-4 小时 |
| 3. Markdown 拼装 | 1-2 小时 |
| 4. 替换 VideoPipeline 主流程 | 2-3 小时 |
| 5. 测试 | 2-3 小时 |
| **总计** | **10-15 小时** |
