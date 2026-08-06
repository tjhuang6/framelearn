# FrameLearn

[English](README.en.md) | 中文

FrameLearn 将本地编程教学视频转换为带关键帧的 Markdown 学习材料。当前实现覆盖音轨提取、ASR、字幕清洗、关键帧抽取/去重、按时间分段生成，以及基于 Codex app-server 的通用问答。

## 当前能力

- 处理本地 `.mp4`、`.mkv`、`.avi`、`.mov`、`.flv`、`.wmv`、`.webm` 视频。
- 自动识别视频内音轨；若 B 站下载文件音视频分离，会在同目录查找同前缀的 `.mp3`、`.m4a` 或 `.aac`。
- ASR 支持：
  - 阿里云百炼 DashScope：长音频分片、OSS 临时上传、异步转写、断点记录和 SRT 时间戳。
  - 硅基流动 SenseVoice：实现简单，但不返回时间戳。
- 可通过 `--subtitle` 直接使用已有 `.txt`、`.srt` 或 `.vtt`，跳过 ASR。
- FFmpeg 场景检测与固定间隔抽帧同时执行，再用 pHash 去重。
- 长字幕或关键帧较多时自动按段生成并缓存，可中断后复用已生成段落。
- 每次运行固定生成 `notes.md`，并按配置生成 `index.md`（`visual_script`、`notes` 或 `textbook`）。
- `ask` 可通过 Codex app-server 或兼容 API 回答通用问题。

## 尚未实现或受限的能力

- YouTube/Bilibili URL 会被识别和校验，但在线下载尚未实现；请先下载到本地。
- `ask` 当前不是“只检索已生成教材”的 RAG 问答；它是工作目录中的通用 Codex/API 对话。
- 当前 FrameLearn 的 app-server `turn/start` 只发送文字。文档生成若要让模型看到关键帧，应使用 `runtime.vision_mode = "api"`。
- Agent 关键帧选择为实验功能。API 图像评估路径目前引用了尚不存在的 `ProviderAdapter` 类；保持默认关闭。

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

当前仓库 `settings.toml` 的默认路径是：

```toml
[runtime]
text_mode = "appserver"
vision_mode = "api"
vision_provider = "siliconflow"
vision_model = "Qwen/Qwen3.6-35B-A3B"

[asr]
provider = "dashscope"
model = "qwen-audio-3.0-asr-flash-filetrans"

[doc_generation]
mode = "visual_script"
segment_duration = 90
max_keyframes_per_segment = 10

[agent]
keyframe_selection = false
quality_review = false
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
├── index.md                       # doc_generation.mode 指定的主文档
├── notes.md                       # 每次额外生成的笔记版
├── src/
│   ├── subtitle.txt               # 清洗后的纯文本
│   ├── subtitle.srt               # ASR/输入提供 SRT 时存在
│   └── frame_00h01m30s.jpg        # 带整秒时间戳的关键帧
├── segments_<mode>/               # 触发分段生成时的段落缓存
└── temp/                          # DashScope 临时切片；是否保留由 asr.keep_temp_files 控制
```

缓存会影响重跑：已有 `subtitle.srt` + `subtitle.txt` 会跳过 ASR，已有 `frame_*.jpg` 会跳过抽帧，已有分段 Markdown 会跳过对应 LLM 调用。如需完全重跑，应先备份并删除对应缓存。

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
