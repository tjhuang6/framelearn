# FrameLearn

[English](README.en.md) | 中文

FrameLearn 将本地编程教学视频转换为带关键帧的 Markdown 学习材料。当前实现覆盖音轨提取、ASR、博客锚点流水线（文本模型生成博客与帧锚点，FFmpeg 提供候选帧，视觉模型验图）、双 Markdown 输出，以及基于文本 API 的通用问答。

## 当前能力

- 处理本地 `.mp4`、`.mkv`、`.avi`、`.mov`、`.flv`、`.wmv`、`.webm` 视频。
- 自动识别视频内音轨；若 B 站下载文件音视频分离，会在同目录查找同前缀的 `.mp3`、`.m4a` 或 `.aac`。
- ASR 支持：
  - 阿里云百炼 DashScope：长音频分片、OSS 临时上传、异步转写、断点记录和 SRT 时间戳。
  - 硅基流动 SenseVoice：实现简单，但不返回时间戳。
- 可通过 `--subtitle` 直接使用已有 `.txt`、`.srt` 或 `.vtt`，跳过 ASR。
- **博客锚点流水线**：先按时长切段并插入启发式帧标记，文本 LLM 生成博客正文并输出 `[[FRAME:id@timestamp]]` 锚点；程序绑定候选帧或 FFmpeg 精准补截；Qwen3-VL 视觉模型只负责验图（retake/keep/caption/text_representation），最后程序拼装 `blog.md` 与 `srt_picture.md`。
- 每次运行固定生成两个 Markdown：`srt_picture.md`（保留 SRT 段结构、时间戳 + 配图）和 `blog.md`（博客式叙述 + 同样的配图）。
- 启发式截帧（ffmpeg 场景检测 + pHash 去重）结果会被 SHA256 摘要写入 manifest，配置或视频变化时自动重跑。
- `ask` 通过文本 LLM API 回答通用问题。

## 尚未实现或受限的能力

- YouTube/Bilibili URL 会被识别和校验，但在线下载尚未实现；请先下载到本地。
- `ask` 当前不是“只检索已生成教材”的 RAG 问答；它是工作目录中的通用 API 对话。

- 旧版 `agent_keyframe_selector.py` / `doc_generator.py`（`notes` / `visual_script` mode）已被分块流程替代，保留仅为向后兼容。

## 安装

要求 Python 3.11+、[uv](https://docs.astral.sh/uv/) 和 FFmpeg（包含 `ffprobe`）。

```bash
brew install ffmpeg
uv sync
```

注意：源码还直接导入 Pillow、ImageHash 和 `oss2`。如果当前锁文件没有安装这些包，需要补装后再运行对应功能：

```bash
uv add pillow imagehash oss2
```

## 配置

复制环境变量模板：

```bash
cp .env.example .env
```

当前仓库 `settings.toml` 的关键段：

```toml
[text]
text_mode = "api"
provider = "claude"
model = "MiniMax-M3"
base_url = "https://api.minimaxi.com/anthropic"

[vision]
vision_mode = "api"
vision_provider = "siliconflow"
vision_model = "Qwen/Qwen3-VL-8B-Instruct"

[asr]
provider = "dashscope"
model = "qwen-audio-3.0-asr-flash-filetrans"

[chunking]
segment_minutes = 30          # 每段最长视频时长
max_images_per_chunk = 50     # 单段最多保留的图片数
concurrency = 5               # 段内并发 LLM 调用上限

[text_clean]
# 旧版分块流水线使用；当前 blog-anchor 流水线暂不调用 TextCleaner
filler_words = ["那么", "就是说", "大家注意", "咱们", "啊", "嗯", "这个", "那个", "对吧"]

[heuristic]
scene_threshold = 0.4         # ffmpeg 场景检测阈值（越低越敏感）
similarity_threshold = 0.95   # pHash 去重阈值
max_frames = 200

[doc_gen]
srt_filename = "srt_picture.md"   # 保留 SRT 结构 + 配图
blog_filename = "blog.md"         # 博客式叙述 + 同样的配图

[blog_gen]
frame_match_tolerance = 2.0       # 锚点时间戳与候选帧匹配容差（秒）
max_retakes = 1                   # 视觉模型 retake 补截上限
```

使用默认配置至少需要：

```bash
DASHSCOPE_API_KEY=...
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
SILICONFLOW_API_KEY=...
```

- DashScope 读取 `[asr]` 和 `[asr.oss]` 配置。
- Vision API 的 provider/model 读取 `settings.toml`，密钥读取 `.env`。
- 文本与视觉 provider 也可通过 `.env` 中的 `TEXT_*` / `VISION_*` 环境变量覆盖。

完整字段见 [`settings.toml`](settings.toml) 和 [`.env.example`](.env.example)。

## 统一模型入口（Python API）

文本模型与视觉模型都通过 `framelearn.llm` 统一入口调用，由工厂函数根据 `settings.toml` / `.env` 决定具体 provider 和模型。所有内置 provider 都走非 Responses 协议：

- `openai_chat` → `/chat/completions`（DeepSeek、OpenAI、OpenRouter、Kimi、智谱、SiliconFlow、DashScope）
- `anthropic` → `/v1/messages`（Claude、MiniMax Anthropic 兼容端点）
- `gemini` → `:generateContent`（Google Gemini）

```python
from framelearn.llm import complete, complete_async, create_llm_client

# 统一入口：purpose 选择文本或视觉
answer = complete("text", "解释一下卷积层")
answer = complete("vision", "这张图适合做教材插图吗？", images=["frame.jpg"])
answer = await complete_async("text", "总结这段字幕")

# 工厂：显式拿到具体 client
text_client = create_llm_client("text")      # 读取 [text] / TEXT_*
vision_client = create_llm_client("vision")  # 读取 [vision] / VISION_*

text_client.complete("写一段博客")
await vision_client.complete_interleaved_async([
    {"type": "text", "text": "看这张图"},
    {"type": "image", "path": "frame.jpg"},
])
```

- `provider` 支持别名，例如 `minimax`、`minimaxi`、`moonshot`、`anthropic`、`qwen`。
- 模型能力目录来自 cc-switch 的 `piModelCatalog` 思路：已知 text-only 模型（如 `deepseek-chat`）被配置为视觉模型时，工厂会直接报错并列出 image-capable 候选。
- 旧的 `provider_adapter.call_text_llm()` / `call_llm()` 等 API 保持兼容。

## 使用

## 使用

```bash
# 本地视频
framelearn run /absolute/path/tutorial.mp4

# 使用已有字幕，跳过 ASR
framelearn run /absolute/path/tutorial.mp4 --subtitle /absolute/path/tutorial.srt

# 自然语言入口
framelearn "处理这个视频 /absolute/path/tutorial.mp4"

# 通用问答
framelearn ask "解释一下这个项目的结构"

# 会话管理
framelearn session list              # 列出所有会话
framelearn session info              # 查看数据库统计
framelearn session delete <id>       # 删除指定会话
framelearn session clear             # 清空所有会话（需确认）
framelearn session export <id>       # 导出会话为 JSON

# 无参数时进入 REPL
framelearn
```

传统命令包括 `run`、`ask`、`summarize`、`session`、`help`。自然语言解析优先使用有效的 `TEXT_PROVIDER` + `TEXT_API_KEY`；没有时使用本地规则，将多数非视频请求路由为 `ask`。

## 实际输出

```text
output/<视频文件名>/
├── srt_picture.md                 # SRT 结构 + 时间戳 + 配图
├── blog.md                        # 博客式叙述 + 同样的配图
├── src/
│   ├── subtitle.txt               # 清洗后的纯文本
│   ├── subtitle.srt               # ASR/输入提供 SRT 时存在
│   ├── frame_00h01m30s.jpg        # 启发式截帧（带时间戳）
│   ├── extra_frame_xxx.jpg        # 锚点流水线按需精准补截的帧
│   ├── subtitle_manifest.json     # 字幕缓存校验
│   └── keyframe_manifest.json     # 启发式帧摘要（用于重跑跳过 ffmpeg）
├── temp/                          # DashScope 临时切片 + ffmpeg 中间帧
└── run-report.json                # 降级事件 / 缓存命中汇总
```

缓存会影响重跑：已有 `subtitle.srt` + `subtitle.txt` 会跳过 ASR，已有 `src/*.jpg` + `keyframe_manifest.json` 会跳过启发式截帧。字幕缓存与 `[asr]` 相关；关键帧缓存与 `[heuristic]` 相关。如需完全重跑，应先备份并删除对应缓存。

## 处理链路

```text
CLI / REPL
  → CommandParser
  → CommandRouter
  → VideoPipeline
      → 已有字幕，或 FFmpeg 提取音轨 → ASRAdapter
      → SubtitleCleaner
      → FFmpegHelper 抽帧
      → KeyframeDeduplicator
      → ChunkedDocGenerator
          → SRTChunker 按时长分块
          → 插入启发式帧标记
          → BlogGenerator 文本生成 + 帧锚点
          → 程序校验锚点 / FFmpeg 补截
          → VisionFrameEvaluator 验图（retake/keep/caption）
          → MDAssembler 输出双 Markdown
```

## 博客锚点流水线（0816 设计）

核心流程图（来自 `framelearn_docs/0816.md`）：

```text
video
  │
  ├── ASR ──→ raw SRT ────────────────────────┐
  │                                            │
  └── FFmpeg 启发式截帧 ───────────────────────┤
                                               ▼
                                    程序生成 annotated SRT-MD
                                    （保留 raw SRT，不覆盖）
                                               │
                                               ▼
                                          SRTChunker
                                               │
                                               ▼
                                        BlogGenerator（文本模型）
                                        输入：annotated SRT chunk
                                        输出：
                                        ├─ blog_markdown
                                        │   （含 [[FRAME:id@timestamp]]）
                                        └─ frame_requests
                                               │
                                               ▼
                                       程序校验锚点
                                       ├─ 已有帧：绑定真实路径
                                       ├─ 无合适帧：FFmpeg 精准截帧
                                       └─ 非法锚点：删除并报告
                                               │
                                               ▼
                                   VisionFrameEvaluator（视频模型）
                                   对每张候选帧输出：
                                   ├─ anchor_id
                                   ├─ retake / retake_timestamp
                                   ├─ keep_image
                                   ├─ content_type
                                   ├─ caption
                                   └─ text_representation
                                               │
                                    retake=true → 循环补截
                                    retake=false → 进入拼装
                                               │
                                               ▼
                                          MDAssembler
                                               │
                        ┌──────────────────────┼──────────────────────────────────────────┐
                        ▼                      ▼                                          ▼
              结构图/公式/表格          text_slide/terminal                       face/blank/transition
              插图 + caption  插图 + caption（如有）+ text_representation（如有）         删除锚点
```

> 实现顺序采用最终确认的方案 A：先用 raw SRT 切 chunk，再向每个 chunk 插入候选帧标记。上图中的 annotated SRT-MD 是 BlogGenerator 实际看到的输入效果，磁盘上的 raw SRT 不会被覆盖。

### VisionFrameEvaluator 字段说明

| 字段 | 含义 | 处理规则 |
|------|------|----------|
| `anchor_id` | 锚点 ID，与 `[[FRAME:id@timestamp]]` 对应 | 程序用它绑定 blog 文本与图片 |
| `retake` | 是否要求重新截帧 | `true` 时 FFmpeg 按 `retake_timestamp` 补截后再次验图 |
| `retake_timestamp` | 重截的精准时间点 | 仅 `retake=true` 时有效 |
| `keep_image` | 图片是否保留 | `true` 保留图片；`false` 删除整个锚点 |
| `content_type` | 图片内容类型 | `text_slide` / `terminal` / `code` / `diagram` / `formula` / `table` / `screenshot` / `face` / `blank` / `transition` / `other` |
| `caption` | 图片说明 | `keep_image=true` 且非空时插入图片下方 |
| `text_representation` | 图片中的文字内容 | `keep_image=true` 且非空时继续插入 caption 下方 |

### BlogGenerator 锚点示例

```text
卷积层负责提取局部特征。
[[FRAME:a1@53.0]]

实际工程中更常用 3x3 卷积。
[[FRAME:a2@633.8]]
```

程序校验锚点时：

```text
已有候选帧，时间差 ≤ frame_match_tolerance → 直接复用
已有候选帧，时间差过大 → FFmpeg 按精准时间戳补截
没有候选帧 → FFmpeg 按精准时间戳补截
非法锚点 → 删除并写入 run-report.json
```


## 测试

```bash
uv run pytest
```

## 文档

> 说明：历史文档链接暂未随当前重构更新，后续应补充架构、流水线与隐私生命周期文档。

## License

MIT
