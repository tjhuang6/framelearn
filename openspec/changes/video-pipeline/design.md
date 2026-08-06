# 设计：视频处理流水线

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│           framelearn run <video> [--subtitle <srt>]          │
└────────────────────────────┬────────────────────────────────┘
                             ↓
                  ┌──────────────────────┐
                  │   VideoPipeline      │
                  │   video_pipeline.py  │
                  └──────────┬───────────┘
                             ↓
         ┌───────────────────┼───────────────────┐
         ↓                   ↓                   ↓
    ┌────────┐        ┌──────────┐      ┌─────────────────────┐
    │ FFmpeg │        │  ASR     │      │  DocumentGenerator  │
    │ Helper │        │ Adapter  │      │  (segmented + agent)│
    └────────┘        └──────────┘      └─────────────────────┘
         │                   │                   │
         ↓                   ↓                   ↓
    音轨提取（可选）     百炼 Qwen ASR        SegmentSplitter
    场景抽帧（pts_time） 词级时间戳           逐段 LLM 生成
    关键帧去重          subtitle.srt         Agent 质量评审
    frame_HHhMMmSSs.jpg                      合并 → index.md
```

---

## 模块拆分

### 1. VideoPipeline（主流程）

**文件**：`framelearn/pipeline/video_pipeline.py`

**职责**：
- 接收视频文件路径（可选 `--subtitle` 跳过 ASR）
- 按需调用 FFmpeg 提取音轨
- 调用 ASR Adapter 转录（或直接读已有字幕）
- 调用 FFmpeg 抽帧（返回时间戳）
- 关键帧去重
- 调用 DocumentGenerator 分段生成文档
- 输出到指定目录

**接口**：
```python
class VideoPipeline:
    def __init__(
        self,
        video_path: str,
        output_dir: Optional[str] = None,
        subtitle_path: Optional[str] = None,  # 跳过 ASR
    ):
        pass

    def run(self) -> PipelineResult:
        """Execute the full pipeline."""
        pass

@dataclass
class PipelineResult:
    output_dir: Path
    markdown_path: Path
    keyframes: list[Path]          # 仅路径（供外部引用）
    subtitle_text: str
    error: Optional[str] = None
```

---

### 2. FFmpegHelper（FFmpeg 封装）

**文件**：`framelearn/pipeline/ffmpeg_helper.py`

**职责**：
- 提取音轨 → `.m4a`
- 场景检测抽帧，解析 `showinfo` 获取 `pts_time`
- 定时保底抽帧
- 单帧截取（供 Agent 补帧）
- 验证 FFmpeg 是否安装

**接口**：
```python
class FFmpegHelper:
    @staticmethod
    def extract_audio(video_path: str, output_path: str) -> bool:
        """Extract audio track to m4a."""

    @staticmethod
    def extract_keyframes(
        video_path: str,
        output_dir: str,
        scene_threshold: float = 0.3,
        fallback_interval: int = 30,
        max_frames: int = 100,
    ) -> list[tuple[Path, float]]:
        """Extract keyframes; returns (path, timestamp_seconds) tuples.

        Filenames use timestamp: frame_00h03m45s.jpg
        """

    @staticmethod
    def capture_single_frame(
        video_path: str,
        timestamp: float,
        output_path: str,
    ) -> bool:
        """Capture one frame at the given timestamp (for Agent补帧)."""

    @staticmethod
    def check_installed() -> bool:
        """Check if ffmpeg is available in PATH."""
```

**FFmpeg 命令示例**：

```bash
# 音轨提取
ffmpeg -i video.mp4 -vn -acodec aac -ar 16000 audio.m4a

# 场景检测抽帧（带 showinfo 提取 pts_time）
ffmpeg -i video.mp4 \
  -vf "select='gt(scene,0.3)',showinfo,scale=1280:-1" \
  -vsync vfr -q:v 2 output/frame_%04d.jpg
# 解析 stderr 中的 pts_time:xx.xx

# 定时保底抽帧（每 30 秒）
ffmpeg -ss <timestamp> -i video.mp4 -vframes 1 frame_HHhMMmSSs.jpg

# 单帧截取
ffmpeg -ss 225.5 -i video.mp4 -vframes 1 -q:v 2 frame_00h03m45s.jpg
```

---

### 3. ASRAdapter（语音识别适配器）

**文件**：`framelearn/pipeline/asr_adapter.py`

**职责**：
- 支持 dashscope（百炼 Qwen ASR，有词级时间戳）
- 支持 siliconflow（无时间戳，低成本）
- 统一返回 `TranscriptResult`

**接口**：
```python
@dataclass
class TranscriptResult:
    segments: list[TranscriptSegment]
    full_text: str
    has_timestamps: bool
    srt: Optional[str] = None      # 带时间戳的 SRT 文本

@dataclass
class TranscriptSegment:
    text: str
    start: float                   # 秒
    end: float

class ASRAdapter:
    def transcribe(
        self,
        audio_path: str,
        output_dir: Optional[Path] = None,
    ) -> TranscriptResult:
        pass
```

---

### 4. KeyframeDeduplicator（感知哈希去重）

**文件**：`framelearn/pipeline/keyframe_dedup.py`

**职责**：移除视觉相似帧，保留代表帧

**接口**：
```python
class KeyframeDeduplicator:
    def deduplicate(
        self,
        frames: list[tuple[Path, float]],   # (path, timestamp)
        max_frames: int = 100,
    ) -> list[tuple[Path, float]]:
        """Remove visually similar frames using pHash."""
```

---

### 5. SubtitleCleaner（字幕清洗）

**文件**：`framelearn/pipeline/subtitle_cleaner.py`

**职责**：清洗 ASR 输出噪声

```python
class SubtitleCleaner:
    def clean(self, raw_text: str) -> str:
        """去括号、全角转半角、去重行、优化断句。"""

    @staticmethod
    def strip_timestamps(srt_text: str) -> str:
        """去掉 SRT/VTT 时间戳，返回纯文本。"""
```

---

### 6. SegmentSplitter（分段切割）

**文件**：`framelearn/pipeline/segment_splitter.py`

**职责**：把字幕和关键帧按时间切分为若干段，供逐段 LLM 生成

```python
@dataclass
class Segment:
    index: int
    start_time: float
    end_time: float
    subtitle: str
    keyframes: list[tuple[Path, float]]

def split_segments(
    subtitle: str,
    keyframes: list[tuple[Path, float]],
    segment_duration: float = 90.0,
    max_keyframes_per_segment: int = 10,
    srt_text: Optional[str] = None,        # 优先用 SRT 精确切分
) -> list[Segment]:
    """SRT 精确切分 → 字数估算 fallback。"""
```

---

### 7. DocumentGenerator（文档生成器）

**文件**：`framelearn/pipeline/doc_generator.py`

**职责**：
- 支持三种模式：`visual_script`（默认）、`notes`、`textbook`
- 长内容自动分段（> 8000 字 或 > 20 帧触发）
- 逐段调用 LLM，进度显示
- 支持 appserver / api 两种后端

```python
DocMode = Literal["visual_script", "notes", "textbook"]

class DocumentGenerator:
    def generate(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        video_title: str,
        mode: DocMode = "visual_script",
        srt_text: Optional[str] = None,
    ) -> str:
        """Segmented generation: split → per-segment LLM → merge."""
```

**分段生成逻辑**：
```
长视频（> 8000 字 或 > 20 帧）
  → split_segments()
  → 逐段 _generate_single()
  → "# 标题\n\n" + "\n\n---\n\n".join(parts)

短视频（直接）
  → _generate_single()
```

---

### 8. AgentQualityChecker（Agent 质量评审，Phase 3）

**文件**：`framelearn/pipeline/agent_quality_checker.py`

**职责**：评审每段生成结果，决定是否重试或补帧

```python
@dataclass
class QualityIssue:
    type: Literal["too_short", "missing_frame", "low_quality"]
    segment_index: int
    detail: str

class AgentQualityChecker:
    def check(
        self,
        segment: Segment,
        generated_text: str,
    ) -> list[QualityIssue]:
        """Check quality issues in generated segment."""

    def should_retry(self, issues: list[QualityIssue]) -> bool:
        """Decide if segment needs retry."""

    def suggest_extra_frames(
        self,
        segment: Segment,
        generated_text: str,
    ) -> list[float]:
        """Return timestamps where extra frames should be captured."""
```

**触发条件**：

| 问题 | 检测方式 | Agent 动作 |
|------|----------|------------|
| 段落太短（< 100 字） | len(text) | 用更大模型重试 |
| 缺少关键帧 | 段内帧数 < 2 | 降低 threshold 补抽 |
| 字幕提到"如图" | 关键词匹配 | 补截对应时间点的帧 |
| 图片全黑/模糊 | 图片亮度/方差检测 | 跳过该帧 |

---

## 配置（settings.toml）

```toml
[runtime]
text_mode = "appserver"        # appserver / api
vision_mode = "appserver"      # appserver / api
vision_provider = "deepseek"
vision_model = "deepseek-reasoner"

[doc_generation]
mode = "visual_script"         # visual_script / notes / textbook
segment_duration = 90          # 每段时长（秒）
max_keyframes_per_segment = 10

[asr]
provider = "dashscope"
model = "qwen-audio-3.0-asr-flash-filetrans"
language_hints = ["zh", "en"]
chunk_duration = 1800
```

---

## 数据流

```
video.mp4
  ↓ FFmpegHelper.extract_keyframes()
list[tuple[Path, float]]          # (frame_00h03m45s.jpg, 225.0)
  ↓ KeyframeDeduplicator.deduplicate()
list[tuple[Path, float]]          # 去重后
  ↓
ASRAdapter.transcribe()
TranscriptResult(srt=..., full_text=...)
  ↓ SubtitleCleaner.clean()
str (cleaned)
  ↓
DocumentGenerator.generate(keyframes, subtitle, srt_text=srt)
  ↓ split_segments()
list[Segment]                     # 每段 90 秒
  ↓ per segment: _generate_single()
  ↓ AgentQualityChecker.check()   # Phase 3
  ↓ retry / extra frames if needed
list[str]                         # 各段 Markdown
  ↓ merge
index.md
```

---

## 依赖

```toml
# pyproject.toml
imagehash = ">=4.3.1"    # 关键帧去重
pillow = ">=10.0.0"       # 图片处理
oss2 = ">=2.18.0"         # 阿里云 OSS（ASR 上传）
httpx = ">=0.27.0"        # HTTP 客户端
python-dotenv = ">=1.0.0" # .env 加载
```

---

## 错误处理策略

| 场景 | 处理方式 |
|------|----------|
| FFmpeg 未安装 | 启动时检查，提示安装并退出 |
| 音轨提取失败 | 报错："无法提取音轨，视频可能损坏" |
| ASR API 超时 | 重试 3 次，间隔 5 秒 |
| ASR API 4xx 错误 | 直接报错，提示检查 API key |
| 抽帧失败 | 警告并继续（只用字幕生成文档） |
| 关键帧全部相似 | 保留至少 1 帧 |
| LLM 生成质量差 | Agent 最多重试 2 次，失败后保存原始结果 |
| --subtitle 文件不存在 | 启动时验证，报错退出 |

---

## 扩展点（后续版本）

- [ ] 在线视频支持（yt-dlp）
- [ ] 知识图谱 / RAG 问答
- [ ] 多说话人分离
- [ ] 热词表支持（ASR 准确率提升）
- [ ] 增量生成（断点续传）


---

## 模块拆分

### 1. VideoPipeline（主流程）

**文件**：`framelearn/pipeline/video_pipeline.py`

**职责**：
- 接收视频文件路径
- 调用 FFmpeg 提取音轨
- 调用 ASR Adapter 转录
- 调用 FFmpeg 抽帧
- 关键帧去重
- 调用 Codex/Vision 生成文档
- 输出到指定目录

**接口**：
```python
class VideoPipeline:
    def __init__(self, video_path: str, output_dir: Optional[str] = None):
        pass
    
    def run(self) -> PipelineResult:
        """Execute the full pipeline."""
        pass

@dataclass
class PipelineResult:
    output_dir: Path
    markdown_path: Path
    keyframes: list[Path]
    subtitle_text: str
    error: Optional[str] = None
```

---

### 2. FFmpegHelper（FFmpeg 封装）

**文件**：`framelearn/pipeline/ffmpeg_helper.py`

**职责**：
- 提取音轨 → `.m4a`
- 场景检测抽帧 → JPEG 列表
- 定时保底抽帧（无场景变化时）
- 验证 FFmpeg 是否安装

**接口**：
```python
class FFmpegHelper:
    @staticmethod
    def extract_audio(video_path: str, output_path: str) -> bool:
        """Extract audio track to m4a."""
        pass
    
    @staticmethod
    def extract_keyframes(
        video_path: str,
        output_dir: str,
        scene_threshold: float = 0.3,
        fallback_interval: int = 30,
        max_frames: int = 100,
    ) -> list[Path]:
        """Extract keyframes using scene detection + fallback."""
        pass
    
    @staticmethod
    def check_installed() -> bool:
        """Check if ffmpeg is available in PATH."""
        pass
```

**FFmpeg 命令示例**：

```bash
# 音轨提取
ffmpeg -i video.mp4 -vn -acodec aac -ar 16000 audio.m4a

# 场景检测抽帧（0.3 阈值）
ffmpeg -i video.mp4 -vf "select='gt(scene,0.3)',scale=1280:-1" \
  -vsync vfr -q:v 2 output/frame_%04d.jpg

# 定时保底抽帧（每 30 秒）
ffmpeg -i video.mp4 -vf "fps=1/30,scale=1280:-1" \
  -q:v 2 output/fallback_%04d.jpg
```

---

### 3. ASRAdapter（语音识别适配器）

**文件**：`framelearn/pipeline/asr_adapter.py`

**职责**：
- 根据 `settings.toml` 的 `asr.provider` 选择后端
- 第一版只支持 `siliconflow`
- 第二版支持 `dashscope`（百炼）
- 返回统一格式的转录结果

**接口**：
```python
@dataclass
class TranscriptSegment:
    text: str
    start: Optional[float] = None  # 秒（硅基流动无此字段）
    end: Optional[float] = None

@dataclass
class TranscriptResult:
    segments: list[TranscriptSegment]
    full_text: str
    has_timestamps: bool

class ASRAdapter:
    def __init__(self, provider: str = "siliconflow"):
        pass
    
    def transcribe(self, audio_path: str) -> TranscriptResult:
        """Transcribe audio file."""
        pass
```

**硅基流动 API 调用**：

```python
import httpx

response = httpx.post(
    "https://api.siliconflow.cn/v1/audio/transcriptions",
    headers={"Authorization": f"Bearer {api_key}"},
    files={"file": open(audio_path, "rb")},
    data={"model": "FunAudioLLM/SenseVoiceSmall"},
    timeout=300,
)
result = response.json()
# result["text"] = "完整转录文字"
```

---

### 4. KeyframeDeduplicator（关键帧去重）

**文件**：`framelearn/pipeline/keyframe_dedup.py`

**职责**：
- 使用感知哈希（pHash）检测相似帧
- 去除视觉相似度 > 90% 的重复帧
- 限制最终关键帧数量

**接口**：
```python
class KeyframeDeduplicator:
    def __init__(self, similarity_threshold: float = 0.9):
        pass
    
    def deduplicate(
        self,
        frames: list[Path],
        max_frames: int = 100,
    ) -> list[Path]:
        """Remove duplicate frames and limit count."""
        pass
```

**依赖**：`pip install imagehash pillow`

---

### 5. SubtitleCleaner（字幕清洗）

**文件**：`framelearn/pipeline/subtitle_cleaner.py`

**职责**：
- 移植 Bilitato 的字幕清洗逻辑
- 去除 `[音乐]`、`（掌声）` 等括号内容
- 全角转半角
- 合并重复行
- 断句优化

**接口**：
```python
class SubtitleCleaner:
    def clean(self, raw_text: str) -> str:
        """Clean raw subtitle text."""
        pass
```

**清洗规则**（移植自 Bilitato）：
```python
# 1. 去除括号内容
text = re.sub(r'[\[【（\(].*?[\]】）\)]', '', text)

# 2. 全角转半角
text = text.replace('，', ',').replace('。', '.').replace('！', '!')

# 3. 合并重复行
lines = []
for line in text.split('\n'):
    if not lines or lines[-1] != line:
        lines.append(line)
text = '\n'.join(lines)

# 4. 断句优化（中文句号、英文句号、问号、感叹号后换行）
text = re.sub(r'([。.!?！？])\s*', r'\1\n', text)
```

---

### 6. DocumentGenerator（文档生成）

**文件**：`framelearn/pipeline/doc_generator.py`

**职责**：
- 调用 Codex app-server 或 Vision API
- 关键帧 + 字幕 → 结构化 Markdown
- 按 `settings.toml` 的 `vision_mode` 和 `text_mode` 选择后端

**接口**：
```python
class DocumentGenerator:
    def __init__(self):
        self.vision_mode = config.get("runtime.vision_mode", "appserver")
        self.text_mode = config.get("runtime.text_mode", "appserver")
    
    def generate(
        self,
        keyframes: list[Path],
        subtitle: str,
        video_title: str,
    ) -> str:
        """Generate markdown tutorial."""
        pass
```

**Prompt 结构**：

```
你是一个编程教程整理助手。根据视频关键帧和字幕，生成结构化的 Markdown 教材。

# 字幕文字

<subtitle>
{cleaned_subtitle}
</subtitle>

# 关键帧

<frames>
关键帧 1: [图片]
关键帧 2: [图片]
...
</frames>

# 要求

1. 提取章节结构（## 标题）
2. 每个章节总结要点
3. 引用关键帧（格式：![关键帧](src/frame_001.jpg)）
4. 提取代码片段并标注语言
5. 保留时间戳引用（如果有）

输出 Markdown 格式。
```

---

## 数据流

```
视频文件 (video.mp4)
  │
  ├─→ FFmpeg 提取音轨
  │      ↓
  │   audio.m4a
  │      ↓
  │   ASRAdapter.transcribe()
  │      ↓
  │   TranscriptResult (无时间戳)
  │      ↓
  │   SubtitleCleaner.clean()
  │      ↓
  │   cleaned_text
  │
  └─→ FFmpeg 场景检测
         ↓
      raw_frames/*.jpg (可能 200+ 帧)
         ↓
      KeyframeDeduplicator.deduplicate()
         ↓
      selected_frames (≤ 100 帧)
         ↓
      复制到 output/src/frame_*.jpg

cleaned_text + selected_frames
         ↓
DocumentGenerator.generate()
         ↓
output/index.md
```

---

## 输出目录结构

```
output/
  视频标题/
    index.md              # 生成的教材
    src/
      frame_001.jpg       # 关键帧
      frame_002.jpg
      ...
      subtitle.txt        # 清洗后的字幕
      audio.m4a           # 提取的音轨（可选保留）
```

---

## 配置文件变更

### settings.toml 新增

```toml
[asr]
provider = "siliconflow"              # 第一版固定
model = "FunAudioLLM/SenseVoiceSmall"
base_url = "https://api.siliconflow.cn/v1"

[video]
output_dir = "./output"
scene_threshold = 0.3       # 场景检测阈值
fallback_interval = 30      # 保底抽帧间隔（秒）
max_keyframes = 100
image_quality = 85
keep_temp_files = false

[subtitle]
remove_brackets = true
merge_duplicates = true

[style]
tone = "balanced"          # relaxed / balanced / professional
detail_level = "standard"  # brief / standard / detailed
```

### .env.example 新增

```bash
# 硅基流动 API Key（语音识别）
SILICONFLOW_API_KEY=your_key_here
```

---

## 依赖新增

```toml
[project.dependencies]
# 现有：httpx, python-dotenv

# 新增：
imagehash = ">=4.3.1"   # 关键帧去重
pillow = ">=10.0.0"     # 图片处理
```

---

## 错误处理策略

| 场景 | 处理方式 |
|------|----------|
| FFmpeg 未安装 | 启动时检查，提示安装并退出 |
| 音轨提取失败 | 报错："无法提取音轨，视频可能损坏" |
| ASR API 超时 | 重试 3 次，间隔 5 秒 |
| ASR API 4xx 错误 | 直接报错，提示检查 API key |
| 抽帧失败 | 警告并继续（只用字幕生成文档） |
| 关键帧全部相似 | 保留至少 1 帧 |
| Codex 生成失败 | 报错并保留中间结果（字幕、关键帧） |

---

## 性能优化

1. **并行处理**：音轨提取和抽帧可以并行
2. **流式上传**：ASR API 使用流式上传大文件
3. **增量处理**：如果 `output/src/audio.m4a` 已存在且 `keep_temp_files=true`，跳过音轨提取
4. **批量发送关键帧**：Vision API 一次发送 ≤ 20 帧，避免 context 超限

---

## 第二版扩展点

- [ ] 支持百炼 paraformer-v2（词级时间戳 + 热词）
- [ ] 在线视频（yt-dlp）
- [ ] SRT 字幕生成（需要时间戳）
- [ ] GPT 术语纠错
- [ ] 视频摘要卡片（JSON 输出）
