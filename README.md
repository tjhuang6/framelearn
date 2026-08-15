# FrameLearn

[English](README.en.md) | 中文

FrameLearn 将本地编程教学视频转换为带关键帧的 Markdown 学习材料。当前实现覆盖音轨提取、ASR、字幕清洗、分块（30 分钟）LLM 调用、启发式 + 视觉两阶段关键帧选择、双 Markdown 输出，以及基于 Codex app-server 的通用问答。

## 当前能力

- 处理本地 `.mp4`、`.mkv`、`.avi`、`.mov`、`.flv`、`.wmv`、`.webm` 视频。
- 自动识别视频内音轨；若 B 站下载文件音视频分离，会在同目录查找同前缀的 `.mp3`、`.m4a` 或 `.aac`。
- ASR 支持：
  - 阿里云百炼 DashScope：长音频分片、OSS 临时上传、异步转写、断点记录和 SRT 时间戳。
  - 硅基流动 SenseVoice：实现简单，但不返回时间戳。
- 可通过 `--subtitle` 直接使用已有 `.txt`、`.srt` 或 `.vtt`，跳过 ASR。
- **分块 LLM 文档生成**：将 SRT 按 30 分钟切段（`[chunking] segment_minutes`），每段一次性发给文本 LLM 去口水词，再用 Qwen3-VL 视觉模型两阶段决策（先看图 + 文本挑时间戳，再回头筛掉重复/无意义帧）。30 分钟视频总 LLM 调用 ≤ 3 次/段数。
- 每次运行固定生成两个 Markdown：`srt_picture.md`（保留 SRT 段结构、时间戳 + 配图）和 `blog.md`（博客式叙述 + 同样的配图）。
- 启发式截帧（ffmpeg 场景检测 + pHash 去重）结果会被 SHA256 摘要写入 manifest，配置或视频变化时自动重跑。
- `ask` 可通过 Codex app-server 或兼容 API 回答通用问题。

## 尚未实现或受限的能力

- YouTube/Bilibili URL 会被识别和校验，但在线下载尚未实现；请先下载到本地。
- `ask` 当前不是“只检索已生成教材”的 RAG 问答；它是工作目录中的通用 Codex/API 对话。
- 当前 FrameLearn 的 app-server `turn/start` 只发送文字。文档生成若要让模型看到关键帧，应使用 `runtime.vision_mode = "api"`。
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
[runtime]
text_mode = "appserver"
vision_mode = "api"
vision_provider = "siliconflow"
vision_model = "Qwen/Qwen3.6-35B-A3B"

[asr]
provider = "dashscope"
model = "qwen-audio-3.0-asr-flash-filetrans"

[chunking]
segment_minutes = 30          # 每段最长视频时长
max_images_per_chunk = 50     # 单段最多保留的图片数
concurrency = 5               # 段内并发 LLM 调用上限

[text_clean]
filler_words = ["那么", "就是说", "大家注意", "咱们", "啊", "嗯", "这个", "那个", "对吧"]

[heuristic]
scene_threshold = 0.4         # ffmpeg 场景检测阈值（越低越敏感）
similarity_threshold = 0.95   # pHash 去重阈值
max_frames = 200

[doc_gen]
srt_filename = "srt_picture.md"   # 保留 SRT 结构 + 配图
blog_filename = "blog.md"         # 博客式叙述 + 同样的配图
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
- `text_mode = "appserver"` 需要本机已安装并配置 `codex` CLI。

完整字段见 [`settings.toml`](settings.toml) 和 [`.env.example`](.env.example)。

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
│   ├── extra_frame_xxx.jpg        # Stage1 按需补充的帧
│   ├── subtitle_manifest.json     # 字幕缓存校验
│   └── keyframe_manifest.json     # 启发式帧摘要（用于重跑跳过 ffmpeg）
├── temp/                          # DashScope 临时切片 + ffmpeg 中间帧
└── run-report.json                # 降级事件 / 缓存命中汇总
```

缓存会影响重跑：已有 `subtitle.srt` + `subtitle.txt` 会跳过 ASR，已有 `src/*.jpg` + `keyframe_manifest.json` 会跳过启发式截帧，配置变化（`[chunking]` / `[text_clean]` / `[doc_gen]` / `[heuristic]`）或帧列表变化会自动失效对应缓存。如需完全重跑，应先备份并删除对应缓存。

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
      → 可选 AgentKeyframeSelector
      → DocumentGenerator
          → 短内容单次生成
          → 长内容 SegmentSplitter 分段、缓存、重试、合并
```

## 测试

```bash
uv run pytest
```

## 文档

- [文档索引与状态](docs/README.md)
- [当前技术架构](docs/architecture.md)
- [流水线实现说明](docs/pipeline-overview.md)
- [AntiVibe 技术报告](mine/antivibe/antivibe-technical-report.md)
- [Codex app-server 指南](docs/codex-app-server-guide.md)
- [隐私与数据生命周期说明](docs/privacy-and-data-lifecycle.md) ⭐

## License

MIT
