# 任务列表：分块批量 LLM 调用 + 双 Markdown 输出

## 前置条件

- [x] `provider_adapter.py` 已实现同步 LLM 调用
- [x] `agent_keyframe_selector.py` 已实现（旧版即将被替换）
- [x] `doc_generator.py` 已实现（旧版即将被替换）
- [x] `FFmpegHelper.extract_keyframes`（场景检测）已实现
- [x] `FFmpegHelper.capture_single_frame` 已实现
- [x] `keyframe_dedup.py`（pHash 去重）已实现
- [x] 用户已配置 DEEPSEEK_API_KEY + QWEN/SILICONFLOW API key
- [x] 已查清 Qwen3-VL-8B context = 256K tokens

---

## 任务拆解

### 阶段 1：异步 LLM 基础（2-3 小时）

#### Task 1.1：provider_adapter 加异步版本

**文件**：`framelearn/provider_adapter.py`

**子任务**：
- [x] 新增 `async def call_llm_async(prompt, config, images, max_tokens, timeout)` — 内部用 `httpx.AsyncClient`
- [x] 同步 `call_llm` 改为 thin wrapper（`asyncio.run(call_llm_async(...))`）
- [x] 新增 `async def call_llm_with_tools_async(...)` — 工具调用异步版
- [x] 单元测试（mock httpx.AsyncClient）

**验收**：
```python
import asyncio
result = asyncio.run(call_llm_async("hello", config))
assert isinstance(result, str)
```

---

#### Task 1.2：settings.toml 加新配置

**文件**：`settings.toml`

**子任务**：
- [x] 添加 `[chunking]` 段：`segment_minutes = 30`、`max_images_per_chunk = 50`、`concurrency = 5`
- [x] 添加 `[text_clean]` 段：`filler_words` 列表
- [x] 添加 `[doc_gen]` 段：`srt_filename = "srt_picture.md"`、`blog_filename = "blog.md"`
- [x] 添加 `[heuristic]` 段：`scene_threshold = 0.4`、`similarity_threshold = 0.95`

---

### 阶段 2：SRT 切段 + 文本清洗（2-3 小时）

#### Task 2.1：实现 SRTChunker

**文件**：`framelearn/pipeline/srt_chunker.py`（新文件）

**子任务**：
- [x] 定义 `SRTChunk` dataclass（`index` / `start_sec` / `end_sec` / `segments`）
- [x] 实现 `chunk(srt_segments)` 按视频时长切段
- [x] 单元测试：30 分钟视频应该切成 1 段（如果 ≤ 30 分钟）
- [x] 单元测试：60 分钟视频应该切成 2 段
- [x] 单元测试：段边界正确落在字幕段 start_sec 上

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
- [x] 加载 settings.toml 的 `[text_clean]` 配置
- [x] 实现 `async clean_chunk(chunk)` 调文本 LLM
- [x] 实现 `async clean_all(chunks)` 用 `asyncio.Semaphore(concurrency)` 限并发
- [x] 解析 LLM 返回 JSON，校验结构，失败重试
- [x] 失败降级：保留原文
- [x] 单元测试（mock LLM）

**验收**：
```python
chunks = chunker.chunk(segments)
cleaner = TextCleaner()
cleaned = asyncio.run(cleaner.clean_all(chunks))
assert len(cleaned) == len(chunks)
assert all(c.segments for c in cleaned)
```

---

### 阶段 3：启发式截帧（1 小时）

#### Task 3.0：实现 HeuristicFrameExtractor

**文件**：`framelearn/pipeline/heuristic_frame_extractor.py`（新文件）

**子任务**：
- [x] 定义 `CandidateFrame` dataclass（`path` / `timestamp_sec` / `source="heuristic"`）
- [x] 复用现有 `FFmpegHelper.extract_keyframes` 场景检测（scene=0.4）
- [x] 复用现有 `KeyframeDeduplicator`（pHash 0.95）
- [x] 输出统一格式 `CandidateFrame` 列表
- [x] 单元测试：mock ffmpeg，验证产出

**接口**：
```python
class HeuristicFrameExtractor:
    def extract(self, video_path: str, output_dir: Path) -> list[CandidateFrame]:
        """返回覆盖完整视频时长的候选帧列表"""
```

---

### 阶段 4：候选帧分配（30 分钟）

#### Task 4.1：实现 FrameDistributor

**文件**：`framelearn/pipeline/frame_distributor.py`（新文件）

**子任务**：
- [x] 实现 `distribute(chunks, frames)` 把候选帧按 timestamp_sec 分配到对应 chunk
- [x] 单 chunk 超过 `max_images_per_chunk` 时均匀采样保留
- [x] 返回 `dict[int, list[CandidateFrame]]`（chunk_index → 该 chunk 的帧）
- [x] 单元测试：边界帧（恰在 30 分钟整点）

---

### 阶段 5：视觉模型两阶段（3-4 小时）

#### Task 5.1：实现 VisionStage1（文本+图）

**文件**：`framelearn/pipeline/vision_stage1.py`（新文件）

**子任务**：
- [x] 定义 `VisionStage1Output` / `SelectedTimestamp` dataclass
- [x] 实现 `async process(chunk, frames_in_chunk)` 调视觉模型，**输入含图片**
- [x] Prompt 包含：候选帧列表（带时间戳）、cleaned SRT chunk、任务说明
- [x] 输出 JSON 解析：blog_markdown + selected_timestamps（每项含 needs_extract 标记）
- [x] 校验 selected_timestamps 数量 ≤ 50
- [x] 失败重试 2 次，失败降级：blog_markdown = cleaned SRT 拼接，selected_timestamps = 所有启发式帧（needs_extract=false）
- [x] 单元测试（mock LLM，传入测试图片）

**SelectedTimestamp 数据结构**：
```python
@dataclass
class SelectedTimestamp:
    srt_id: int
    timestamp: float              # 调整后的时间戳
    needs_extract: bool           # True = 需要 ffmpeg 截
    source_frame_path: str | None # 启发式帧路径（needs_extract=False 时）
    reason: str
```

---

#### Task 5.2：实现额外截帧逻辑

**文件**：`framelearn/pipeline/chunked_doc_generator.py` 内私有函数（无需新文件）

**子任务**：
- [x] 遍历 Stage1 输出的 selected_timestamps
- [x] 过滤 `needs_extract=True` 的项
- [x] 对每项调 `FFmpegHelper.capture_single_frame`
- [x] 输出到 `temp/frames/chunk_<i>/extra_frame_<j>.jpg`
- [x] 合并启发式帧 + 新截帧 → 该 chunk 的完整帧列表

---

#### Task 5.3：实现 VisionStage2（看图）

**文件**：`framelearn/pipeline/vision_stage2.py`（新文件）

**子任务**：
- [x] 定义 `FrameDecision` dataclass（`srt_id` / `frame_path` / `keep` / `reason`）
- [x] 实现 `async process(chunk, all_frames)` 调视觉模型，**输入是启发式 + 新截的所有帧**
- [x] 输出 JSON 解析：每张帧的 keep/discard
- [x] 失败降级：全部 keep=true
- [x] 单元测试（mock LLM）

---

### 阶段 6：Markdown 拼装（1-2 小时）

#### Task 6.1：实现 MDAssembler

**文件**：`framelearn/pipeline/md_assembler.py`（新文件）

**子任务**：
- [x] 实现 `assemble_srt(cleaned_srt, all_decisions) -> str` 输出 srt_picture.md 风格
- [x] 实现 `assemble_blog(all_blog_markdowns, all_decisions) -> str` 输出 blog.md 风格
- [x] 图片插入位置：按 `decision.srt_id` 找到对应 SRT 段，在其后插入 `![](src/frame_*.jpg)`
- [x] 时间戳格式化：`HH:MM:SS`
- [x] 文件名从 settings.toml `[doc_gen]` 读取
- [x] 单元测试：手动构造测试数据，验证插入位置

**srt_picture.md 格式**：
```markdown
# 标题

> 1. **HH:MM:SS - HH:MM:SS**  
> 字幕内容

![图片说明](src/frame_xxx.jpg)

> 2. ...
```

**blog.md 格式**：
```markdown
# 视频讲义（博客版）

## 第一节

[博客式段落...]

![代码示例](src/frame_xxx.jpg)
```

---

### 阶段 7：主流程集成（2-3 小时）

#### Task 7.1：实现 ChunkedDocGenerator

**文件**：`framelearn/pipeline/chunked_doc_generator.py`（新文件）

**子任务**：
- [x] 组织：SRTChunker → TextCleaner → HeuristicFrameExtractor → SRTChunker（再切段）→ FrameDistributor → VisionStage1 → FFmpeg 新截 → VisionStage2 → MDAssembler
- [x] 实现 `async generate(video_path, srt_segments, output_dir) -> tuple[Path, Path]`
- [x] 进度输出（每个阶段打印状态）
- [x] 错误处理：单段失败不影响其他段
- [x] 单元测试（mock 所有 LLM 和 ffmpeg）

**新流程**：
```python
async def generate(video_path, srt_segments, output_dir):
    # 1. 切段 + 清洗
    srt_chunks = SRTChunker().chunk(srt_segments)
    cleaned_chunks = await TextCleaner().clean_all(srt_chunks)
    big_cleaned_srt = concat(cleaned_chunks)
    
    # 2. 启发式截帧（全局，不分 chunk）
    heuristic_frames = HeuristicFrameExtractor().extract(video_path, output_dir / "temp")
    
    # 3. 重新切段（文本 + 帧都按同样边界）
    cleaned_chunks_again = SRTChunker().chunk(big_cleaned_srt)
    
    # 4. 分配帧到 chunk
    frames_by_chunk = FrameDistributor().distribute(cleaned_chunks_again, heuristic_frames)
    
    # 5. 每个 chunk：Stage1 + ffmpeg 新截 + Stage2
    all_decisions = {}
    all_blogs = {}
    for i, chunk in enumerate(cleaned_chunks_again):
        s1 = await VisionStage1().process(chunk, frames_by_chunk[i])
        new_frames = ffmpeg_extract_needed(s1.selected_timestamps, video_path, i)
        all_frames = frames_by_chunk[i] + new_frames
        s2 = await VisionStage2().process(chunk, all_frames)
        all_decisions[i] = s2
        all_blogs[i] = s1.blog_markdown
    
    # 6. 拼装
    srt_md = MDAssembler().assemble_srt(big_cleaned_srt, all_decisions)
    blog_md = MDAssembler().assemble_blog(all_blogs, all_decisions)
    
    return write(output_dir / "srt_picture.md", srt_md), \
           write(output_dir / "blog.md", blog_md)
```

---

#### Task 7.2：VideoPipeline 接入新流程

**文件**：`framelearn/pipeline/video_pipeline.py`

**子任务**：
- [x] 移除 `AgentKeyframeSelector` 调用
- [x] 移除旧 `DocumentGenerator` 调用
- [x] 改用 `ChunkedDocGenerator`
- [x] 更新 `PipelineResult`：`srt_picture_path` + `blog_path`
- [x] 更新 `__main__.py` 输出提示

**验收**：
```python
pipeline = VideoPipeline("test.mp4")
result = pipeline.run()
assert result.srt_picture_path.exists()
assert result.blog_path.exists()
```

---

#### Task 7.3：缓存 manifest 更新

**文件**：`framelearn/pipeline/cache_manifest.py`

**子任务**：
- [x] manifest 哈希包含 `[chunking]`、`[text_clean]`、`[doc_gen]`、`[heuristic]` 配置
- [x] manifest 哈希包含启发式截帧结果摘要（候选帧列表的 SHA256）
- [x] 删除旧的 `segments_notes/manifest.json` 和 `segments_visual_script/manifest.json` 相关代码（如果存在）
- [x] 单元测试：配置变更触发 manifest 失效

---

### 阶段 8：测试和文档（2-3 小时）

#### Task 8.1：单元测试

**文件**：
- `test/src/test_srt_chunker.py`（新）
- `test/src/test_text_cleaner.py`（新）
- `test/src/test_heuristic_frame_extractor.py`（新）
- `test/src/test_frame_distributor.py`（新）
- `test/src/test_vision_stages.py`（新）
- `test/src/test_md_assembler.py`（新）

**子任务**：
- [x] SRTChunker 边界测试
- [x] TextCleaner mock LLM
- [x] HeuristicFrameExtractor mock ffmpeg
- [x] FrameDistributor 边界帧测试
- [x] VisionStage1/2 mock LLM（Stage1 含图）
- [x] MDAssembler 拼装测试

---

#### Task 8.2：端到端测试

**文件**：`test/src/test_chunked_pipeline_e2e.py`（新）

**子任务**：
- [x] mock 所有 LLM 调用 + ffmpeg
- [x] 用真实 5 分钟短视频（如果仓库有 fixture）或 mock SRT
- [x] 验证：srt_picture.md + blog.md 都生成
- [x] 验证：图片插入位置正确
- [x] 验证：Stage1 needs_extract=true 的帧被 ffmpeg 截取

---

#### Task 8.3：更新 README

**文件**：`README.md` / `README.en.md`

**子任务**：
- [x] 输出目录结构更新（加 `srt_picture.md` / `blog.md`）
- [x] 移除 `notes.md` / `visual_script.md` 旧模式说明
- [x] 加新配置项说明（`[chunking]` / `[text_clean]` / `[doc_gen]` / `[heuristic]`）

---

#### Task 8.4：删除/废弃旧代码

**子任务**：
- [x] `agent_keyframe_selector.py` 加 deprecation 注释，不删除（向后兼容）
- [x] `doc_generator.py` 中 `notes` / `visual_script` mode 加 deprecation 注释
- [x] `keyframe_dedup.py` 保留（被 HeuristicFrameExtractor 复用）

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
                 │                     ├─→ 7.1 (ChunkedDocGen) ──→ 7.2 (VideoPipeline)
                 │                     │
                 ├─→ 3.0 (启发式截帧) ──┤
                 │                     │
                 │                     ├─→ 4.1 (FrameDistributor)
                 │                     │
                 │                     ├─→ 5.1 (VisionStage1 文本+图)
                 │                     │
                 │                     ├─→ 5.3 (VisionStage2 看图)
                 │                     │
                 │                     ├─→ 6.1 (MDAssembler)
                 │                     │
                 │                     └─→ 7.3 (缓存)
                 │
                 └─→ 8.1 (单测) ──→ 8.2 (e2e) ──→ 8.3 (README) ──→ 8.4 (清理)
```

---

## 完成标准

- [x] 30 分钟视频完整跑通，LLM 调用 = 3 次（1 文本清洗 + 1 视觉文本+图 + 1 视觉看图）
- [x] 启发式截帧可缓存（同视频第二次跳过 ffmpeg 场景检测）
- [x] `srt_picture.md` 保留 SRT 原结构 + 正确插入图片
- [x] `blog.md` 是博客式叙述 + 同样的图片
- [x] Stage1 能正确输出 `needs_extract=true` 的新时间戳
- [x] ffmpeg 精准截取 Stage1 新增的时间戳
- [x] 60 分钟视频切 2 段，每段独立处理
- [x] 单段失败不影响其他段
- [x] 旧 `notes.md` / `visual_script.md` 不再生成
- [x] 所有单测通过
- [x] README 更新

---

## 预计工作量

| 阶段 | 时间 |
|------|------|
| 1 | 2-3 小时 |
| 2 | 2-3 小时 |
| 3 | 1 小时 |
| 4 | 0.5 小时 |
| 5 | 3-4 小时 |
| 6 | 1-2 小时 |
| 7 | 2-3 小时 |
| 8 | 2-3 小时 |
| **总计** | **14-19 小时** |
