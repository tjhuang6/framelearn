# 设计：分块批量 LLM 调用 + 双 Markdown 输出

## 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│           framelearn run <video> [--subtitle <srt>]          │
└────────────────────────────┬─────────────────────────────────┘
                             ↓
                  ┌──────────────────────┐
                  │   VideoPipeline      │
                  └──────────┬───────────┘
                             ↓
              ┌──────────────┼──────────────┐
              ↓              ↓              ↓
         ┌────────┐   ┌──────────┐   ┌────────────────┐
         │ ASR    │   │ FFmpeg   │   │ ChunkedDocGen  │
         │ Adapter│   │ Helper   │   │ (新模块)       │
         └────────┘   └──────────┘   └────────────────┘
              │              │              │
              ↓              ↓              ↓
         subtitle.srt   frame_<HH>.jpg    ┌──────────────┐
                                          │ SRTChunker   │
                                          │  (按 30 分钟) │
                                          └──────┬───────┘
                                                 ↓
                                          ┌──────────────┐
                                          │ TextCleaner  │
                                          │ (并行 N 段)  │
                                          └──────┬───────┘
                                                 ↓
                                          ┌──────────────┐
                                          │ VisionStage1 │
                                          │ (纯文本，N 段)│
                                          │ 输出 md + ts │
                                          └──────┬───────┘
                                                 ↓
                                          ┌──────────────┐
                                          │ FFmpeg.extract│
                                          │  截 ≤50 帧   │
                                          └──────┬───────┘
                                                 ↓
                                          ┌──────────────┐
                                          │ VisionStage2 │
                                          │ (看图，N 段) │
                                          │ 输出 keep    │
                                          └──────┬───────┘
                                                 ↓
                                          ┌──────────────┐
                                          │ MDAssembler  │
                                          │ output_a.md  │
                                          │ output_b.md  │
                                          └──────────────┘
```

---

## 模块拆分

### 1. SRTChunker（新增）

**文件**：`framelearn/pipeline/srt_chunker.py`

**职责**：把完整 SRT 按视频时长切段，每段固定 N 分钟。

**接口**：
```python
@dataclass
class SRTChunk:
    index: int                # 第几段（0-based）
    start_sec: float          # 段起始视频时间
    end_sec: float            # 段结束视频时间
    segments: list[TranscriptSegment]  # 该段内的字幕段

class SRTChunker:
    def __init__(self, segment_minutes: int = 30):
        self.segment_minutes = segment_minutes

    def chunk(self, srt_segments: list[TranscriptSegment]) -> list[SRTChunk]:
        """按视频时长切段。段边界落在字幕段的 start_sec 上。"""
```

**算法**：
1. 按 `seg.start_sec` 升序遍历
2. 当 `seg.start_sec >= (chunk_idx + 1) * segment_minutes * 60` 时开新段
3. 最后一段可能短于 segment_minutes（剩余视频）

---

### 2. TextCleaner（新增）

**文件**：`framelearn/pipeline/text_cleaner.py`

**职责**：用文本 LLM 清洗每段 SRT 的口水词，保持结构不变。

**接口**：
```python
class TextCleaner:
    def __init__(self, provider: str = "deepseek", model: str = "deepseek-chat"):
        ...

    async def clean_chunk(self, chunk: SRTChunk) -> SRTChunk:
        """清洗单段 SRT，返回新的 SRTChunk（segments 内容已清洗）"""

    async def clean_all(self, chunks: list[SRTChunk]) -> list[SRTChunk]:
        """并行清洗所有段（asyncio.Semaphore 限并发）"""
```

**Prompt 约束**（关键）：
```
你是字幕清洗助手，只删口水词，不重组句序，不删内容词。

口水词清单：那么、就是说、大家注意、咱们、啊、嗯、这个、那个、对吧

输入 SRT 段（保持 id 和时间戳不变，只改 text）：
<subtitle>
{chunk_text}
</subtitle>

输出 JSON：
{"segments": [{"id": 1, "text": "..."}, ...]}
```

**并发**：`asyncio.Semaphore(5)`，失败重试 2 次（指数退避），第 3 次失败抛错。

---

### 3. VisionStage1（新增）

**文件**：`framelearn/pipeline/vision_stage1.py`

**职责**：纯文本视觉模型调用，输出博客 markdown 和候选时间戳。

**接口**：
```python
@dataclass
class VisionStage1Output:
    blog_markdown: str                # 这一段的博客式 markdown
    candidate_timestamps: list[CandidateTimestamp]

@dataclass
class CandidateTimestamp:
    srt_id: int                       # 插在哪个 SRT 段之后
    timestamp: float                  # 视频时间戳（秒）
    reason: str                       # 为什么这里要截图

class VisionStage1:
    async def process(self, chunk: SRTChunk) -> VisionStage1Output:
        """纯文本调用，返回博客 markdown + 候选时间戳"""
```

**Prompt 模板**（关键约束）：
```
你是视频字幕整理助手。给你一段清洗过的 SRT，请做两件事：

1. 生成博客式 markdown 段落（每条 SRT 合并成连贯叙述，去掉时间戳和序号）
2. 选出 ≤50 个候选时间戳，建议插入图片的位置

候选时间戳选择规则（硬下限）：
- 出现"看"、"如图"、"图中"、"屏幕"、"代码"、"演示"、"PPT"等关键词的段必选
- 在此之上自由发挥：识别需要视觉辅助讲解的概念

输入：
<subtitle>
{chunk_text}
</subtitle>

输出 JSON：
{
  "blog_markdown": "## ...\\n\\n这是博客式段落...",
  "candidates": [{"srt_id": 42, "timestamp": 90.5, "reason": "代码示例"}, ...]
}
```

---

### 4. FFmpeg 截帧（复用）

**文件**：`framelearn/pipeline/ffmpeg_helper.py`（已有 `capture_single_frame`）

```python
# 直接用现有方法
FFmpegHelper.capture_single_frame(video_path, timestamp, output_path)
```

每段最多截 50 帧，输出到 `temp/frames/chunk_<i>/frame_<j>.jpg`。

---

### 5. VisionStage2（新增）

**文件**：`framelearn/pipeline/vision_stage2.py`

**职责**：看图阶段，决定哪些候选帧保留、哪些丢弃。

**接口**：
```python
@dataclass
class FrameDecision:
    frame_path: str
    srt_id: int                       # 关联的 SRT 段 id
    timestamp: float
    keep: bool
    reason: str

class VisionStage2:
    async def process(
        self,
        chunk: SRTChunk,
        candidates: list[CandidateTimestamp],
        frames_dir: Path,
    ) -> list[FrameDecision]:
        """看图后输出每帧的 keep/discard 决策"""
```

**输入构造**：
```
{
  "chunk_text": "<subtitle>...</subtitle>",
  "frames": [
    {"path": "frame_0.jpg", "srt_id": 42, "timestamp": 90.5, "reason": "代码示例"},
    {"path": "frame_1.jpg", ...},
    ...
  ]
}
```

**Prompt**：
```
你看到了 N 张视频关键帧和对应字幕。每张图都要决定保留（keep=true）还是丢弃（keep=false）。

判断标准：
- 保留：PPT、代码、终端、表格、示意图 — 有视觉教学价值
- 丢弃：讲师人脸、模糊画面、纯黑屏、空白屏幕、与字幕不相关的画面

输出 JSON：
{
  "decisions": [
    {"frame": "frame_0.jpg", "keep": true, "reason": "代码示例"},
    {"frame": "frame_1.jpg", "keep": false, "reason": "模糊"}
  ]
}
```

---

### 6. MDAssembler（新增）

**文件**：`framelearn/pipeline/md_assembler.py`

**职责**：程序化拼装两个最终 markdown。

**接口**：
```python
class MDAssembler:
    def assemble_a(
        self,
        cleaned_srt: list[TranscriptSegment],
        all_decisions: list[FrameDecision],
    ) -> str:
        """Markdown A：SRT 原结构 + 图片插入"""

    def assemble_b(
        self,
        all_blog_markdowns: list[str],   # 各段博客 markdown
        all_decisions: list[FrameDecision],
    ) -> str:
        """Markdown B：博客 markdown 拼接 + 图片插入"""
```

**Markdown A 格式**（示例）：
```markdown
# 视频讲义

> 1. **00:00:01 - 00:00:04**  
> 大家好，今天讲一下 FastAPI

> 2. **00:00:05 - 00:00:12**  
> 首先看路由机制

![FastAPI 路由代码](src/frame_00h00m10s.jpg)

> 3. **00:00:13 - 00:00:18**  
> ...

（图片按 decision.srt_id 插在对应 SRT 段之后）
```

**Markdown B 格式**（示例）：
```markdown
# 视频讲义（博客版）

## FastAPI 路由机制

[博客式段落...]

![代码示例](src/frame_00h00m10s.jpg)
```

---

### 7. VideoPipeline（修改）

**文件**：`framelearn/pipeline/video_pipeline.py`

**改动**：
- 移除对 `AgentKeyframeSelector` 和 `DocumentGenerator` 的调用
- 改用 `ChunkedDocGenerator`（新模块，组织上面 1-6）
- 输出从单 `markdown_path` 改为 `markdown_a_path` + `markdown_b_path`

**新接口**：
```python
@dataclass
class PipelineResult:
    output_dir: Path
    markdown_a_path: Path       # 新：SRT 式 + 插图
    markdown_b_path: Path       # 新：博客式 + 插图
    subtitle_text: str
    keyframes: list[Path]
    error: Optional[str] = None
```

---

## 异步策略

- 用 `asyncio.gather()` 并发执行 N 段文本清洗和 N 段视觉模型调用
- 用 `asyncio.Semaphore(5)` 限并发（避免 provider 限流）
- `provider_adapter.py` 加 `async def call_llm_async(...)`，内部用 `httpx.AsyncClient`
- 保留同步版本 `call_llm(...)` 作为 fallback（用 `asyncio.run` 包装）

---

## 缓存策略

manifest 文件：`output_dir/manifest.json`，SHA256 哈希：
- 视频文件
- SRT 内容
- `[chunking]` 配置快照
- `[text_clean]` 配置快照
- `[vision]` 配置快照

任一变更 → 全部重跑。

不像旧版有 `segments_notes/manifest.json` 这种段级缓存——段内单次大调用无法做段级缓存。

---

## 错误处理

| 失败点 | 处理 |
|--------|------|
| 文本清洗某段失败 | 重试 2 次（指数退避），第 3 次降级：保留原文 |
| 视觉 Stage1 某段失败 | 重试 2 次，第 3 次降级：该段博客 markdown 用 cleaned SRT 替代，无候选时间戳 |
| 视觉 Stage2 某段失败 | 重试 2 次，第 3 次降级：全部 keep=true |
| ffmpeg 截帧失败 | 跳过该时间戳，继续 |
| 单段全部失败 | 该段在两个 markdown 中都缺失，记录 warning |

---

## 向后兼容

- 旧 `agent_keyframe_selector.py` 和 `doc_generator.py` 保留代码，但 `VideoPipeline` 不再调用
- 旧 `notes.md` / `visual_script.md` 不再生成
- 如果用户配置 `[doc_gen] legacy_modes = true`，可以保留旧输出（可选）

---

## 测试要点

- `SRTChunker` 切段边界（30 分钟整点、剩余段）
- `TextCleaner` 保留 id 和时间戳，只改 text
- `VisionStage1` 输出 JSON 格式正确
- `VisionStage2` 处理 50 张图不超 context
- `MDAssembler` 图片插入位置正确（按 srt_id）
- 端到端：30 分钟视频完整跑通，输出两个 markdown
- 错误降级路径：单段失败不影响其他段
