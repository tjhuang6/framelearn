# 技术架构

## 概述

FrameLearn 是一个多步骤 AI Agent 系统，基于 [Hello-Agents](https://github.com/datawhalechina/Hello-Agents) 框架构建。与固定流水线不同，每个组件会根据观察到的内容自主决定下一步操作，核心循环采用 **ReAct 范式**（Reasoning + Acting）。

目标输出是一份**可独立学习的 Markdown 教材**，包含整理后的文字讲解和对应的关键帧截图，完全不需要观看原视频。

整体流程：

```
视频 URL
   ↓
工具执行器（预处理阶段）
   ├── Bilibili：优先获取官方字幕 → 无字幕时提取音频 → Whisper 转写
   ├── YouTube：优先 yt-dlp 下载字幕 → 无字幕时提取音频 → Whisper 转写
   └── ffmpeg 提取采样帧
   ↓
规划 Agent        ← 将采样帧（base64）发给 LLM Provider，分析视频结构，制定转换计划
   ↓
工具执行器（全量阶段）
   ├── ffmpeg 按计划提取关键帧
   └── Tesseract OCR 提取帧内文字
   ↓
内容分析器        ← 将帧（base64）发给 LLM Provider，筛选关键帧，按时间戳与文字稿对齐
   ↓
文档生成器        ← 将对齐后的文字稿发给 LLM Provider，整理成教材格式输出
   ↓
问答模块          ← RAG 检索 + 交互式回答用户提问
```

---

## 各模块说明

### 1. 规划 Agent

负责在全量处理之前建立转换计划。基于 HelloAgents 的 `SimpleAgent` 基类实现，采用 ReAct 范式驱动规划循环。

**前提**：规划 Agent 启动时，视频已由工具执行器下载到本地，采样帧已由 ffmpeg 提取完毕。规划 Agent 拿到的输入是这批帧的本地路径，而不是 URL。

ReAct 循环示例：
```
Thought: 需要了解视频的整体结构
Action: send_frames_to_llm(frames=[frame_0s.jpg, frame_60s.jpg, ...])
Observation: LLM 返回：识别到 5 个章节，标题分别为...
Thought: 已有足够信息，制定转换计划
Action: create_plan(chapters=[...])
```

- 将采样帧（base64 编码）连同提问一起发给 LLM Provider
- LLM 分析帧内容，识别主要章节及每章的结构
- 输出**转换计划**：章节列表、估计时间范围、每章的关注重点
- 系统其余部分按此计划执行

为什么需要规划：没有计划，Agent 会盲目处理每一帧。规划器能减少噪音，将精力集中在有意义的片段上。

### 2. 工具执行器

基于 HelloAgents 的工具注册机制，让 Agent 能够按名称调用外部工具。工具在启动时统一注册，Agent 自主决定调用顺序。

工具执行器分两个阶段运行：

**预处理阶段**（规划 Agent 之前）

| 工具 | 底层依赖 | 用途 |
|---|---|---|
| `download_video` | yt-dlp | 从 YouTube 或 Bilibili 下载视频到本地 |
| `fetch_subtitle` | Bilibili API / yt-dlp | 优先获取官方字幕（带时间戳），跳过 Whisper |
| `extract_audio` | ffmpeg | 无字幕时从视频分离音频（.mp3） |
| `transcribe_audio` | Whisper / Groq API | 无字幕时语音转文字，输出带时间戳的文字稿 |
| `extract_sample_frames` | ffmpeg | 每隔固定间隔提取采样帧，供规划 Agent 分析 |

**全量阶段**（规划 Agent 之后）

| 工具 | 底层依赖 | 用途 |
|---|---|---|
| `extract_frames` | ffmpeg | 按转换计划提取指定时间段的帧 |
| `detect_scene_changes` | ffmpeg | 场景检测，找出画面切换点 |
| `run_ocr` | Tesseract | 从帧图像中提取文字（代码、终端输出等） |

Agent 自主决定调用哪些工具以及调用顺序，不遵循硬编码的固定序列。

### 3. 内容分析器

处理提取出的帧，判断哪些值得纳入教材，并将帧与文字稿按时间戳对齐。

关键帧判断标准：
- **代码发生变化**：当前帧的代码与前一帧不同
- **出现报错**：帧中包含可见的错误信息或 traceback
- **终端输出**：执行了命令，输出结果可见
- **新章节开始**：文件名、窗口标题或代码结构发生明显变化

不符合任何标准的帧会被丢弃，保持输出的精简和相关性。

**时间戳对齐**：每个关键帧都有对应的时间戳（秒），文字稿同样带有逐句时间戳。内容分析器按时间段将二者匹配，每段关键帧对应该时间窗口内的文字内容。

### 4. 文档生成器

接收对齐后的"帧 + 文字稿"片段和转换计划，生成最终 Markdown 教材。

每个章节的处理步骤：
1. 写入 `##` 标题和时间范围
2. 将该时间段的文字稿发给 LLM Provider，整理成流畅的书面表达（去除口语化、填充词、重复）
3. 插入关键帧截图
4. 将识别到的代码格式化为带正确语言标签的代码块
5. 添加指向源视频对应时间点的时间戳链接

输出为 `output/` 目录下的单个 `tutorial.md` 文件，以及 `output/frames/` 目录下的关键帧图片。

### 5. 问答模块

允许用户在教程生成后提问，基于 HelloAgents 的记忆与 RAG 系统实现。

- 教程生成后，将内容向量化存入向量数据库（Chroma）
- 接收用户的自然语言问题后，先检索相关段落，再构建回答
- 不将整个教程塞进上下文，而是按需检索，节省 token
- 如果问题涉及截图或视觉细节，返回对应时间戳链接，引导用户自行查看
- 返回基于实际视频内容的准确回答

上下文管理使用 HelloAgents 的 **ContextBuilder（GSSC Pipeline）**，处理长视频时自动压缩历史分析结果，避免上下文溢出。

---

## Agent 循环设计

FrameLearn 的核心循环基于 **ReAct 范式**（Hello-Agents 第 4 章），遵循 **Thought → Action → Observation** 的交替推理：

```
Thought:     分析当前状态，决定下一步
Action:      调用工具（yt-dlp / ffmpeg / OCR / LLM Provider）
Observation: 观察工具返回结果
Thought:     根据结果调整策略，或判断任务完成
```

此循环持续运行，直到 Agent 判断输出达到质量标准，或向用户返回带有清晰说明的失败信息。

HelloAgents `SimpleAgent` 提供了循环的基础骨架，FrameLearn 在此基础上注册自定义工具，扩展视频处理能力。

---

## 自我批评机制

文档生成器产出草稿后，会运行一轮独立的批评检查：

- 计划中的所有章节是否都已覆盖？
- 是否存在没有截图的章节？
- 是否有代码块不完整或被截断？

如果发现问题，Agent 会回头补全，再返回最终输出。

---

## 技术选型说明

**字幕优先策略**
Bilibili 视频优先通过 B站 API 获取官方字幕（JSON 格式，带时间戳），完全跳过音频下载和 Whisper 转写，大幅缩短预处理时间。YouTube 视频优先用 yt-dlp 下载官方或自动生成字幕。仅在无任何字幕时才回退到 Whisper 转写。

**Whisper（OpenAI）**
语音转文字，本地运行，免费。输出带逐句时间戳的文字稿，与帧时间戳对齐是教材生成的核心步骤。中英文均支持。也可配置为 Groq Whisper API（云端，更快）。

**LLM Provider（多提供商支持）**
视觉任务（规划 Agent、内容分析器）需要支持多模态的模型，推荐 Gemini 2.0 Flash（成本极低）。文字任务（文档生成器、问答模块）可使用任意 OpenAI 兼容模型，推荐 DeepSeek（极便宜）。通过 `.env` 分别配置视觉和文字提供商，也支持统一使用同一个提供商。

**HelloAgents 框架（第 7 章）**
提供 Agent 基类、工具注册机制、消息传递协议。FrameLearn 的五个模块均继承自 `SimpleAgent`，避免从零搭建 Agent 骨架。

**ReAct 范式（第 4 章）**
规划 Agent 的核心决策循环。相比固定流水线，ReAct 允许 Agent 根据中间结果动态调整策略。

**记忆与 RAG（第 8 章）**
QA 模块的底层实现。使用 Chroma 向量数据库存储教程内容，问答时按需检索，不依赖全量上下文。

**ContextBuilder / GSSC Pipeline（第 9 章）**
处理长视频时管理上下文窗口，自动压缩历史分析结果，控制 token 消耗。

**yt-dlp**
通过统一接口支持 YouTube 和 Bilibili，自动处理鉴权、限速和格式选择。字幕下载也通过 yt-dlp 完成（YouTube）。

**ffmpeg**
业界标准视频处理工具，用于帧提取、场景变化检测和缩略图生成。

**Tesseract / pytesseract**
从帧中提取文字，尤其适用于因字体渲染导致 LLM 视觉识别可能出错的代码内容。

**uv**
依赖管理工具，比 pip 更快，通过 `uv.lock` 实现确定性安装。

---

## 数据流图

```
┌─────────────┐
│   视频 URL  │
└──────┬──────┘
       │
       ▼
┌─────────────────┐     ┌──────────────────────────────────┐
│   工具执行器    │────▶│  Bilibili API / yt-dlp           │  获取字幕（优先）
│  （预处理阶段） │────▶│  ffmpeg + Whisper / Groq API     │  无字幕时转写音频
│                 │────▶│  ffmpeg                          │  提取采样帧
└──────┬──────────┘     └──────────────────────────────────┘
       │ 采样帧 + 文字稿
       ▼
┌─────────────────┐     ┌──────────────┐
│   规划 Agent    │────▶│ LLM Provider │  帧（base64）→ 章节分析（视觉模型）
└──────┬──────────┘     └──────────────┘
       │ 转换计划
       ▼
┌─────────────────┐     ┌──────────────┐
│   工具执行器    │────▶│  ffmpeg      │  按计划提取关键帧
│  （全量阶段）   │────▶│  Tesseract   │  OCR 提取帧内文字
└──────┬──────────┘     └──────────────┘
       │ 关键帧 + OCR 文字
       ▼
┌──────────────────┐    ┌──────────────┐
│   内容分析器     │───▶│ LLM Provider │  帧（base64）→ 关键帧筛选（视觉模型）
│ （筛选+时间对齐）│    └──────────────┘
└──────┬───────────┘
       │ 关键帧 + 对应时间段文字稿
       ▼
┌──────────────────┐    ┌──────────────┐
│   文档生成器     │───▶│ LLM Provider │  文字稿整理 → 书面表达（文字模型）
└──────┬───────────┘    └──────────────┘
       │
       ▼
┌──────────────────┐
│  tutorial.md     │  文字讲解 + 关键帧截图
└──────┬───────────┘
       │
       ▼
┌──────────────────┐    ┌──────────────┐
│    问答模块      │◀──▶│ LLM Provider │  RAG 检索 + 回答（文字模型）
│  （用户提问）    │    └──────────────┘
└──────────────────┘
```

---

## 目录结构

```
framelearn/
├── README.md
├── README.en.md
├── pyproject.toml
├── uv.lock
├── docs/
│   ├── architecture.md
│   ├── modules/
│   ├── hello-agents/
│   └── decisions/
├── framelearn/
│   ├── __init__.py
│   ├── __main__.py
│   ├── planner.py          # 规划 Agent（继承 HelloAgents SimpleAgent）
│   ├── executor.py         # 工具执行器（HelloAgents 工具注册）
│   ├── analyzer.py         # 内容分析器（关键帧筛选 + 时间戳对齐）
│   ├── generator.py        # 文档生成器（文字稿整理 + 教材输出）
│   ├── qa.py               # 问答模块（HelloAgents RAG + 记忆）
│   └── tools/
│       ├── downloader.py   # yt-dlp 封装
│       ├── subtitle.py     # 字幕获取（Bilibili API + yt-dlp）
│       ├── extractor.py    # ffmpeg 封装（帧 + 音频）
│       ├── transcriber.py  # Whisper / Groq API 封装
│       └── ocr.py          # Tesseract 封装
└── output/
    ├── tutorial.md         # 生成的教材
    └── frames/             # 关键帧截图
```

## 参考资料

- [Hello-Agents 课程](https://github.com/datawhalechina/Hello-Agents) — Agent 框架基础
- [Hello-Agents 内容总结](hello-agents/hello-agents-summary.md) — 各章节知识点与 FrameLearn 的对应关系

---

## 各模块说明

### 1. 规划 Agent

负责在全量处理之前建立转换计划。基于 HelloAgents 的 `SimpleAgent` 基类实现，采用 ReAct 范式驱动规划循环。

**前提**：规划 Agent 启动时，视频已由工具执行器下载到本地，采样帧已由 ffmpeg 提取完毕。规划 Agent 拿到的输入是这批帧的本地路径，而不是 URL。

ReAct 循环示例：
```
Thought: 需要了解视频的整体结构
Action: send_frames_to_claude(frames=[frame_0s.jpg, frame_60s.jpg, ...])
Observation: Claude 返回：识别到 5 个章节，标题分别为...
Thought: 已有足够信息，制定转换计划
Action: create_plan(chapters=[...])
```

- 将采样帧（base64 编码）连同提问一起发给 Claude API
- Claude 分析帧内容，识别主要章节及每章的结构
- 输出**转换计划**：章节列表、估计时间范围、每章的关注重点
- 系统其余部分按此计划执行

为什么需要规划：没有计划，Agent 会盲目处理每一帧。规划器能减少噪音，将精力集中在有意义的片段上。

### 2. 工具执行器

基于 HelloAgents 的工具注册机制，让 Agent 能够按名称调用外部工具。工具在启动时统一注册，Agent 自主决定调用顺序。

工具执行器分两个阶段运行：

**预处理阶段**（规划 Agent 之前）

| 工具 | 底层依赖 | 用途 |
|---|---|---|
| `download_video` | yt-dlp | 从 YouTube 或 Bilibili 下载视频到本地 |
| `extract_audio` | ffmpeg | 从视频中分离音频（.mp3） |
| `transcribe_audio` | Whisper | 语音转文字，输出带时间戳的文字稿 |
| `extract_sample_frames` | ffmpeg | 每隔固定间隔提取采样帧，供规划 Agent 分析 |

**全量阶段**（规划 Agent 之后）

| 工具 | 底层依赖 | 用途 |
|---|---|---|
| `extract_frames` | ffmpeg | 按转换计划提取指定时间段的帧 |
| `detect_scene_changes` | ffmpeg | 场景检测，找出画面切换点 |
| `run_ocr` | Tesseract | 从帧图像中提取文字（代码、终端输出等） |

Agent 自主决定调用哪些工具以及调用顺序，不遵循硬编码的固定序列。

### 3. 内容分析器

处理提取出的帧，判断哪些值得纳入教材，并将帧与文字稿按时间戳对齐。

关键帧判断标准：
- **代码发生变化**：当前帧的代码与前一帧不同
- **出现报错**：帧中包含可见的错误信息或 traceback
- **终端输出**：执行了命令，输出结果可见
- **新章节开始**：文件名、窗口标题或代码结构发生明显变化

不符合任何标准的帧会被丢弃，保持输出的精简和相关性。

**时间戳对齐**：每个关键帧都有对应的时间戳（秒），Whisper 输出的文字稿同样带有逐句时间戳。内容分析器按时间段将二者匹配，每段关键帧对应该时间窗口内的文字内容。

### 4. 文档生成器

接收对齐后的"帧 + 文字稿"片段和转换计划，生成最终 Markdown 教材。

每个章节的处理步骤：
1. 写入 `##` 标题和时间范围
2. 将该时间段的 Whisper 文字稿发给 Claude API，整理成流畅的书面表达（去除口语化、填充词、重复）
3. 插入关键帧截图
4. 将识别到的代码格式化为带正确语言标签的代码块
5. 添加指向源视频对应时间点的时间戳链接

输出示例：

```markdown
## 第二章：定义神经网络层

> 00:12:30 – 00:18:45

我们来定义 `Layer` 类，它接收输入维度和输出维度作为参数。
初始化时，权重用随机数填充，偏置初始化为零。

![](frames/frame_750s.jpg)

`forward` 方法接收输入张量，返回线性变换的结果：

```python
class Layer:
    def __init__(self, nin, nout):
        self.weights = [[random() for _ in range(nin)] for _ in range(nout)]
        self.bias = [0.0] * nout
```
```

输出为 `output/` 目录下的单个 `tutorial.md` 文件，以及 `output/frames/` 目录下的关键帧图片。

### 5. 问答模块

允许用户在教程生成后提问，基于 HelloAgents 的记忆与 RAG 系统实现。

- 教程生成后，将内容向量化存入向量数据库（Chroma）
- 接收用户的自然语言问题后，先检索相关段落，再构建回答
- 不将整个教程塞进上下文，而是按需检索，节省 token
- 如果问题涉及某个具体步骤，Agent 会重新检查对应帧
- 返回基于实际视频内容的准确回答

上下文管理使用 HelloAgents 的 **ContextBuilder（GSSC Pipeline）**，处理长视频时自动压缩历史分析结果，避免上下文溢出。

---

## Agent 循环设计

FrameLearn 的核心循环基于 **ReAct 范式**（Hello-Agents 第 4 章），遵循 **Thought → Action → Observation** 的交替推理：

```
Thought:     分析当前状态，决定下一步
Action:      调用工具（yt-dlp / ffmpeg / OCR / Claude API）
Observation: 观察工具返回结果
Thought:     根据结果调整策略，或判断任务完成
```

此循环持续运行，直到 Agent 判断输出达到质量标准，或向用户返回带有清晰说明的失败信息。

HelloAgents `SimpleAgent` 提供了循环的基础骨架，FrameLearn 在此基础上注册自定义工具，扩展视频处理能力。

---

## 自我批评机制

文档生成器产出草稿后，会运行一轮独立的批评检查：

- 计划中的所有章节是否都已覆盖？
- 是否存在没有截图的章节？
- 是否有代码块不完整或被截断？

如果发现问题，Agent 会回头补全，再返回最终输出。

---

## 技术选型说明

**Whisper（OpenAI）**
语音转文字，本地运行，免费。输出带逐句时间戳的文字稿，与帧时间戳对齐是教材生成的核心步骤。中英文均支持。

**HelloAgents 框架（第 7 章）**
提供 Agent 基类、工具注册机制、消息传递协议。FrameLearn 的五个模块均继承自 `SimpleAgent`，避免从零搭建 Agent 骨架。

**ReAct 范式（第 4 章）**
规划 Agent 和内容分析器的核心决策循环。相比固定流水线，ReAct 允许 Agent 根据中间结果动态调整策略。

**记忆与 RAG（第 8 章）**
QA 模块的底层实现。使用 Chroma 向量数据库存储教程内容，问答时按需检索，不依赖全量上下文。

**ContextBuilder / GSSC Pipeline（第 9 章）**
处理长视频时管理上下文窗口，自动压缩历史分析结果，控制 token 消耗。

**Claude API（Anthropic）**
用于规划、内容分析、文档生成和问答。Claude 的视觉能力处理帧分析，工具调用能力驱动 ReAct 循环。

**yt-dlp**
通过统一接口支持 YouTube 和 Bilibili，自动处理鉴权、限速和格式选择。

**ffmpeg**
业界标准视频处理工具，用于帧提取、场景变化检测和缩略图生成。

**Tesseract / pytesseract**
从帧中提取文字，尤其适用于因字体渲染导致 Claude 视觉识别可能出错的代码内容。

**uv**
依赖管理工具，比 pip 更快，通过 `uv.lock` 实现确定性安装。

---

## 数据流图

```
┌─────────────┐
│   视频 URL  │
└──────┬──────┘
       │
       ▼
┌─────────────────┐     ┌──────────────┐
│   工具执行器    │────▶│  yt-dlp      │  下载视频
│  （预处理阶段） │────▶│  ffmpeg      │  提取音频 + 采样帧
│                 │────▶│  Whisper     │  语音转文字（带时间戳）
└──────┬──────────┘     └──────────────┘
       │ 采样帧 + 文字稿
       ▼
┌─────────────────┐     ┌──────────────┐
│   规划 Agent    │────▶│  Claude API  │  帧（base64）→ 章节分析
└──────┬──────────┘     └──────────────┘
       │ 转换计划
       ▼
┌─────────────────┐     ┌──────────────┐
│   工具执行器    │────▶│  ffmpeg      │  按计划提取关键帧
│  （全量阶段）   │────▶│  Tesseract   │  OCR 提取帧内文字
└──────┬──────────┘     └──────────────┘
       │ 关键帧 + OCR 文字
       ▼
┌──────────────────┐    ┌──────────────┐
│   内容分析器     │───▶│  Claude API  │  帧（base64）→ 关键帧筛选
│ （筛选+时间对齐）│    └──────────────┘
└──────┬───────────┘
       │ 关键帧 + 对应时间段文字稿
       ▼
┌──────────────────┐    ┌──────────────┐
│   文档生成器     │───▶│  Claude API  │  文字稿整理 → 书面表达
└──────┬───────────┘    └──────────────┘
       │
       ▼
┌──────────────────┐
│  tutorial.md     │  文字讲解 + 关键帧截图
└──────┬───────────┘
       │
       ▼
┌──────────────────┐    ┌──────────────┐
│    问答模块      │◀──▶│  Claude API  │
│  （用户提问）    │    └──────────────┘
└──────────────────┘
```

---

## 目录结构

```
framelearn/
├── README.md
├── README.zh.md
├── pyproject.toml
├── uv.lock
├── docs/
│   ├── architecture.md
│   ├── architecture.zh.md
│   └── hello-agents-summary.md
├── framelearn/
│   ├── __init__.py
│   ├── __main__.py
│   ├── planner.py          # 规划 Agent（继承 HelloAgents SimpleAgent）
│   ├── executor.py         # 工具执行器（HelloAgents 工具注册）
│   ├── analyzer.py         # 内容分析器（关键帧筛选 + 时间戳对齐）
│   ├── generator.py        # 文档生成器（文字稿整理 + 教材输出）
│   ├── qa.py               # 问答模块（HelloAgents RAG + 记忆）
│   └── tools/
│       ├── downloader.py   # yt-dlp 封装
│       ├── extractor.py    # ffmpeg 封装（帧 + 音频）
│       ├── transcriber.py  # Whisper 封装（语音转文字）
│       └── ocr.py          # Tesseract 封装
└── output/
    ├── tutorial.md         # 生成的教材
    └── frames/             # 关键帧截图
```

## 参考资料

- [Hello-Agents 课程](https://github.com/datawhalechina/Hello-Agents) — Agent 框架基础
- [Hello-Agents 内容总结](hello-agents-summary.md) — 各章节知识点与 FrameLearn 的对应关系
