# 任务列表：分块批量 LLM 调用 + 双 Markdown 输出

## 前置条件

- [x] `provider_adapter.py` 已实现同步 LLM 调用
- [x] `agent_keyframe_selector.py` 已实现（旧版即将被替换）
- [x] `doc_generator.py` 已实现（旧版即将被替换）
- [x] `FFmpegHelper.capture_single_frame` 已实现
- [x] 用户已配置 DEEPSEEK_API_KEY + QWEN/SILICONFLOW API key
- [x] 已查清 Qwen3-VL-8B context = 256K tokens

---

## 任务拆解

### 阶段 1：异步 LLM 基础（2-3 小时）

#### Task 1.1：provider_adapter 加异步版本

**文件**：`framelearn/provider_adapter.py`

**子任务**：
- [ ] 新增 `async def call_llm_async(prompt, config, images, max_tokens, timeout)` — 内部用 `httpx.AsyncClient`
- [ ] 同步 `call_llm` 改为 thin wrapper（`asyncio.run(call_llm_async(...))`）
- [ ] 新增 `async def call_llm_with_tools_async(...)` — 工具调用异步版
- [ ] 单元测试（mock httpx.AsyncClient）

**验收**：
```python
import asyncio
result = asyncio.run(call_llm_async("hello", config))
assert isinstance(result, str)
```

---

#### Task 1.2：settings.toml 加 chunking 配置

**文件**：`settings.toml`

**子任务**：
- [ ] 添加 `[chunking]` 段：`segment_minutes = 30`、`max_images_per_chunk = 50`
- [ ] 添加 `[doc_gen]` 段：`output_a_filename`、`output_b_filename`、`concurrency = 5`
- [ ] 添加 `[text_clean]` 段：`filler_words` 列表

---

### 阶段 2：SRT 切段 + 文本清洗（2-3 小时）

#### Task 2.1：实现 SRTChunker

**文件**：`framelearn/pipeline/srt_chunker.py`（新文件）

**子任务**：
- [ ] 定义 `SRTChunk` dataclass（`index` / `start_sec` / `end_sec` / `segments`）
- [ ] 实现 `chunk(srt_segments)` 按视频时长切段
- [ ] 单元测试：30 分钟视频应该切成 1 段（如果 ≤ 30 分钟）
- [ ] 单元测试：60 分钟视频应该切成 2 段
- [ ] 单元测试：段边界正确落在字幕段 start_sec 上

**接口**：
```python
class SRTChunker:
    def __init__(self, segment_minutes: int = 30): ...
    def chunk(self, srt_segments: list[TranscriptSegment]) -> list[SRTChunk]: ...
```

---

#### Task 2.2：实现 TextCleaner

**文件**：`framelearn/pipeline/text_cleaner.py`（新文件）

**子任务**：
- [ ] 加载 settings.toml 的 `[text_clean]` 配置
- [ ] 实现 `async clean_chunk(chunk)` 调文本 LLM
- [ ] 实现 `async clean_all(chunks)` 用 `asyncio.Semaphore(5)` 限并发
- [ ] 解析 LLM 返回 JSON，校验结构，失败重试
- [ ] 失败降级：保留原文
- [ ] 单元测试（mock LLM）

**验收**：
```python
chunks = chunker.chunk(segments)
cleaner = TextCleaner()
cleaned = asyncio.run(cleaner.clean_all(chunks))
assert len(cleaned) == len(chunks)
assert all(c.segments for c in cleaned)
```

---

### 阶段 3：视觉模型两阶段（3-4 小时）

#### Task 3.1：实现 VisionStage1（纯文本）

**文件**：`framelearn/pipeline/vision_stage1.py`（新文件）

**子任务**：
- [ ] 定义 `VisionStage1Output` / `CandidateTimestamp` dataclass
- [ ] 实现 `async process(chunk)` 调视觉模型纯文本模式
- [ ] Prompt 含硬下限关键词（"看"、"如图"、"代码"等）
- [ ] 解析 JSON 输出，校验候选时间戳数 ≤ 50
- [ ] 失败重试 2 次，失败降级：用 cleaned SRT 替代博客 markdown，无候选
- [ ] 单元测试（mock LLM）

---

#### Task 3.2：实现 VisionStage2（看图）

**文件**：`framelearn/pipeline/vision_stage2.py`（新文件）

**子任务**：
- [ ] 定义 `FrameDecision` dataclass
- [ ] 实现 `async process(chunk, candidates, frames_dir)` 调视觉模型看图模式
- [ ] 输入构造：cleaned SRT chunk + 帧路径列表（≤ 50）
- [ ] 输出 JSON 解析，逐帧 keep/discard
- [ ] 失败降级：全部 keep=true
- [ ] 单元测试（mock LLM，传入测试图片）

**验收**：
```python
candidates = [CandidateTimestamp(srt_id=42, timestamp=90.5, reason="...")]
decisions = await stage2.process(chunk, candidates, frames_dir)
assert len(decisions) == len(candidates)
assert all(d.keep in (True, False) for d in decisions)
```

---

### 阶段 4：Markdown 拼装（1-2 小时）

#### Task 4.1：实现 MDAssembler

**文件**：`framelearn/pipeline/md_assembler.py`（新文件）

**子任务**：
- [ ] 实现 `assemble_a(cleaned_srt, decisions)` — SRT 式 markdown
- [ ] 实现 `assemble_b(blog_markdowns, decisions)` — 博客式 markdown
- [ ] 图片插入位置：按 `decision.srt_id` 找到对应 SRT 段，在其后插入 `![](src/frame_*.jpg)`
- [ ] 时间戳格式化：`HH:MM:SS`
- [ ] 单元测试：手动构造测试数据，验证插入位置

**Markdown A 格式**：
```markdown
# 标题

> 1. **HH:MM:SS - HH:MM:SS**  
> 字幕内容

![图片说明](src/frame_xxx.jpg)

> 2. ...
```

---

### 阶段 5：主流程集成（2-3 小时）

#### Task 5.1：实现 ChunkedDocGenerator

**文件**：`framelearn/pipeline/chunked_doc_generator.py`（新文件）

**子任务**：
- [ ] 组织 SRTChunker + TextCleaner + VisionStage1 + ffmpeg + VisionStage2 + MDAssembler
- [ ] 实现 `async generate(video_path, srt_segments, output_dir) -> tuple[Path, Path]`
- [ ] 调用流程：chunk → clean → stage1 → ffmpeg → stage2 → assemble
- [ ] 进度输出（每段打印状态）
- [ ] 错误处理：单段失败不影响其他段
- [ ] 单元测试（mock 所有 LLM 和 ffmpeg）

---

#### Task 5.2：VideoPipeline 接入新流程

**文件**：`framelearn/pipeline/video_pipeline.py`

**子任务**：
- [ ] 移除 `AgentKeyframeSelector` 调用
- [ ] 移除旧 `DocumentGenerator` 调用
- [ ] 改用 `ChunkedDocGenerator`
- [ ] 更新 `PipelineResult`：`markdown_a_path` + `markdown_b_path`
- [ ] 更新 `__main__.py` 输出提示

**验收**：
```python
pipeline = VideoPipeline("test.mp4")
result = pipeline.run()
assert result.markdown_a_path.exists()
assert result.markdown_b_path.exists()
```

---

#### Task 5.3：缓存 manifest 更新

**文件**：`framelearn/pipeline/cache_manifest.py`

**子任务**：
- [ ] manifest 哈希包含 `[chunking]`、`[text_clean]`、`[doc_gen]` 配置
- [ ] 删除旧的 `segments_notes/manifest.json` 和 `segments_visual_script/manifest.json` 相关代码（如果存在）
- [ ] 单元测试：配置变更触发 manifest 失效

---

### 阶段 6：测试和文档（2-3 小时）

#### Task 6.1：单元测试

**文件**：
- `test/src/test_srt_chunker.py`（新）
- `test/src/test_text_cleaner.py`（新）
- `test/src/test_vision_stages.py`（新）
- `test/src/test_md_assembler.py`（新）

**子任务**：
- [ ] SRTChunker 边界测试
- [ ] TextCleaner mock LLM
- [ ] VisionStage1/2 mock LLM
- [ ] MDAssembler 拼装测试

---

#### Task 6.2：端到端测试

**文件**：`test/src/test_chunked_pipeline_e2e.py`（新）

**子任务**：
- [ ] mock 所有 LLM 调用
- [ ] 用真实 5 分钟短视频（如果仓库有 fixture）或 mock SRT
- [ ] 验证：output_a.md + output_b.md 都生成
- [ ] 验证：图片插入位置正确

---

#### Task 6.3：更新 README

**文件**：`README.md` / `README.en.md`

**子任务**：
- [ ] 输出目录结构更新（加 `output_a.md` / `output_b.md`）
- [ ] 移除 `notes.md` / `visual_script.md` 旧模式说明
- [ ] 加新配置项说明（`[chunking]` / `[doc_gen]`）

---

#### Task 6.4：删除/废弃旧代码

**子任务**：
- [ ] `agent_keyframe_selector.py` 加 deprecation 注释，不删除（向后兼容）
- [ ] `doc_generator.py` 中 `notes` / `visual_script` mode 加 deprecation 注释
- [ ] `keyframe_dedup.py` 保留（仍用于 ASR 后的初始抽帧去重，但不再被 LLM 决策）

---

## 任务依赖图

```
1.1 (异步 LLM)
   ↓
1.2 (配置) ──┐
             │
2.1 (SRT 切段) ──┐
                 ├─→ 2.2 (文本清洗) ──┐
                 │                     │
                 │                     ├─→ 5.1 (ChunkedDocGen) ──→ 5.2 (VideoPipeline)
                 │                     │
                 ├─→ 3.1 (VisionStage1) ┤
                 │                     │
                 │                     ├─→ 3.2 (VisionStage2)
                 │                     │
                 │                     ├─→ 4.1 (MDAssembler)
                 │                     │
                 │                     └─→ 5.3 (缓存)
                 │
                 └─→ 6.1 (单测) ──→ 6.2 (e2e) ──→ 6.3 (README) ──→ 6.4 (清理)
```

---

## 完成标准

- [ ] 30 分钟视频完整跑通，LLM 调用 ≤ 5 次（1 文本 + 2 视觉 × N 段，N=1）
- [ ] `output_a.md` 保留 SRT 原结构 + 正确插入图片
- [ ] `output_b.md` 是博客式叙述 + 同样的图片
- [ ] 60 分钟视频切 2 段，每段独立处理
- [ ] 单段失败不影响其他段
- [ ] 旧 `notes.md` / `visual_script.md` 不再生成
- [ ] 所有单测通过
- [ ] README 更新

---

## 预计工作量

| 阶段 | 时间 |
|------|------|
| 1 | 2-3 小时 |
| 2 | 2-3 小时 |
| 3 | 3-4 小时 |
| 4 | 1-2 小时 |
| 5 | 2-3 小时 |
| 6 | 2-3 小时 |
| **总计** | **12-18 小时** |
