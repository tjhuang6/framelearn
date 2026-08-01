# 设计：视频处理流水线

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    framelearn run <video>                    │
└────────────────────────────┬────────────────────────────────┘
                             ↓
                  ┌──────────────────────┐
                  │   VideoPipeline      │
                  │   (pipeline.py)      │
                  └──────────┬───────────┘
                             ↓
         ┌───────────────────┼───────────────────┐
         ↓                   ↓                   ↓
    ┌────────┐        ┌──────────┐      ┌───────────┐
    │ FFmpeg │        │  ASR     │      │  Codex    │
    │ Helper │        │ Adapter  │      │  Adapter  │
    └────────┘        └──────────┘      └───────────┘
         │                   │                   │
         ↓                   ↓                   ↓
    音轨提取            硅基流动 API         app-server
    场景抽帧            SenseVoice          或 Vision API
    关键帧去重          (无时间戳)          
```

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
