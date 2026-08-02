# 技术架构

## 概述

FrameLearn 是一个将编程教学视频自动转换为结构化图文教材的工具。用户输入自然语言或传统命令，系统完成音频转写、关键帧提取、字幕清洗、文档生成全流程，最终输出可独立学习的 Markdown 教材。

整体数据流：

```
用户输入（自然语言或传统命令）
   ↓
CommandParser（意图识别）
   ↓
CommandRouter（命令路由）
   ↓
VideoPipeline（主流水线）
   ├── Step 1: FFmpegHelper      提取音轨 + 场景检测抽帧
   ├── Step 2: ASRAdapter        语音转文字（带时间戳）
   ├── Step 3: SubtitleCleaner   字幕清洗（去口水词、全半角等）
   ├── Step 4: KeyframeDedup     感知哈希去重
   ├── Step 5: SegmentSplitter   按时长切分段落 + 分配关键帧
   └── Step 6: DocumentGenerator 生成 Markdown 教材
```

---

## 各模块说明

### CommandParser

**文件**：`framelearn/command_parser.py`

将用户的自然语言输入解析为结构化命令。例如"帮我处理这个视频 /path/to/video.mp4"解析为 `{command: "run", video_path: "..."}`。

- 传统格式（`run`, `ask`, `summarize`）：正则直接匹配，不调用 LLM
- 自然语言：调用 LLM 进行意图识别

---

### CommandRouter

**文件**：`framelearn/router.py`

接收解析后的命令，分发给对应处理器：

| 命令 | 处理器 |
|------|--------|
| `run` | `VideoPipeline` |
| `ask` | 问答模块 |
| `summarize` | 摘要模块 |

---

### VideoPipeline

**文件**：`framelearn/pipeline/video_pipeline.py`

主流水线，协调所有子模块完成视频到教材的转换。

**输入**：视频文件路径（+ 可选字幕文件）  
**输出**：`PipelineResult`（教材 Markdown、关键帧路径列表、字幕文本）

流程：
1. FFmpeg 检查 → 提取音轨（自动检测 B 站伴随音频）
2. ASR 转写 → 得到带时间戳的 `TranscriptResult`
3. 字幕清洗
4. 关键帧提取 + 感知哈希去重
5. （可选）Agent 关键帧精选
6. SegmentSplitter 切段 + 分配帧
7. DocumentGenerator 逐段生成，拼接输出

---

### FFmpegHelper

**文件**：`framelearn/pipeline/ffmpeg_helper.py`

封装所有 FFmpeg 操作。

| 方法 | 说明 |
|------|------|
| `check_installed()` | 检查 FFmpeg 是否在 PATH |
| `has_audio_stream()` | 检测视频是否含音轨 |
| `find_companion_audio()` | 查找同目录伴随音频（B 站分离文件） |
| `extract_audio()` | 提取音轨到 `.m4a` |
| `extract_keyframes()` | 场景检测抽帧 + 定时保底抽帧 |
| `capture_single_frame()` | 精确截取指定时间戳的单帧 |

关键帧返回格式：`list[tuple[Path, float]]`（帧路径, 时间戳秒数）

---

### ASRAdapter

**文件**：`framelearn/pipeline/asr_adapter.py`  
**后端**：`framelearn/pipeline/asr_backends/`

支持两个 ASR 后端，通过 `runtime.asr_provider` 配置切换：

| 后端 | 说明 |
|------|------|
| `siliconflow` | 硅基流动 FunAudioLLM/SenseVoiceSmall（默认） |
| `dashscope` | 阿里云百炼 Qwen-Audio（支持长音频，通过 OSS 上传） |

输出 `TranscriptResult`，含完整文本、`TranscriptSegment` 列表（text / start / end）、SRT 格式字幕。

---

### SubtitleCleaner

**文件**：`framelearn/pipeline/subtitle_cleaner.py`

字幕后处理：

- 去除噪音标签（`[音乐]`、`（掌声）` 等）
- 全角标点 → 半角
- 合并连续重复行
- 口水词过滤（嗯、啊、那个……）
- 空白规范化（多余空格、多余换行）

---

### KeyframeDeduplicator

**文件**：`framelearn/pipeline/keyframe_dedup.py`

基于感知哈希（pHash）去除视觉相似帧：

- 计算每帧 pHash
- 汉明距离 < 阈值（默认 0.9）视为重复
- 支持 `max_frames` 上限
- 输入输出均为 `list[tuple[Path, float]]`

---

### AgentKeyframeSelector（可选）

**文件**：`framelearn/pipeline/agent_keyframe_selector.py`

在 `agent.keyframe_selection = true` 时激活，替代纯启发式抽帧：

1. **启发式预过滤**：正则检测视觉关键词（"如图"、"代码"、"PPT"……），无关键词直接跳过
2. **LLM 决策**：问 LLM "这段字幕需要截图吗？" → 返回 `{need_frame: bool}`
3. **截帧**：调用 `FFmpegHelper.capture_single_frame()`
4. **LLM 评估**：问 LLM "这张图有教学价值吗？" → 返回 `{keep: bool}`
5. **去重**：±2 秒内不重复截帧

---

### SegmentSplitter

**文件**：`framelearn/pipeline/segment_splitter.py`

将完整字幕和关键帧切分为若干段，每段独立送给 DocumentGenerator：

- **SRT 模式**：解析时间戳，按 `segment_duration`（默认 90 秒）切段
- **字数估算 fallback**：无时间戳时按字数切分
- 每段分配其时间范围内的关键帧

---

### DocumentGenerator

**文件**：`framelearn/pipeline/doc_generator.py`

核心生成器，将关键帧 + 字幕送给 LLM，输出 Markdown 教材。

支持两种运行模式（`runtime.vision_mode`）：

| 模式 | 说明 |
|------|------|
| `appserver`（默认） | 通过 Codex app-server 调用（复用本地 Claude Code 会话） |
| `api` | 直接调用 Vision API（DeepSeek / Qwen 等） |

支持三种文档风格（`doc_generation.mode`）：

| 风格 | 说明 |
|------|------|
| `visual_script`（默认） | 顺序图文讲稿，保留讲解顺序 |
| `notes` | 课堂笔记，bullet points |
| `textbook` | 正式教材，知识点重排 |

**质量评审**（`agent.quality_review = true`）：

生成后自动评审，不合格则重试（最多 3 次，第 3 次失败降级保存原始字幕）：
- 内容过短（< 100 字）
- 包含未清洗口水词
- 字幕提到图但无关键帧插入

---

### ProviderAdapter

**文件**：`framelearn/provider_adapter.py`

统一的 LLM 调用接口，抽象不同 provider 的 API 差异。`api` 模式下由 DocumentGenerator 调用。

---

### App-Server 模块

**目录**：`framelearn/app_server/`

复刻 Codex app-server 协议，实现本地 JSONRPC 通信：

| 文件 | 说明 |
|------|------|
| `session.py` | 会话管理，`run_turn()` 发送 prompt 并等待结果 |
| `jsonrpc_client.py` | JSONRPC 2.0 客户端 |
| `projector.py` | 工具调用投影（解析 LLM 的 tool_use 响应） |
| `persistence.py` | 会话状态持久化 |
| `runtime.py` | 运行时配置 |

---

## 配置

### settings.toml

```toml
[runtime]
vision_mode = "appserver"    # appserver | api
vision_provider = "deepseek" # api 模式下的 provider
vision_model = "deepseek-reasoner"
asr_provider = "siliconflow" # siliconflow | dashscope
text_mode = "appserver"

[video]
output_dir = "./output"
scene_threshold = 0.3        # 场景检测灵敏度（0-1，越小越灵敏）
fallback_interval = 30       # 无场景变化时保底抽帧间隔（秒）
max_keyframes = 100

[doc_generation]
mode = "visual_script"       # visual_script | notes | textbook
segment_duration = 90        # 分段时长（秒）
max_keyframes_per_segment = 10

[agent]
keyframe_selection = false   # true = LLM 精选关键帧（慢但精准）
quality_review = false       # true = 生成后 LLM 评审并重试
upgrade_model = ""           # 评审失败升级模型（留空不升级）
```

### .env

```bash
# ASR（必需）
SILICONFLOW_API_KEY=sk-...

# Vision API（api 模式下必需）
DEEPSEEK_API_KEY=sk-...

# 阿里云百炼 ASR（dashscope 后端）
DASHSCOPE_API_KEY=sk-...
ALIYUN_OSS_BUCKET=...
ALIYUN_OSS_REGION=...
```

---

## 目录结构

```
framelearn/
├── pyproject.toml
├── settings.toml
├── .env
├── docs/
│   ├── architecture.md        # 本文档
│   └── ...
├── framelearn/
│   ├── __main__.py            # 入口
│   ├── command_parser.py      # 自然语言意图识别
│   ├── router.py              # 命令路由
│   ├── config.py              # 配置加载（settings.toml + .env）
│   ├── provider_adapter.py    # 统一 LLM 调用接口
│   ├── app_server/            # Codex app-server 协议实现
│   │   ├── session.py
│   │   ├── jsonrpc_client.py
│   │   ├── projector.py
│   │   ├── persistence.py
│   │   └── runtime.py
│   └── pipeline/              # 视频处理流水线
│       ├── video_pipeline.py          # 主流水线
│       ├── ffmpeg_helper.py           # FFmpeg 封装
│       ├── asr_adapter.py             # ASR 适配器
│       ├── asr_backends/
│       │   ├── siliconflow.py         # 硅基流动后端
│       │   └── dashscope.py           # 百炼后端
│       ├── subtitle_cleaner.py        # 字幕清洗
│       ├── keyframe_dedup.py          # 感知哈希去重
│       ├── segment_splitter.py        # 分段 + 关键帧分配
│       ├── doc_generator.py           # 文档生成器
│       └── agent_keyframe_selector.py # Agent 关键帧精选（可选）
├── test/
│   └── src/
│       ├── test_pipeline.py
│       ├── test_agent_keyframe.py
│       ├── test_router.py
│       └── ...
└── output/
    └── <视频名>/
        ├── index.md           # 生成的教材
        └── src/
            ├── frame_00h01m30s.jpg
            └── subtitle.txt
```

---

## 输出格式

```
output/视频名称/
  index.md               # 主教材（Markdown）
  src/
    frame_00h00m30s.jpg  # 关键帧（时间戳命名）
    frame_00h01m00s.jpg
    subtitle.txt         # 清洗后字幕
```

教材示例结构（visual_script 模式）：

```markdown
## 变量与类型

Python 中变量无需声明类型，赋值即创建：

![变量赋值示例](src/frame_00h02m15s.jpg)

```python
x = 10
name = "Alice"
```

赋值语句左边是变量名，右边是值。整数、字符串、布尔值……
```
