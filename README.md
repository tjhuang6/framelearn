# FrameLearn

[English](README.en.md) | 中文

将编程教学视频自动转换为结构化图文教材。提取音频、转写字幕、抽取关键帧，用 LLM 生成可独立学习的 Markdown 教材，遇到不懂的地方随时向 AI 提问。

## 它能做什么

1. **转录**：提取视频音轨，通过硅基流动 SenseVoice 或阿里云百炼转录为带时间戳的字幕
2. **抽帧**：FFmpeg 场景检测 + 定时保底抽帧，感知哈希去重；可选 LLM Agent 精选有价值的帧
3. **生成**：关键帧 + 字幕分段送给 LLM，输出三种风格的 Markdown 教材
4. **问答**：生成教材后，随时针对视频内容向 AI 提问

## 输出格式

```
output/视频名称/
  index.md               # 主教材（章节结构、要点、代码示例、关键帧引用）
  src/
    frame_00h01m30s.jpg  # 关键帧（时间戳命名）
    frame_00h03m15s.jpg
    subtitle.txt         # 清洗后字幕文本
```

## 快速上手

### 1. 安装依赖

```bash
# 安装 FFmpeg（必需）
brew install ffmpeg       # macOS
# apt install ffmpeg      # Ubuntu

# 安装 Python 依赖
uv sync
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入 API Key：

```bash
cp .env.example .env
```

最少配置：

```bash
# ASR 语音识别（硅基流动，国内直接访问）
SILICONFLOW_API_KEY=your_siliconflow_key_here

# Vision/文字模型（api 模式下需要；appserver 模式复用本地 Codex 会话，无需 key）
DEEPSEEK_API_KEY=your_deepseek_key_here
```

`settings.toml` 控制运行参数：

```toml
[runtime]
vision_mode = "appserver"   # appserver（复用 Codex）或 api（直接调用）
asr_provider = "siliconflow"

[doc_generation]
mode = "visual_script"      # visual_script | notes | textbook
segment_duration = 90       # 每段时长（秒）

[agent]
keyframe_selection = false  # true = LLM 精选关键帧（更准，更慢）
quality_review = false      # true = 生成后 LLM 评审质量并重试
```

### 3. 运行

```bash
# 自然语言（推荐）
framelearn "处理这个视频 /path/to/tutorial.mp4"
framelearn "第 3 章讲了什么"

# 传统命令
framelearn run /path/to/tutorial.mp4
framelearn ask "为什么要用虚拟环境"
framelearn summarize
```

## B 站视频说明

B 站下载的视频通常是音视频分离的两个文件：

```
tutorial-30080.mp4   # 视频流（无音轨）
tutorial-30280.mp3   # 音频流
```

FrameLearn 自动检测并配对同目录下的伴随音频，无需手动合并。

## 文档风格

通过 `doc_generation.mode` 选择：

| 模式 | 说明 |
|------|------|
| `visual_script` | 顺序图文讲稿，保留讲解顺序，适合跟着视频复习 |
| `notes` | 课堂笔记风格，bullet points，快速浏览 |
| `textbook` | 正式教材，按知识点重排，系统学习 |

## 架构

```
用户输入
  ↓
CommandParser（意图识别）
  ↓
CommandRouter（命令路由）
  ↓
VideoPipeline
  ├── FFmpegHelper          音轨提取 + 关键帧抽取
  ├── ASRAdapter            语音转文字（硅基流动 / 百炼）
  ├── SubtitleCleaner       字幕清洗
  ├── KeyframeDeduplicator  感知哈希去重
  ├── AgentKeyframeSelector LLM 精选关键帧（可选）
  ├── SegmentSplitter       按时长切段 + 分配关键帧
  └── DocumentGenerator     生成 Markdown 教材（支持质量评审重试）
```

## 技术栈

| 用途 | 工具 |
|------|------|
| 音视频处理 | FFmpeg |
| 语音识别 | 硅基流动 FunAudioLLM/SenseVoiceSmall 或阿里云百炼 Qwen-Audio |
| 视觉 / 文字分析 | Codex app-server 或直接 Vision API（DeepSeek / Qwen） |
| 关键帧去重 | imagehash（感知哈希 pHash） |
| 配置管理 | settings.toml + .env |
| 运行时 | Python 3.11+，uv |

## 测试

```bash
uv run pytest test/ -v
```

## 文档

- [技术架构](docs/architecture.md)
- [视频流水线设计](openspec/changes/video-pipeline/design.md)

## 开源协议

MIT
