# 当前技术架构

本文只描述仓库当前可执行的代码。早期规划文档和设计提案的状态见 [文档索引](README.md)。

## 1. 系统边界

FrameLearn 当前是本地 CLI 应用，而不是在线视频下载器或完整 RAG 学习平台。

已实现：

- 本地视频处理；
- 可选已有字幕输入；
- DashScope/SiliconFlow ASR；
- 字幕清洗；
- FFmpeg 抽帧和 pHash 去重；
- 可选 LLM 辅助补帧；
- 单次或分段 Markdown 生成；
- Codex app-server/API 通用问答。

未实现或不是当前行为：

- YouTube/Bilibili 下载；
- Planner Agent、OCR Content Analyzer、Chroma RAG；
- FrameLearn 内部的学习总结生成；
- app-server 图片结构化输入。

## 2. 入口与命令路由

```text
framelearn 命令 / 无参数 REPL
          │
          ▼
framelearn.__main__
          │  解析 --subtitle
          ▼
CommandParser
          │  输出 run / ask / summarize / help
          ▼
CommandRouter
          ├── run       → VideoPipeline
          ├── ask       → RuntimeAdapter 或 provider_adapter
          ├── summarize → 打印外部 skill 提示
          └── help      → 打印帮助
```

### `framelearn/__main__.py`

- 无参数启动 REPL。
- 单次调用支持 `--subtitle <path>`。
- CLI 当前只识别这一个 flag；未知参数会并入用户输入。

### `framelearn/command_parser.py`

- `run`、`ask`、`summarize`、`help` 开头的传统命令直接透传。
- 若配置了有效 `TEXT_PROVIDER` 和 `TEXT_API_KEY`，通过 `provider_adapter.call_text_llm()` 分类。
- 否则使用本地规则：包含 URL/视频路径时生成 `run`；总结关键词生成 `summarize`；其余请求生成 `ask`。
- 本地路径提取只识别以 `/` 或 `~` 开头、且无空格的路径片段。

### `framelearn/router.py`

- 在线链接只进行域名校验，随后明确提示下载未实现。
- 本地视频先检查存在性和扩展名，再实例化 `VideoPipeline`。
- `ask` 根据 `runtime.text_mode` 选择 Codex app-server 或文字 API。
- `summarize` 尚未集成实现。

## 3. 视频处理数据流

```text
本地视频 + 可选字幕
        │
        ▼
VideoPipeline
  0. 检查 FFmpeg，创建 output/<video-stem>/src
  1. 使用 --subtitle / 字幕缓存，或提取音频并调用 ASR
  2. SubtitleCleaner 清洗并写 subtitle.txt；有 SRT 时写 subtitle.srt
  3. 使用帧缓存，或同时执行场景检测和固定间隔抽帧
  4. KeyframeDeduplicator 全局 pHash 去重
  5. 可选 AgentKeyframeSelector 补帧与评估
  6. 生成 notes.md
  7. 按 doc_generation.mode 生成 index.md
```

核心协调器是 `framelearn/pipeline/video_pipeline.py`。返回值 `PipelineResult` 包含输出目录、主 Markdown 路径、最终关键帧路径、清洗字幕和错误信息。

## 4. ASR 子系统

### `ASRAdapter`

`framelearn/pipeline/asr_adapter.py` 读取 `asr.provider`，而不是 `runtime.asr_provider`。支持：

| Provider | 实现 | 时间戳 | 适用场景 |
|---|---|---:|---|
| `dashscope` | `asr_backends/dashscope.py` | 是 | 长音频、需要 SRT |
| `siliconflow` | `asr_backends/siliconflow.py` | 否 | 快速、简单转写 |

### DashScope 路径

```text
audio.m4a
  → 按 asr.chunk_duration 切片
  → 并发上传到阿里云 OSS
  → 提交异步转写任务
  → 轮询并下载 JSON 结果
  → 加回各切片的起始偏移
  → TranscriptSegment + subtitle.srt
  → 尽力删除 OSS 临时对象
```

断点文件位于 `output/<video>/temp/asr_checkpoint.json`。任务全部成功后会删除 checkpoint。若 `asr.keep_temp_files = true`，本地音频切片会保留。

需要的密钥是 `DASHSCOPE_API_KEY`、`OSS_ACCESS_KEY_ID` 和 `OSS_ACCESS_KEY_SECRET`；Bucket 与 region 读取 `[asr.oss]`。

### SiliconFlow 路径

直接上传完整音频到 `/audio/transcriptions`，返回纯文本。它会针对限流和一般失败重试，但没有 SRT，因此后续时间对齐只能使用字数估算。

## 5. 关键帧子系统

### `FFmpegHelper`

- `extract_audio()` 输出 16 kHz AAC/m4a；代码没有显式设置单声道。
- `extract_keyframes()` 总是执行两套采样：场景检测和固定间隔，不是“仅在场景检测失败时 fallback”。
- 两类帧按整秒时间戳重命名为 `frame_HHhMMmSSs.jpg` 后合并。相同整秒名称可能发生覆盖或重命名冲突，这是已知边界。
- `capture_single_frame()` 用于 Agent 精确补帧。

### `KeyframeDeduplicator`

- 对每帧计算 64 位 pHash。
- 与所有已保留帧比较；归一化相似度大于 `0.9` 时丢弃。
- 这是全局贪心去重，复杂度近似 O(n²)，并保留时间序列中最先遇到的代表帧。

### `AgentKeyframeSelector`

默认关闭。启用后不会替代基础抽帧，而是在现有关键帧集合上扫描带时间戳字幕段、按关键词触发 LLM 决策并补帧。失败时采用偏向保留内容的降级策略。

当前 API 图像评估分支引用不存在的 `ProviderAdapter`，app-server 分支也只发送文字，不发送真实图片；因此该功能仍是实验状态。

## 6. 文档生成

`framelearn/pipeline/doc_generator.py` 支持：

- `visual_script`：按讲解顺序整理；
- `notes`：技术博客式连续叙述；
- `textbook`：正式教材式叙述。

需要注意，“notes”当前 prompt 明确禁止 bullet point，因此它并不是 README 早期版本所说的提纲式课堂笔记。

### 分段触发

只有满足以下任一条件才启用分段：

- 清洗后字幕长度大于 8000 字符；
- 关键帧数量大于 20。

短输入忽略 `segment_duration`，直接单次调用。长输入通过 `SegmentSplitter`：

1. 有 SRT 时按时间戳聚合到约 `segment_duration` 秒；
2. 没有 SRT 时按固定 4 字/秒估算；
3. 每段最多分配 `max_keyframes_per_segment` 张帧；
4. 每段最多进行 3 次网络/生成重试，等待 15、30、45 秒；
5. 结果缓存到 `segments_<mode>/seg_NNN.md`；
6. 用分隔线合并并加一级标题。

`quality_review = true` 时，生成结果经过本地启发式检查；不合格最多重生成 3 次，最终降级为原字幕。当前实现没有独立的 LLM reviewer。

### 两种生成后端

| 模式 | 当前行为 |
|---|---|
| `vision_mode = "api"` | 通过 `provider_adapter.call_llm()` 发送 prompt 和本地图片的 base64 数据 |
| `vision_mode = "appserver"` | 启动独立 `AppServerSession`，但 `turn/start` 只包含 text；关键帧仅以文件名出现在 prompt 中 |

app-server 若产生 `fileChange` 事件，生成器会优先读取其中的 `.md`；否则使用最终文字消息。

## 7. App-server 子系统

```text
RuntimeAdapter
  → AppServerSession
      → JsonRpcStdioClient
          → codex app-server 子进程
      → EventProjector
  → SessionDB
```

- `JsonRpcStdioClient` 负责 newline-delimited JSON-RPC 和进程生命周期。
- `AppServerSession` 负责 initialize、thread/start、turn/start、审批、超时和中断。
- `EventProjector` 将完成事件映射成可持久化消息。
- `RuntimeAdapter` 在 turn 前保存用户消息，turn 后保存投影消息，并在 session 退休后重建一次。
- 默认审批回调是 fail-closed：未提供回调时拒绝请求。

## 8. 配置的实际来源

| 配置域 | 来源 |
|---|---|
| 运行、视频、ASR、文档、Agent 参数 | `settings.toml`，通过 `framelearn.config` 读取并缓存 |
| LLM/ASR/OSS 密钥 | `.env` / 进程环境变量 |
| 文本 API provider/model/base URL | `TEXT_*` 环境变量 |
| 文档 Vision provider/model | `settings.toml` 的 `runtime.vision_*`；密钥/base URL 仍来自环境 |
| app-server CLI 配置 | 本机 Codex 配置与认证 |

`config.load_config()` 不会把 `.env` 字段合并到返回字典；它只是先调用 `load_dotenv()`，业务代码再用 `os.getenv()` 读取密钥。

## 9. 输出与缓存

```text
output/<video-stem>/
├── index.md
├── notes.md
├── src/
│   ├── subtitle.txt
│   ├── subtitle.srt
│   └── frame_HHhMMmSSs.jpg
├── segments_notes/
├── segments_visual_script/ 或 segments_textbook/
└── temp/
```

缓存键主要是文件是否存在，不包含源视频哈希、配置哈希或 prompt 版本。因此改变模型、prompt、分段长度或源文件后，旧缓存仍可能被复用；需要人工决定是否清理。

## 10. 测试范围

测试集中在：

- CommandParser/CommandRouter 分发与校验；
- SubtitleCleaner；
- FFmpeg helper 的部分命令构造；
- SiliconFlow ASR mock；
- pHash 去重接口；
- SegmentSplitter；
- 文档 prompt 与质量重试；
- AgentKeyframeSelector；
- app-server JSON-RPC、投影、持久化和 runtime。

目前没有对真实 FFmpeg 视频、DashScope/OSS、Vision API 或完整端到端流水线进行在线集成测试。
