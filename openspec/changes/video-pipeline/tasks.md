# 任务列表：视频处理流水线

## 前置条件

- [x] CommandRouter 已实现（task #10）
- [x] config.py 已实现（配置加载）
- [x] settings.toml 已创建
- [ ] 用户已安装 FFmpeg
- [ ] 用户已配置 SILICONFLOW_API_KEY

---

## 任务拆解

### 阶段 1：基础设施（2-3 小时）

#### Task 1.1：创建 pipeline 模块目录

**文件**：
- `framelearn/pipeline/__init__.py`
- `framelearn/pipeline/video_pipeline.py`（空骨架）

**验收**：
```python
from framelearn.pipeline import VideoPipeline
```

- [x] 已完成

---

#### Task 1.2：实现 FFmpegHelper

**文件**：`framelearn/pipeline/ffmpeg_helper.py`

**子任务**：
- [x] `check_installed()` — 检查 ffmpeg 是否在 PATH
- [x] `extract_audio()` — 提取音轨到 m4a
- [x] `extract_keyframes()` — 场景检测 + 定时保底抽帧（返回带时间戳元组）
- [x] 编写单元测试（mock subprocess）

**验收**：
```python
helper = FFmpegHelper()
assert helper.check_installed()
helper.extract_audio("test.mp4", "out.m4a")
frames = helper.extract_keyframes("test.mp4", "output/")
assert len(frames) > 0
```

**依赖**：无

---

#### Task 1.3：实现 ASRAdapter（硅基流动）

**文件**：`framelearn/pipeline/asr_adapter.py`

**子任务**：
- [x] 定义 `TranscriptResult` 和 `TranscriptSegment` dataclass
- [x] 实现 `transcribe()` — 调用百炼 Qwen ASR API
- [x] 处理 API 错误（401、429、超时）
- [x] 重试逻辑（最多 3 次，间隔 5 秒）
- [x] 编写单元测试（mock httpx）

**验收**：
```python
adapter = ASRAdapter(provider="siliconflow")
result = adapter.transcribe("test.m4a")
assert result.full_text != ""
assert result.has_timestamps is False  # 硅基流动无时间戳
```

**API 文档**：https://docs.siliconflow.cn/api-reference/audio-transcription

**依赖**：无

---

### 阶段 2：图片处理（1-2 小时）

#### Task 2.1：实现 KeyframeDeduplicator

**文件**：`framelearn/pipeline/keyframe_dedup.py`

**子任务**：
- [x] 使用 `imagehash.phash()` 计算感知哈希
- [x] 实现相似度计算（汉明距离）
- [x] 去重逻辑：保留第一帧，跳过相似度 > 90% 的后续帧
- [x] 限制最终数量（max_frames）
- [x] 编写单元测试（准备测试图片集）

**验收**：
```python
dedup = KeyframeDeduplicator(similarity_threshold=0.9)
unique = dedup.deduplicate(all_frames, max_frames=100)
assert len(unique) <= 100
```

**依赖**：
```bash
uv add imagehash pillow
```

---

### 阶段 3：字幕清洗（1 小时）

#### Task 3.1：实现 SubtitleCleaner

**文件**：`framelearn/pipeline/subtitle_cleaner.py`

**子任务**：
- [x] 移植 Bilitato 的清洗规则（去括号、全角转半角、去重复）
- [x] 断句优化（句号后换行）
- [x] `strip_timestamps()` — 去除 SRT/VTT 时间戳，返回纯文本
- [x] 编写单元测试（测试用例覆盖常见情况）

**验收**：
```python
cleaner = SubtitleCleaner()
raw = "大家好[音乐]，，今天讲 Python。。今天讲 Python"
cleaned = cleaner.clean(raw)
assert "[音乐]" not in cleaned
assert "今天讲 Python" in cleaned
assert cleaned.count("今天讲 Python") == 1  # 去重
```

**参考**：`/Users/iwill/Documents/learn_bilitato/Bilitato/utils/subtitleProcessor.js`

**依赖**：无

---

### 阶段 4：文档生成（2-3 小时）

#### Task 4.1：实现 DocumentGenerator（app-server 模式）

**文件**：`framelearn/pipeline/doc_generator.py`

**子任务**：
- [x] 读取 `settings.toml` 的 `vision_mode` 和 `text_mode`
- [x] `vision_mode=appserver` 时，调用 `AppServerSession`
- [x] 构造 prompt：字幕 + 关键帧（带时间戳）
- [x] 支持三种模式：visual_script / notes / textbook
- [x] 分段生成（> 8000 字或 > 20 帧自动切分）
- [x] 编写单元测试（mock app-server）

---

#### Task 4.2：实现 DocumentGenerator（API 模式）

**子任务**：
- [x] `vision_mode=api` 时，调用 `provider_adapter`
- [x] 支持多模态输入（text + image）
- [x] Base64 编码图片
- [x] 编写单元测试（mock httpx）

---

#### Task 4.3：实现 SegmentSplitter

**文件**：`framelearn/pipeline/segment_splitter.py`

**子任务**：
- [x] 定义 `Segment` dataclass
- [x] SRT 精确切分（解析时间戳）
- [x] 字数估算 fallback
- [x] 为每段分配对应时间范围内的关键帧
- [x] 编写单元测试

---

### 阶段 5：主流程集成（2-3 小时）

#### Task 5.1：实现 VideoPipeline 主流程

**文件**：`framelearn/pipeline/video_pipeline.py`

**子任务**：
- [x] 定义 `PipelineResult` dataclass
- [x] `__init__()` — 验证输入、创建输出目录，支持 `subtitle_path` 跳过 ASR
- [x] `run()` — 串联所有模块（分段生成，带时间戳关键帧）
- [x] 临时文件管理（keep_temp_files 控制）
- [x] 错误处理（try/finally 保证清理）
- [x] 进度输出（每个阶段打印状态）
- [ ] 编写集成测试（端到端）

**流程**：
```python
1. FFmpegHelper.check_installed()
2. output_dir = Path(config.get("video.output_dir")) / video_title
3. audio_path = FFmpegHelper.extract_audio(video, temp/audio.m4a)
4. transcript = ASRAdapter.transcribe(audio_path)
5. cleaned = SubtitleCleaner.clean(transcript.full_text)
6. raw_frames = FFmpegHelper.extract_keyframes(video, temp/frames/)
7. unique_frames = KeyframeDeduplicator.deduplicate(raw_frames)
8. 复制 unique_frames 到 output_dir/src/
9. markdown = DocumentGenerator.generate(unique_frames, cleaned, title)
10. 写入 output_dir/index.md
11. 写入 output_dir/src/subtitle.txt
12. 清理临时文件（如果 keep_temp_files=false）
13. 返回 PipelineResult
```

**验收**：
```python
pipeline = VideoPipeline("test.mp4")
result = pipeline.run()
assert result.markdown_path.exists()
assert result.output_dir.exists()
assert len(result.keyframes) > 0
```

**依赖**：Task 1.2, 1.3, 2.1, 3.1, 4.1

---

#### Task 5.2：接入 CommandRouter

**文件**：`framelearn/router.py`

**修改**：
```python
def _run_pipeline(self, source: str):
    # 替换 TODO
    from framelearn.pipeline import VideoPipeline
    
    print(f"📹 正在处理视频：{source}")
    pipeline = VideoPipeline(source)
    result = pipeline.run()
    
    if result.error:
        print(f"❌ {result.error}")
    else:
        print(f"✅ 教材已生成：{result.markdown_path}")
```

**验收**：
```bash
framelearn run test.mp4
# 输出：✅ 教材已生成：output/test/index.md
```

- [x] 已完成

**依赖**：Task 5.1

---

### 阶段 6：配置和依赖（30 分钟）

#### Task 6.1：更新配置文件

**文件**：
- `settings.toml` — 添加 `[asr]` 和 `[video]` 配置
- `.env.example` — 添加 `SILICONFLOW_API_KEY`

- [x] 已完成

**依赖**：无

---

#### Task 6.2：更新 pyproject.toml

**添加依赖**：
```toml
[project.dependencies]
imagehash = ">=4.3.1"
pillow = ">=10.0.0"
```

**运行**：
```bash
uv sync
```

- [x] 已完成

**依赖**：无

---

### 阶段 7：测试和文档（2-3 小时）

#### Task 7.1：编写单元测试

**文件**：`test/src/test_pipeline.py`

**覆盖模块**：
- `FFmpegHelper`（mock subprocess）
- `ASRAdapter`（mock httpx）
- `KeyframeDeduplicator`
- `SubtitleCleaner`
- `DocumentGenerator`（mock app-server）

**验收**：
```bash
pytest test/src/test_pipeline.py -v
# 全部通过
```

- [x] 已完成

**依赖**：所有实现任务

---

#### Task 7.2：手动集成测试

**测试场景**：
1. 正常流程：本地短视频（< 5 分钟）
2. 边界情况：无音轨视频
3. 错误处理：FFmpeg 未安装
4. 错误处理：ASR API key 错误
5. 配置切换：`vision_mode = api`

**测试视频**：`/Users/iwill/Documents/李哥考研/第四节分类任务(1).mp4`

**验收**：所有场景输出符合预期

**依赖**：Task 5.2

---

#### Task 7.3：更新 README

**添加章节**：
- 依赖安装（FFmpeg）
- 配置 ASR API key
- 使用示例
- 输出目录结构
- 常见问题

**依赖**：Task 7.2

---

## 任务依赖图

```
1.1 (创建目录)
 │
 ├─→ 1.2 (FFmpeg) ──┐
 │                  │
 ├─→ 1.3 (ASR) ─────┤
 │                  │
 ├─→ 2.1 (去重) ────┤
 │                  ├─→ 5.1 (主流程) ─→ 5.2 (接入 Router)
 ├─→ 3.1 (清洗) ────┤
 │                  │
 └─→ 4.1 (生成) ────┘
      ↓
     4.2 (API 模式)

6.1 (配置) ──┐
6.2 (依赖) ──┤
             ├─→ 7.1 (单元测试)
5.2 (Router)─┘       ↓
                  7.2 (集成测试)
                     ↓
                  7.3 (文档)
```

---

## 完成标准

- [ ] 所有单元测试通过（pytest -v）
- [ ] 手动测试通过（真实视频生成教材）
- [ ] FFmpeg 未安装时有清晰提示
- [ ] ASR API 错误有友好提示
- [ ] 输出目录结构符合设计（时间戳文件名）
- [ ] README 包含完整使用说明
- [ ] 无临时文件残留（keep_temp_files=false 时）
- [ ] 3 小时视频全量字幕和关键帧都被使用（不截断）
- [ ] visual_script 模式生成的讲稿保持时间顺序

---

## 阶段 8：Agent 化（关键帧选择、质量评审、自动重试）

### Task 8.1：FFmpegHelper 加单帧截取

**文件**：`framelearn/pipeline/ffmpeg_helper.py`

**子任务**：
- [x] 实现 `capture_single_frame(video_path, timestamp, output_path)` — 精确截取某秒的帧
- [x] 编写单元测试（mock subprocess）

**接口**：
```python
@staticmethod
def capture_single_frame(
    video_path: str,
    timestamp: float,   # 秒数
    output_path: str,
) -> bool:
    """ffmpeg -ss <timestamp> -i video -vframes 1 output.jpg"""
```

**依赖**：Task 1.2

---

### Task 8.2：实现 AgentKeyframeSelector

**文件**：`framelearn/pipeline/agent_keyframe_selector.py`

**子任务**：
- [x] 启发式预过滤（关键词匹配，免费）
- [x] LLM 决策：是否需要截图（JSON 返回）
- [x] 调用 `FFmpegHelper.capture_single_frame()` 截帧
- [x] LLM 评估图片价值（PPT/代码 → 保留；人脸/空白 → 丢弃）
- [x] 结果去重（±2 秒内不重复）
- [x] 编写单元测试（mock LLM 决策）

**接口**：
```python
class AgentKeyframeSelector:
    def select(
        self,
        video_path: str,
        subtitle_with_timestamps: list[TranscriptSegment],
        existing_keyframes: list[tuple[Path, float]],
    ) -> list[tuple[Path, float]]:
        """
        Agent loop:
        1. LLM reads subtitle segment → decide if frame needed
        2. If yes: capture_single_frame() at segment.start
        3. LLM evaluates image → keep or discard
        4. Repeat for all segments
        """
```

**LLM 决策逻辑**：
```
输入：字幕段落文字
问 LLM：
  "这段字幕需要截图吗？判断依据：
   - 提到'看图'、'如图'、'代码'、'屏幕' → 需要
   - 只是口头讲解，无参考内容 → 不需要
   返回 JSON: {need_frame: bool, reason: str}"

如果 need_frame = true：
  截图 → 问 LLM：
  "这张图有教学价值吗？
   - PPT/代码/终端 → 保留
   - 讲师人脸/空白屏 → 丢弃
   返回 JSON: {keep: bool, reason: str}"
```

**依赖**：Task 8.1、ASRAdapter（需要时间戳）

---

### Task 8.3：DocumentGenerator 加质量评审循环

**文件**：`framelearn/pipeline/doc_generator.py`

**子任务**：
- [x] 实现 `_review_segment(draft, segment)` — 启发式评审（长度、口水词、缺图）
- [x] 实现重试逻辑：质量差 → 加强 prompt 重试，最多 3 次
- [x] 第 3 次失败降级保存原始字幕（不丢内容）
- [x] `_generate_with_review()` 集成评审循环
- [x] `model_override` 支持升级模型

---

### Task 8.4：VideoPipeline 集成 Agent 模式

**文件**：`framelearn/pipeline/video_pipeline.py`

**子任务**：
- [x] 配置项 `agent.keyframe_selection = true/false`（默认 false）
- [x] 配置项 `agent.quality_review = true/false`（默认 false）
- [x] 配置项 `agent.upgrade_model`（质量差时升级到此模型）
- [x] 在关键帧去重后：若 `agent.keyframe_selection = true`，调用 `AgentKeyframeSelector`
- [x] 在文档生成时：若 `agent.quality_review = true`，走评审循环

---

### Task 8.5：Agent 化单元测试

**文件**：`test/src/test_agent_keyframe.py`

**子任务**：
- [x] mock LLM 决策，验证 `AgentKeyframeSelector` 逻辑
- [x] 验证质量评审重试次数上限
- [x] 验证降级策略（第 3 次失败保存原始字幕）

**依赖**：Task 8.2、Task 8.3

---

## 预计工作量

| 阶段 | 任务 | 预计时间 |
|------|------|----------|
| 1 | 基础设施 | 2-3 小时 |
| 2 | 图片处理 | 1-2 小时 |
| 3 | 字幕清洗 | 1 小时 |
| 4 | 文档生成 | 2-3 小时 |
| 5 | 主流程 | 2-3 小时 |
| 6 | 配置 | 0.5 小时 |
| 7 | 测试文档 | 2-3 小时 |
| **8** | **Agent 化** | **4-6 小时** |
| **总计** | | **15-22 小时（3-4 个工作日）** |

---

## 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| FFmpeg 命令平台差异 | 中 | 中 | 在 macOS/Linux 上测试；Windows 用户提供详细文档 |
| 百炼 API 限流 | 低 | 高 | 重试逻辑 + 友好提示 |
| 长视频内存溢出 | 低 | 高 | 流式处理；大文件分批上传 |
| Agent 关键帧选择成本过高 | 中 | 中 | 默认关闭；配置项控制 |
| LLM 评审误判（好内容被重试） | 中 | 低 | 最多重试 2 次；第 3 次降级保存 |
| 关键帧全黑屏 | 低 | 低 | Agent 评审自动跳过；保留至少 1 帧 |
| 5 | 主流程 | 2-3 小时 |
| 6 | 配置 | 0.5 小时 |
| 7 | 测试文档 | 2-3 小时 |
| **总计** | | **11-16 小时（2-3 个工作日）** |

---

## 风险与对策

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| FFmpeg 命令平台差异 | 中 | 中 | 在 macOS/Linux 上测试；Windows 用户提供详细文档 |
| 硅基流动 API 限流 | 低 | 高 | 重试逻辑 + 友好提示 |
| 长视频内存溢出 | 低 | 高 | 流式处理；大文件分批上传 |
| Codex context 超限 | 中 | 中 | 分批发送关键帧（≤ 20 帧/批） |
| 关键帧全黑屏 | 低 | 低 | 保留至少 1 帧 |
