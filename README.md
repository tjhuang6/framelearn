# FrameLearn

[English](README.en.md) | 中文

一个 AI Agent，将 Bilibili / YouTube 编程教学视频自动转换为图文教程——让你按自己的节奏学习，遇到不懂的地方随时向 AI 提问。

## 它能做什么

1. **下载**：通过 URL 下载 Bilibili 或 YouTube 视频
2. **分析**：自主分析视频结构（简介、环境配置、核心代码、测试、总结）
3. **提取**：在关键时刻截取帧（代码变化、报错、测试结果）
4. **生成**：输出带截图和逐步说明的 Markdown 教程
5. **问答**：生成教程后，可以针对视频内容进行交互式提问

## 使用示例

```bash
framelearn run "https://www.bilibili.com/video/BV1xx411c7mD"
```

输出：`output/tutorial.md` — 一份完整的图文教程，包含代码块、截图和章节标题。

## 架构

```
FrameLearn
├── 规划 Agent          # 分析视频结构，制定转换计划
├── 工具执行器          # 调用 yt-dlp、ffmpeg、OCR 等工具
├── 内容分析器          # 识别关键帧，提取代码，切分章节
├── 文档生成器          # 输出结构化 Markdown 教程
└── 问答模块            # 基于视频内容回答用户提问
```

## 技术栈

- **Claude API**（Anthropic）— Agent 调度、视频内容分析、问答
- **HelloAgents** — 轻量级 Agent 框架，提供 ReAct 循环、工具注册机制
- **yt-dlp** — 视频下载，支持 YouTube 和 Bilibili
- **ffmpeg** — 帧提取与视频处理
- **Whisper**（OpenAI）— 本地语音转文字，输出带时间戳的文字稿
- **Tesseract / pytesseract** — OCR，识别帧中的代码文字
- **Chroma** — 向量数据库，支持问答模块的 RAG 检索
- **Python 3.11+**

## 快速上手

```bash
# 1. 克隆仓库
git clone https://github.com/tjhuang6/framelearn.git
cd framelearn

# 2. 安装依赖
uv sync

# 3. 配置 API Key
export ANTHROPIC_API_KEY=your_key_here

# 4. 运行
python -m framelearn run "https://www.youtube.com/watch?v=example"
```

## 输出格式

生成的教程包含：

- 与视频结构对应的章节标题
- 关键时刻的截图
- 从视频中提取的代码块
- 每个片段的逐步说明
- 指向源视频对应时间点的时间戳链接

## 交互式问答

教程生成后，可以直接提问：

```bash
python -m framelearn ask "第 3 步为什么要用虚拟环境？"
```

Agent 会结合原始视频内容和生成的笔记给出准确的回答。

## 文档

- [技术架构](docs/architecture.md)
- [模块接口设计](docs/modules/)
- [Hello-Agents 学习笔记](docs/hello-agents/)
- [技术决策记录](docs/decisions/)

## 开源协议

MIT
