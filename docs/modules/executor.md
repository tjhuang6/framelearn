# 工具执行器接口设计

## 职责

封装所有外部工具（yt-dlp、ffmpeg、Whisper、Tesseract），为 Agent 提供统一的工具调用接口。执行器本身不做决策，只负责执行和返回结果。

---

## 两个运行阶段

### 预处理阶段（规划 Agent 之前）

目标：从视频来源（URL 或本地文件）生成采样帧 + 带时间戳文字稿，供规划 Agent 分析。

字幕优先策略：
- **Bilibili URL**：先调 B站 API 获取官方字幕 → 有字幕直接用，跳过音频下载和 Whisper
- **YouTube URL**：先用 yt-dlp 下载官方或自动生成字幕 → 有字幕直接用
- **本地文件**：检查同目录 `.srt` / `.vtt` 字幕文件 → 有字幕直接用
- **无字幕时**：提取音频 → Whisper（本地）或 Groq Whisper API 转写

```
来源判断：
  ├─ 在线视频 URL：
  │   ├─ fetch_subtitle（优先）
  │   │    ├─ 成功 → 直接得到带时间戳文字稿
  │   │    └─ 失败 → download_video → extract_audio → transcribe_audio
  │   └─ extract_sample_frames（始终执行）
  │
  └─ 本地视频文件：
      ├─ load_local_subtitle（检查同目录 .srt/.vtt）
      │    ├─ 成功 → 解析字幕文件得到文字稿
      │    └─ 失败 → extract_audio → transcribe_audio
      └─ extract_sample_frames（始终执行）
```

### 全量阶段（规划 Agent 之后）

目标：按转换计划提取关键帧并识别文字。

```
extract_frames（按计划）→ detect_scene_changes → run_ocr
```

---

## 数据结构

```python
@dataclass
class VideoDownloadResult:
    local_path: str         # 下载后的视频文件路径
    title: str              # 视频标题
    duration_sec: float     # 视频时长（秒）
    file_size_bytes: int

@dataclass
class TranscriptRow:
    start: float
    end: float
    text: str

@dataclass
class FrameExtractionResult:
    frame_paths: list[str]  # 提取的帧文件路径，按时间排序
    timestamps: list[float] # 每帧对应的时间戳（秒）

@dataclass
class OcrResult:
    frame_path: str
    text: str               # OCR 识别的文字（可能为空）
    confidence: float       # 置信度 0.0-1.0
```

---

## 工具接口

所有工具实现 `BaseTool`，由 `ToolRegistry` 统一管理。

### fetch_subtitle

```python
class FetchSubtitleTool(BaseTool):
    name = "fetch_subtitle"
    description = "优先获取视频官方字幕。Bilibili 调用 B站 API，YouTube 用 yt-dlp 下载字幕。"
                  "成功时直接返回带时间戳文字稿，跳过音频下载和 Whisper 转写。"

    def execute(self, url: str) -> list[TranscriptRow] | None:
        """
        Bilibili：调用 /x/player/v2 接口获取字幕列表，优先选中文字幕。
        YouTube：yt-dlp --write-subs --sub-lang zh-Hans,en --skip-download
        返回 None 表示无字幕，上层回退到 Whisper 流程。
        """
```

### load_local_subtitle

```python
class LoadLocalSubtitleTool(BaseTool):
    name = "load_local_subtitle"
    description = "本地视频文件时，检查同目录是否有同名 .srt 或 .vtt 字幕文件。"
                  "成功时解析字幕并返回带时间戳文字稿，跳过 Whisper 转写。"

    def execute(self, video_path: str) -> list[TranscriptRow] | None:
        """
        检查 video.mp4 同目录的：
          - video.srt
          - video.zh.srt
          - video.vtt
        优先选择中文字幕，解析成 TranscriptRow 列表。
        返回 None 表示无字幕，上层回退到 Whisper 流程。
        """
```

### download_video

```python
class DownloadVideoTool(BaseTool):
    name = "download_video"
    description = "从 YouTube 或 Bilibili URL 下载视频到本地。输入：视频 URL。"

    def execute(self, url: str) -> VideoDownloadResult:
        """
        使用 yt-dlp 下载视频。
        - 默认下载最高质量 mp4
        - 自动处理 Bilibili 鉴权（如需要）
        - 输出到 output/video.{ext}
        """
```

### extract_audio

```python
class ExtractAudioTool(BaseTool):
    name = "extract_audio"
    description = "从视频文件提取音频（MP3 格式）。输入：视频文件本地路径。"

    def execute(self, video_path: str) -> str:  # 返回 audio_path
        """使用 ffmpeg 提取音频，输出到 output/audio.mp3。"""
```

### transcribe_audio

```python
class TranscribeAudioTool(BaseTool):
    name = "transcribe_audio"
    description = "将音频文件转为带时间戳的中文文字稿。"
                  "对长音频自动分块处理。输入：音频文件路径。"

    def execute(self, audio_path: str) -> list[TranscriptRow]:
        """
        使用本地 Whisper 模型转写。
        - 自动估算安全分块时长（参考 Bilitato asrChunking 逻辑）
        - 分块转写后合并，去重叠区域
        - 转写结果经过清洗流水线（参考 Bilitato subtitleProcessor 逻辑）
        """
```

### extract_sample_frames

```python
class ExtractSampleFramesTool(BaseTool):
    name = "extract_sample_frames"
    description = "每隔固定时间间隔从视频提取采样帧，供规划 Agent 分析结构。"
                  "输入：视频路径和采样间隔（秒，默认 60）。"

    def execute(self, video_path: str, interval_sec: int = 60) -> FrameExtractionResult:
        """使用 ffmpeg -vf fps=1/interval 提取帧，输出到 output/frames/sample/。"""
```

### extract_frames

```python
class ExtractFramesTool(BaseTool):
    name = "extract_frames"
    description = "按转换计划中的时间范围提取关键帧。"
                  "输入：视频路径、章节时间范围列表、每章提取帧数。"

    def execute(
        self,
        video_path: str,
        chapters: list[Chapter],
        frames_per_chapter: int = 20
    ) -> dict[int, FrameExtractionResult]:  # chapter_index → frames
        """
        按每章时长和密度建议（chapter.frame_density）决定提取频率。
        高密度章节（代码演示）提取更多帧。
        输出到 output/frames/chapter_{n}/。
        """
```

### detect_scene_changes

```python
class DetectSceneChangesTool(BaseTool):
    name = "detect_scene_changes"
    description = "检测视频中的场景切换点（画面突变位置）。"
                  "输入：视频路径和时间范围（秒）。"

    def execute(
        self,
        video_path: str,
        start_sec: float,
        end_sec: float,
        threshold: float = 0.3
    ) -> list[float]:  # 场景切换时间戳列表
        """使用 ffmpeg select filter 检测场景变化。"""
```

### run_ocr

```python
class RunOcrTool(BaseTool):
    name = "run_ocr"
    description = "从图像帧中提取文字（代码、终端输出、错误信息等）。"
                  "输入：帧图像路径列表。"

    def execute(self, frame_paths: list[str]) -> list[OcrResult]:
        """
        使用 pytesseract 对每帧执行 OCR。
        - 优先识别代码区域（等宽字体区域）
        - 置信度 < 0.5 的结果标记为低可信度
        """
```

---

## 执行器统一接口

```python
class ToolExecutor:
    def __init__(self):
        self.registry = ToolRegistry()
        self._register_all_tools()

    def run_preprocessing(self, url: str) -> PreprocessingResult:
        """运行预处理阶段：下载 → 提取音频 → 转写 → 采样帧。"""

    def run_full_extraction(
        self,
        plan: ConversionPlan,
        video_path: str
    ) -> ExtractionResult:
        """运行全量阶段：按计划提取帧 + OCR。"""
```

---

## 错误处理

| 工具 | 常见错误 | 处理方式 |
|-----|---------|---------|
| download_video | 网络超时、URL 无效 | 抛出 `DownloadError`，附带原因 |
| transcribe_audio | 音频文件损坏 | 尝试重新提取音频；仍失败则返回空文字稿，记录警告 |
| run_ocr | 图像质量太低 | 返回低置信度结果，不抛出异常（OCR 失败不是致命错误）|
| extract_frames | 时间范围超出视频长度 | 自动裁剪到视频实际长度 |
