# FrameLearn AntiVibe 技术报告

> 分析范围：当前仓库的 `framelearn/`、`settings.toml`、`pyproject.toml` 与测试。
>
> 方法：AntiVibe compact / mid——重点解释代码做什么、为什么这样设计、适用条件、替代方案和工程风险。
> 结论基线：本报告描述扫描时的工作树实现，不把 `docs/modules/` 和 OpenSpec 中的目标设计当作已完成功能。

## 1. Overview：这套代码实际上是什么

FrameLearn 不是“自主规划视频内容的多 Agent 系统”，而是一条以本地文件为输入、由确定性 Python 代码编排、在少数步骤调用 LLM 的媒体处理流水线：

```text
命令解析
  → 本地视频校验
  → 音轨/字幕获取
  → ASR 与字幕清洗
  → 关键帧采样和去重
  → 可选 LLM 补帧
  → LLM 分段生成 Markdown
```

这个定位很重要。当前代码的强项不是 Agent 自主规划，而是把三个不稳定的外部系统——FFmpeg、ASR 服务和 LLM——包进可缓存、可降级的顺序流程中。控制流主要写在 `framelearn/pipeline/video_pipeline.py`，因此行为容易追踪；代价是这个协调器同时承担缓存、I/O、错误翻译、进度显示和业务编排，已经开始变得臃肿。

系统还有一条独立的 Codex app-server 路径，用于 `ask` 和可选的文档生成。它实现了 JSON-RPC 进程通信、thread/turn 生命周期、事件投影和 SQLite 持久化。这部分架构比视频流水线更接近“Agent runtime”，但目前与视频上下文并未形成教材 RAG。

## 2. 关键组件

| 组件 | 当前职责 | 设计意图 | 关键依据 |
|---|---|---|---|
| `CommandParser` | 传统命令透传；API 或规则式意图分类 | 避免每个输入都付出一次 LLM 分类成本 | `command_parser.py:77-160` |
| `CommandRouter` | 分发 `run/ask/summarize/help` | 把 CLI 入口与重型依赖延迟解耦 | `router.py:47-154` |
| `VideoPipeline` | 协调 ASR、清洗、抽帧、生成和缓存 | 让失败可以在阶段边界被翻译成用户错误 | `video_pipeline.py:47-255` |
| `ASRAdapter` | 在 DashScope 与 SiliconFlow 间路由 | 用统一 `TranscriptResult` 隔离 provider 差异 | `asr_adapter.py:15-74` |
| `DashscopeBackend` | 音频切片、OSS、异步任务、合并、SRT | 适配长音频和服务端文件转写限制 | `dashscope.py:109-231` |
| `FFmpegHelper` | 音轨检测/提取、场景帧、定时帧、精确补帧 | 把 shell 命令固定在一个边界内 | `ffmpeg_helper.py:8-235` |
| `KeyframeDeduplicator` | 全局 pHash 贪心去重 | 以低成本减少视觉请求中的重复图片 | `keyframe_dedup.py:19-67` |
| `SegmentSplitter` | SRT 时间分段或字数估时分段 | 控制长视频上下文和单次请求大小 | `segment_splitter.py:27-178` |
| `DocumentGenerator` | prompt、分段缓存、重试、后端选择 | 在完整性、成本和可恢复性间折中 | `doc_generator.py:154-438` |
| app-server 子系统 | JSON-RPC、turn、事件、持久化 | 将 Codex 作为本地 Agent runtime 嵌入 | `app_server/session.py:97-237`、`runtime.py:68-129` |

## 3. 核心概念与为什么这样做

### 3.1 Adapter：统一不一致的外部服务

What：`ASRAdapter` 把两个 provider 的结果统一为 `TranscriptResult`；`provider_adapter` 把 OpenAI-compatible、Gemini 和 Claude 请求收敛成 `call_llm()`。

Why：上层流水线只关心“文本、分段、是否有时间戳和 SRT”，不应知道 DashScope 的异步任务或 Claude 的消息 JSON 格式。这样切换 provider 时，改动集中在边界层。

When：当多个服务提供相似能力，但认证、请求和响应格式不同，Adapter 很合适。

Alternatives：

- 直接在流水线中 `if provider == ...`：短期更少文件，长期会让主流程被协议细节淹没。
- 定义严格的 `Protocol`/ABC 和插件注册表：扩展性更强，但对只有两个 ASR backend 的当前规模可能偏重。

Prerequisites：依赖倒置、数据类、HTTP API、错误边界。

### 3.2 分段生成：用空间分治换取上下文可控

What：输入较大时，按 SRT 时间或 4 字/秒估算切段，每段独立生成并写缓存，最后合并。

Why：长视频字幕和大量图片很容易超过模型上下文、请求体或服务超时。独立分段还能做到失败重试和断点续跑。

When：输入远大于单请求安全窗口，且各时间段可以近似独立处理时。

Alternatives：

- 一次性长上下文：实现最简单，连贯性最好，但失败成本高。
- 先做全局 outline，再按章节生成：结构更统一，但需要额外模型调用和跨段状态。
- Map-reduce：先逐段提取事实，再二次统一成书；更稳，但成本和延迟更高。

当前折中有两个明显后果：按字符切割可能截断语义；逐段结果只用 `---` 拼接，没有全局一致性修订。

### 3.3 时间戳作为跨模态主键

What：关键帧使用 `(Path, seconds)`，SRT 段使用 start/end；分段时按时间范围把图片分配给字幕。

Why：图片文件名或顺序号本身无法说明它对应哪段讲解。时间戳是视频、字幕和图片共有的最小坐标系。

When：多种媒体要在同一时间轴上对齐时。

Alternatives：帧序号、章节 ID、语义相似度匹配。帧序号依赖 FPS，章节 ID 需要预先规划，纯语义匹配成本高且不稳定。

当前实现将帧名降到整秒，这简化了人类阅读和缓存解析，但会丢失亚秒精度，也可能让同一秒的两帧发生文件名冲突。

### 3.4 pHash：用感知相似代替字节相等

What：图片转为 64 位感知哈希，以汉明距离估算视觉相似度。

Why：同一 PPT/代码画面即使经过 JPEG 压缩或有轻微变化，文件字节也不同；普通哈希无法去重。

When：需要快速过滤近似图片，而不是证明图片完全相同时。

Alternatives：SSIM、图像 embedding、OCR 文本 diff、只比较相邻帧。embedding 语义更强但贵；SSIM 更细致但计算更重；OCR 对代码变化敏感但引入额外依赖。

当前实现与所有已保留帧比较，可做全局去重，但最坏复杂度为 O(n²)。对默认最多约 200 个候选帧仍可接受。

### 3.5 existence-based cache：便宜的断点恢复

What：字幕、关键帧和分段 Markdown 只要存在，就被视为可复用结果。

Why：视频 ASR 和逐段 LLM 调用昂贵且耗时，最简单的恢复机制就是把阶段产物落盘。

When：个人工具、输入稳定、用户能理解并管理输出目录时。

Alternatives：内容寻址缓存、manifest、任务数据库。更健壮的做法是记录源视频哈希、配置哈希、prompt 版本、模型和完成状态。

当前方案的核心风险不是“缓存失效”，而是“缓存不会自动失效”：改模型、prompt、字幕或源视频后，旧文件仍可能静默复用。

### 3.6 app-server：宿主控制会话，Codex 控制 Agent turn

What：FrameLearn 启动 `codex app-server`，通过 stdio JSON-RPC 管理 thread 和 turn；完成事件被投影成消息并持久化。

Why：宿主不需要重写模型工具循环、沙箱和 Codex thread 语义，同时保留自己的 CLI、数据库、超时与恢复策略。

When：需要把现成 Agent runtime 嵌入应用，而不是只调用一次聊天 API 时。

Alternatives：直接 OpenAI-compatible HTTP、调用 `codex exec`、自己实现 tool loop。直接 HTTP 更简单但不含 Codex runtime；自建 tool loop 控制力最强但工程量最大。

当前边界：`run_turn()` 只接受文字，文档中列出的关键帧文件名不等于把图片像素送入模型。

## 4. 关键执行路径

### 4.1 本地视频到 Markdown

1. `router.py:78-109` 校验本地文件并启动流水线。
2. `video_pipeline.py:69-109` 选择字幕缓存、显式字幕或 ASR。
3. `video_pipeline.py:111-124` 清洗并写字幕。
4. `video_pipeline.py:126-169` 复用或提取关键帧并去重。
5. `video_pipeline.py:171-183` 可选执行 Agent 补帧。
6. `video_pipeline.py:185-230` 分别生成 notes 和主模式文档。
7. `doc_generator.py:184-246` 对长输入分段、重试、缓存和合并。

### 4.2 DashScope 长音频

1. `dashscope.py:139-142` 切片。
2. `dashscope.py:145-164` 恢复 checkpoint。
3. `dashscope.py:166-183` 并发上传/提交。
4. `dashscope.py:187-207` 轮询结果。
5. `dashscope.py:208-224` 合并并生成 SRT。
6. `dashscope.py:226-231` 清理 OSS 和可选本地临时文件。

### 4.3 ask 的 app-server turn

1. `router.py:111-140` 选择 app-server 并流式打印 delta。
2. `runtime.py:76-93` 先持久化用户消息，再执行 turn。
3. `session.py:124-150` 发送 `turn/start`。
4. `session.py:155-237` 消费事件、处理审批/超时/进程死亡。
5. `runtime.py:113-129` 更新 thread 并持久化投影消息。

## 5. 主要风险与边界

### P0：安装声明与实际 import 不一致

`pyproject.toml` 只声明 `httpx` 和 `python-dotenv`，但关键帧路径直接导入 Pillow/ImageHash，DashScope OSS 直接导入 `oss2`。当前开发环境里这些包可导入，不代表 `uv sync` 在新环境会安装它们。一个干净安装可能在运行时失败。

建议：把 `pillow`、`imagehash`、`oss2` 加入项目依赖；若希望 DashScope 可选，则用 extras/dependency group，并在选择 provider 时给出明确安装提示。

### P0：Agent 图像 API 路径不可执行

`agent_keyframe_selector.py:229-241` 导入 `ProviderAdapter`，但 `provider_adapter.py` 没有这个类。启用默认 `vision_mode="api"` 下的 Agent keyframe selection，会在图像评估时抛异常，再降级成文字评估，因此功能表面继续运行，但实际没有进行视觉判断。

建议：直接复用现有 `call_llm()`/`call_vision_llm()`，并增加验证“图片确实进入请求体”的测试。

### P1：app-server 文档生成不是多模态

`session.py:126-131` 的 input 只有 text。`DocumentGenerator` 虽列出关键帧文件名，但 app-server 模型不会自动读取图片。若 Codex 自主使用文件工具读取图片，还受工作目录、审批和模型行为影响，不能当作确定性输入。

建议：将 `run_turn()` 扩展为结构化 user input，明确发送 `localImage`；或者把文档生成统一限定为 Vision API，并把 app-server 只用于 `ask`。

### P1：缓存缺少可追溯性和失效策略

缓存判断散落在 `video_pipeline.py:85-97`、`:129-141` 和 `doc_generator.py:209-215`。它们没有 manifest。用户无法从产物判断使用了哪个源文件、模型、prompt 或配置。

建议：每个任务写 `manifest.json`，记录输入文件 hash/mtime/size、配置、provider/model、代码版本、段落完成状态。只有 cache key 匹配才复用。

### P1：关键帧文件名冲突

场景帧与固定间隔帧都按整秒命名。两个候选帧落在同一秒时，`Path.rename()` 可能覆盖、失败或使两个 tuple 指向同一文件，具体取决于平台语义。

建议：文件名保留毫秒或附加来源/序号，例如 `frame_00h01m30s250_scene_003.jpg`。

### P1：错误被“偏向继续”掩盖

- pHash 失败直接跳帧；
- Agent LLM 决策失败默认补帧；
- 图像评估失败最终默认保留；
- DashScope 某些分段失败时，只要还有结果就继续合并。

这些策略符合“不丢内容”的目标，但产物没有统一记录降级事件，用户难以知道结果是否完整。

建议：`PipelineResult` 增加 warnings；输出 `run-report.json`，列出失败分段、fallback、跳过帧和缓存命中。

### P1：CLI 失败可能仍返回成功退出码

`VideoPipeline` 的业务失败通常通过 `PipelineResult.error` 返回，Router 只打印错误；未实现的在线下载同样是打印提示后正常返回。因此 `_run_once()` 没有看到异常，最终返回 0。审计实测 `framelearn run https://youtube.com/watch?v=x` 明确打印“在线下载尚未实现”，shell 退出码仍为 0。

这会误导 shell 脚本、批处理和 CI：日志显示失败，但自动化系统将任务标为成功。

建议：让 Router handler 返回明确状态，或在不可完成的 `run` 路径抛出领域异常；`_run_once()` 应把失败统一映射为非零退出码，并增加 CLI exit-code 测试。

### P1：Codex 子进程继承了不需要的云凭据

`JsonRpcStdioClient._build_env()` 从完整 `os.environ` 复制环境，只移除 `TEXT_API_KEY`、`VISION_API_KEY`、`DATABASE_URL` 和 `WEBHOOK_SECRET`。因此 `DASHSCOPE_API_KEY`、`SILICONFLOW_API_KEY`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET` 以及其他云凭据仍会进入 `codex app-server` 子进程。

保留真实 `HOME` 对 Git/Codex 配置有价值，但不等于应继承全部秘密。只要 Agent 能执行命令，就可能读取这些环境变量，扩大凭据暴露面。

建议：改为 allowlist 构建子进程环境，至少只保留系统运行变量、`HOME`、`PATH`、Codex 必要配置和明确授权的字段；如必须使用 denylist，应补齐 FrameLearn 自身的 ASR/OSS 密钥并加入回归测试。

### P2：配置存在重复和未使用字段

- `runtime.asr_provider` 在旧文档中出现，但代码读 `asr.provider`；当前 `settings.toml` 同时还有 `runtime.asr_provider` 与 `[asr].provider`。
- `asr.overlap` 写在配置里，但当前 DashScope 切片没有读取。
- `style.tone/detail_level` 当前没有进入 DocumentGenerator prompt。
- `appserver.command/workspace/approval_policy` 没有完整贯穿所有直接 `AppServerSession` 构造。

建议：为每个配置字段建立读取测试，移除无消费者字段，或明确标记 reserved。

### P2：安装形态可能改变配置来源

默认配置路径由 `Path(__file__).parent.parent / "settings.toml"` 推导。源码可编辑安装时它通常指向仓库根目录；普通 wheel 安装后则可能指向 `site-packages` 上层，而项目没有明确把根目录 `settings.toml` 作为包资源安装。找不到文件时，代码会静默使用与仓库配置不完全一致的内置默认值。

建议：定义稳定的配置优先级，例如显式 `--config` / 环境变量路径 → 当前工作目录 → 用户配置目录 → 包内默认资源；启动时打印实际配置来源，并为 editable install 与 wheel install 分别增加测试。

### P2：会话和远程媒体的数据边界缺少说明

DashScope 路径会把音频切片上传到 OSS，再通过签名 URL交给 ASR；Vision API 会发送字幕和关键帧；Codex 对话、工具调用和 reasoning 会明文保存到 `~/.framelearn/sessions.db`。当前用户文档没有集中说明这些数据去向、保留周期、清理方式或关闭持久化的方法。

建议：补充隐私与数据生命周期文档；提供会话数据库清理/禁用选项；输出每次任务实际使用的外部服务和远程数据类型。

### P2：分段和双文档生成的成本模型不透明

流水线固定先生成 notes，再生成主模式。如果主模式也是 notes，会做两次相同生成。无 SRT 时按 4 字/秒硬切，可能切断代码或句子。

建议：主模式为 notes 时复用结果；字数 fallback 至少寻找最近的句末边界；长视频可先生成章节 outline 再分段。

### P2：测试与真实集成之间存在空白

单元测试覆盖不少控制逻辑，但缺少：

- 干净环境安装测试；
- 最小真实视频端到端测试；
- DashScope/OSS contract test；
- Vision 请求体测试；
- 缓存失效测试；
- 同秒关键帧冲突测试。

这意味着“pytest 通过”主要证明局部 Python 行为，不证明生产流水线可运行。

## 6. 架构评价

### 做得好的地方

1. ASR 和 LLM provider 的协议细节大体被隔离在边界层。
2. 时间戳从 ASR 到关键帧再到分段的传递方向正确。
3. 长任务具有落盘缓存、checkpoint 和重试意识。
4. app-server 按 client/session/projector/runtime/persistence 分层，职责比单体封装清晰。
5. 失败策略普遍优先保留原始内容，符合教学资料“宁可粗糙，不要静默丢失”的目标。

### 当前架构债务

1. `VideoPipeline.run()` 超过 190 行核心流程，阶段边界只靠注释表达。
2. 配置、环境变量和构造参数的优先级不统一。
3. “Agent”“Vision”“quality review”等命名比实际实现更强，容易造成能力误判。
4. 历史目标文档数量多于当前实现文档，代码认知成本高。
5. 缓存和降级缺少可观察性。

## 7. 推荐演进顺序

### 第一阶段：先让当前链路可重复安装、可验证

1. 补齐依赖声明。
2. 修复 Agent Vision API 的不存在类引用。
3. 增加最小 5–10 秒视频 fixture 的离线端到端测试，mock ASR/LLM，但真实运行 FFmpeg。
4. 为输出写 manifest 和 warnings。

### 第二阶段：消除“看起来支持、实际降级”的能力

1. app-server 要么实现 `localImage`，要么在配置校验时禁止用于需要视觉的文档生成。
2. 删除重复/未使用配置，统一 `asr.provider`。
3. 修复关键帧毫秒命名与缓存解析。
4. 主模式为 notes 时避免重复调用。

### 第三阶段：提高长视频质量

1. 字符 fallback 改为句子边界切分。
2. 增加全局 outline 或合并后统一编辑 pass。
3. checkpoint 记录部分 ASR 失败，禁止将不完整转录伪装成完整成功。
4. 对分段缓存加入 prompt/model/config hash。

### 第四阶段：再决定是否引入真正 Agent/RAG

只有当用户确实需要“按教材内容回答并附来源”时，再实现教程索引和检索；只有当固定流水线无法决定章节/截图密度时，再增加 Planner Agent。当前阶段直接实现早期文档中的完整多 Agent 架构，会显著增加复杂度，却不一定改善视频转教材的核心可靠性。

## 8. 最终判断

FrameLearn 已经有一条可以继续工程化的真实主干：长音频时间戳 ASR、关键帧时间轴、分段生成和磁盘恢复。这些部分比早期“Planner/Executor/Analyzer/QA”蓝图更接近实际产品价值。

下一步最值得投入的不是增加更多 Agent 名词，而是缩小“配置声称的能力”与“实际数据是否进入模型”之间的差距，并让每次运行可复现、可诊断、可确认完整性。完成依赖、Vision 输入、manifest 和端到端测试之后，再扩展在线下载或 RAG，风险会低得多。
