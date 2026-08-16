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
                                          │ (按 30 分钟) │
                                          └──────┬───────┘
                                                 ↓
                                          ┌──────────────┐
                                          │ TextCleaner  │
                                          │ (并行 N 段)  │
                                          └──────┬───────┘
                                                 ↓
                                          大 cleaned SRT
                                                 ↓
                                          ┌──────────────────┐
                                          │ HeuristicFrame   │
                                          │ Extractor        │
                                          │ (ffmpeg + pHash) │
                                          └──────┬───────────┘
                                                 ↓
                                          候选帧集（全局）
                                                 ↓
                                          ┌──────────────────┐
                                          │ FrameDistributor │
                                          │ 按 chunk 边界    │
                                          └──────┬───────────┘
                                                 ↓
                                       For each chunk:
                                  ┌─────────────────────┐
                                  │ VisionStage1        │
                                  │ (文本+图，N 段)     │
                                  │ 输出 md + ts        │
                                  └──────────┬──────────┘
                                             ↓
                                  ┌─────────────────────┐
                                  │ 检查 + ffmpeg 新截  │
                                  │ (needs_extract)     │
                                  └──────────┬──────────┘
                                             ↓
                                  ┌─────────────────────┐
                                  │ VisionStage2        │
                                  │ (看图，N 段)        │
                                  │ 输出 keep/discard   │
                                  └──────────┬──────────┘
                                             ↓
                                  ┌─────────────────────┐
                                  │ MDAssembler         │
                                  │ srt_picture.md      │
                                  │ blog.md             │
                                  └─────────────────────┘
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

### 3. HeuristicFrameExtractor（新增）

**文件**：`framelearn/pipeline/heuristic_frame_extractor.py`

**职责**：对完整视频做启发式截帧（不调 LLM），产出覆盖完整时长的候选帧集。

**接口**：
```python
@dataclass
class CandidateFrame:
    path: str                 # 帧文件路径
    timestamp_sec: float      # 视频时间戳
    source: str = "heuristic" # 来源标记

class HeuristicFrameExtractor:
    def __init__(
        self,
        scene_threshold: float = 0.4,
        similarity_threshold: float = 0.95,
    ):
        ...

    def extract(self, video_path: str, output_dir: Path) -> list[CandidateFrame]:
        """调用 FFmpeg 场景检测 + pHash 去重，返回候选帧列表"""
```

**实现**：
1. 调用现有 `FFmpegHelper.extract_keyframes(video, output_dir, scene_threshold=0.4)`
2. 调用现有 `KeyframeDeduplicator.deduplicate(frames, similarity_threshold=0.95)`
3. 包装成 `list[CandidateFrame]`

**关键**：不调 LLM，纯本地计算。可缓存（同一视频第二次跳过）。

---

### 4. FrameDistributor（新增）

**文件**：`framelearn/pipeline/frame_distributor.py`

**职责**：把全局候选帧按 timestamp_sec 分配到对应 chunk。

**接口**：
```python
class FrameDistributor:
    def distribute(
        self,
        chunks: list[SRTChunk],
        frames: list[CandidateFrame],
        max_per_chunk: int = 50,
    ) -> dict[int, list[CandidateFrame]]:
        """返回 {chunk_index: 该 chunk 的候选帧列表}"""
```

**算法**：
1. 按 `chunk.start_sec <= frame.timestamp_sec < chunk.end_sec` 分配
2. 单 chunk 超过 `max_per_chunk` 时均匀采样保留
3. 边界帧（恰在 chunk 边界）：归到前一 chunk（避免重复）

---

### 5. VisionStage1（新增，文本+图）

**文件**：`framelearn/pipeline/vision_stage1.py`

**职责**：视觉模型一次调用，输入 cleaned SRT + 启发式候选帧，输出博客 markdown + 增删改时间戳决策。

**接口**：
```python
@dataclass
class SelectedTimestamp:
    srt_id: int
    timestamp: float              # 调整后的时间戳（增/改后）
    needs_extract: bool           # True = 需要 ffmpeg 截
    source_frame_path: str | None # 启发式帧路径（needs_extract=False 时）
    reason: str

@dataclass
class VisionStage1Output:
    blog_markdown: str                # 该段的博客式 markdown
    selected_timestamps: list[SelectedTimestamp]  # ≤ 50 个

class VisionStage1:
    async def process(
        self,
        chunk: SRTChunk,
        frames_in_chunk: list[CandidateFrame],
    ) -> VisionStage1Output:
        """输入 cleaned SRT + 启发式帧，输出 blog_markdown + selected_timestamps"""
```

**Prompt 模板**（关键）：
```
你是视频字幕整理助手。给你一段清洗过的 SRT 和该段内的启发式截帧列表。

请做三件事：
1. 生成博客式 markdown 段落（合并 SRT 段为连贯叙述，去掉时间戳和序号）
2. 从启发式帧中**保留**合适的帧
3. **新增**启发式未覆盖的时间戳（如果老师提到屏幕上的图但启发式没截到）
4. **调整**不精确的时间戳

候选时间戳选择规则：
- 出现"看"、"如图"、"图中"、"屏幕"、"代码"、"演示"、"PPT"等关键词的段必选
- 启发式帧列表中包含 path + timestamp_sec
- 你可以选择调整 timestamp（±2 秒）
- 你可以新增 timestamp（如果启发式没覆盖到）

输入：
<subtitle>
{chunk_text}
</subtitle>

启发式帧：
{frames_json}

输出 JSON：
{
  "blog_markdown": "## ...\\n\\n这是博客式段落...",
  "selected_timestamps": [
    {"srt_id": 42, "timestamp": 90.5, "needs_extract": false, "source_frame_path": "frame_xxx.jpg", "reason": "保留：代码示例"},
    {"srt_id": 50, "timestamp": 100.2, "needs_extract": true, "source_frame_path": null, "reason": "新增：屏幕图"}
  ]
}
```

**降级**：失败重试 2 次，第 3 次 blog_markdown = cleaned SRT 拼接，selected_timestamps = 所有启发式帧（needs_extract=false）。

---

### 6. FFmpeg 新截帧（运行时逻辑）

**文件**：`framelearn/pipeline/chunked_doc_generator.py` 内私有函数

**职责**：处理 Stage1 输出的 `needs_extract=true` 项。

```python
def extract_new_frames(
    selected: list[SelectedTimestamp],
    video_path: str,
    chunk_index: int,
    output_dir: Path,
) -> list[CandidateFrame]:
    """只截 needs_extract=True 的项，输出 CandidateFrame(source='stage1')"""
```

输出路径：`temp/frames/chunk_<i>/extra_frame_<j>.jpg`

---

### 7. VisionStage2（新增，看图）

**文件**：`framelearn/pipeline/vision_stage2.py`

**职责**：看图阶段，对 Stage1 选中的所有帧（启发式 + 新截）做最终 keep/discard。

**接口**：
```python
@dataclass
class FrameDecision:
    srt_id: int                       # 关联的 SRT 段 id
    frame_path: str                   # 帧路径
    timestamp: float
    keep: bool
    reason: str

class VisionStage2:
    async def process(
        self,
        chunk: SRTChunk,
        all_frames: list[CandidateFrame],  # 启发式 + 新截
    ) -> list[FrameDecision]:
        """看图后输出每帧的最终 keep/discard 决策"""
```

**输入构造**：
```
{
  "chunk_text": "<subtitle>...</subtitle>",
  "frames": [
    {"path": "frame_0.jpg", "srt_id": 42, "timestamp": 90.5, "source": "heuristic"},
    {"path": "extra_frame_0.jpg", "srt_id": 50, "timestamp": 100.2, "source": "stage1"},
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
    {"frame": "extra_frame_0.jpg", "keep": false, "reason": "模糊"}
  ]
}
```

**降级**：失败重试 2 次，第 3 次全部 keep=true。

---

### 8. MDAssembler（新增）

**文件**：`framelearn/pipeline/md_assembler.py`

**职责**：程序化拼装两个最终 markdown。

**接口**：
```python
class MDAssembler:
    def __init__(self, srt_filename: str = "srt_picture.md", blog_filename: str = "blog.md"):
        ...

    def assemble_srt(
        self,
        cleaned_srt: list[TranscriptSegment],
        all_decisions: list[FrameDecision],
    ) -> str:
        """Markdown-SRT：SRT 原结构 + 图片插入"""

    def assemble_blog(
        self,
        all_blog_markdowns: list[str],   # 各段博客 markdown
        all_decisions: list[FrameDecision],
    ) -> str:
        """Markdown-Blog：博客 markdown 拼接 + 图片插入"""
```

**Markdown-SRT 格式**（示例）：
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

**Markdown-Blog 格式**（示例）：
```markdown
# 视频讲义（博客版）

## FastAPI 路由机制

[博客式段落...]

![代码示例](src/frame_00h00m10s.jpg)
```

---

### 9. VideoPipeline（修改）

**文件**：`framelearn/pipeline/video_pipeline.py`

**改动**：
- 移除对 `AgentKeyframeSelector` 和 `DocumentGenerator` 的调用
- 改用 `ChunkedDocGenerator`（新模块，组织上面 1-8）
- 输出从单 `markdown_path` 改为 `srt_picture_path` + `blog_path`

**新接口**：
```python
@dataclass
class PipelineResult:
    output_dir: Path
    srt_picture_path: Path       # 新：SRT 式 + 插图
    blog_path: Path              # 新：博客式 + 插图
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
- **启发式截帧结果摘要**（候选帧列表的 SHA256）— 视频不变 + 场景参数不变 → 复用
- `[chunking]` 配置快照
- `[text_clean]` 配置快照
- `[doc_gen]` 配置快照
- `[heuristic]` 配置快照
- `[vision]` 配置快照

任一变更 → 全部重跑。

不像旧版有 `segments_notes/manifest.json` 这种段级缓存——段内单次大调用无法做段级缓存。

---

## 错误处理

| 失败点 | 处理 |
|--------|------|
| 启发式截帧失败 | 重试 2 次；失败则放弃启发式阶段，pipeline 仍可继续（只是 Stage1 无图输入） |
| 文本清洗某段失败 | 重试 2 次（指数退避），第 3 次降级：保留原文 |
| 视觉 Stage1 某段失败 | 重试 2 次，第 3 次降级：blog_markdown = cleaned SRT 拼接，selected_timestamps = 该 chunk 全部启发式帧 |
| ffmpeg 新截帧失败 | 跳过该时间戳，继续 |
| 视觉 Stage2 某段失败 | 重试 2 次，第 3 次降级：全部 keep=true |
| 单段全部失败 | 该段在两个 markdown 中都缺失，记录 warning |

---

## 向后兼容

- 旧 `agent_keyframe_selector.py` 和 `doc_generator.py` 保留代码，但 `VideoPipeline` 不再调用
- 旧 `notes.md` / `visual_script.md` 不再生成
- `keyframe_dedup.py` 保留（被 HeuristicFrameExtractor 复用）

---

## 测试要点

- `SRTChunker` 切段边界（30 分钟整点、剩余段）
- `TextCleaner` 保留 id 和时间戳，只改 text
- `HeuristicFrameExtractor` 复用现有 ffmpeg + pHash
- `FrameDistributor` 边界帧正确归位（不重复不丢失）
- `VisionStage1` 输入含图，输出 JSON 含 `needs_extract` 字段
- `VisionStage2` 处理所有选中帧（启发式 + 新截）不超 context
- `MDAssembler` 图片插入位置正确（按 srt_id）
- 端到端：30 分钟视频完整跑通，输出 srt_picture.md + blog.md
- 错误降级路径：单段失败不影响其他段
