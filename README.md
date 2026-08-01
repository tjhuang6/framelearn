# FrameLearn

[English](README.en.md) | 中文

一个 AI Agent，将编程教学视频自动转换为结构化图文教材——让你按自己的节奏学习，遇到不懂的地方随时向 AI 提问。

## 它能做什么

1. **转录**：提取视频音轨，通过硅基流动 SenseVoice 转录为文字
2. **抽帧**：FFmpeg 场景检测 + 定时保底抽帧，感知哈希去重
3. **生成**：关键帧 + 字幕送给 GPT / Claude，输出结构化 Markdown 教材
4. **问答**：生成教材后，随时针对视频内容向 AI 提问

## 输出格式

```
output/视频名称/
  index.md          # 主教材（章节结构、要点、代码示例、关键帧引用）
  src/
    frame_001.jpg   # 关键帧截图
    frame_002.jpg
    ...
    subtitle.txt    # 清洗后的字幕文本
```

## 快速上手

### 1. 安装依赖

```bash
# 安装 FFmpeg（必需）
brew install ffmpeg          # macOS
# apt install ffmpeg         # Ubuntu

# 安装 Python 依赖
uv sync
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入 API Key：

```bash
cp .env.example .env
```

最少需要配置两项：

```bash
# 文字/视觉模型（通过 Codex app-server，无需额外 key）
# 或直接配置 API key
TEXT_PROVIDER=deepseek
TEXT_API_KEY=your_deepseek_key_here

# ASR 语音识别（硅基流动，国内可直接访问）
SILICONFLOW_API_KEY=your_siliconflow_key_here
```

运行模式和视频参数在 `settings.toml` 中配置：

```toml
[runtime]
text_mode = "appserver"   # appserver（通过 Codex）或 api（直接调用）
vision_mode = "appserver"

[video]
output_dir = "./output"
scene_threshold = 0.3     # 场景切换灵敏度
max_keyframes = 100
```

### 3. 运行

```bash
# 自然语言（推荐）
framelearn "处理这个视频 /path/to/tutorial.mp4"
framelearn "第 3 章讲了什么"

# 传统命令格式
framelearn run /path/to/tutorial.mp4
framelearn ask "为什么要用虚拟环境"
framelearn summarize
framelearn help
```

## B 站视频说明

B 站下载的视频通常是音视频分离的两个文件：

```
tutorial-30080.mp4   # 视频流（无音轨）
tutorial-30280.mp3   # 音频流
```

FrameLearn 会自动检测并配对同目录下的伴随音频文件，无需手动合并。

## 架构

```
用户输入
  ↓
CommandParser（自然语言意图识别）
  ↓
CommandRouter（命令分发）
  ↓
VideoPipeline
  ├── FFmpegHelper     音轨提取 + 关键帧抽取
  ├── ASRAdapter       语音转文字（硅基流动 SenseVoice）
  ├── SubtitleCleaner  字幕清洗（移植自 Bilitato）
  ├── KeyframeDedup    感知哈希去重
  └── DocumentGenerator  生成 Markdown 教材
```

## 技术栈

| 用途 | 工具 |
|------|------|
| 音视频处理 | FFmpeg |
| 语音识别 | 硅基流动 FunAudioLLM/SenseVoiceSmall |
| 视觉 / 文字分析 | Codex app-server（GPT-5.6）或直接 API |
| 关键帧去重 | imagehash（感知哈希） |
| 配置管理 | settings.toml + .env |
| 运行时 | Python 3.11+，uv |

## 文档

- [技术架构](docs/architecture.md)
- [视频流水线设计](openspec/changes/video-pipeline/design.md)
- [Bilitato 迁移决策](docs/decisions/bilitato-to-framelearn.md)
- [App-Server 多模态流水线](docs/app-server-video-multimodal-pipeline.md)

## 开源协议

MIT
