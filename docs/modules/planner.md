# 规划 Agent 接口设计

## 职责

分析视频采样帧，识别视频结构（章节划分），输出转换计划供后续模块使用。

这是整个流水线的起点：没有计划，后续模块就是在盲目处理每一帧。

---

## 前提条件

规划 Agent 启动时，以下工作已由工具执行器完成：
- 视频已下载到本地（`output/video.mp4`）
- 采样帧已提取（每 60 秒一帧，存于 `output/frames/sample/`）
- 音频转写已完成，带时间戳的文字稿已就绪

规划 Agent 的输入是**本地帧路径列表**，不是 URL。

---

## 数据结构

### 输入

```python
@dataclass
class PlannerInput:
    sample_frame_paths: list[str]   # 采样帧的本地路径，按时间排序
    transcript: list[TranscriptRow] # Whisper 输出的带时间戳文字稿
    video_title: str                # 视频标题（用于 Prompt 上下文）
    total_duration_sec: float       # 视频总时长（秒）
```

```python
@dataclass
class TranscriptRow:
    start: float    # 句子开始时间（秒）
    end: float      # 句子结束时间（秒）
    text: str       # 句子文本（已经过清洗）
```

### 输出

```python
@dataclass
class ConversionPlan:
    chapters: list[Chapter]
    created_at: datetime

@dataclass
class Chapter:
    index: int              # 章节序号（从 0 开始）
    title: str              # 章节标题
    start_sec: float        # 预估开始时间（秒）
    end_sec: float          # 预估结束时间（秒）
    focus: str              # 该章节的关注重点（给内容分析器的提示）
    frame_density: str      # "high" | "normal"：关键帧提取密度建议
```

---

## 接口

```python
class PlannerAgent(SimpleAgent):
    def __init__(self, llm: HelloAgentsLLM): ...

    def run(self, input: PlannerInput) -> ConversionPlan:
        """
        分析采样帧，识别视频章节结构，返回转换计划。
        内部使用 ReAct 循环，可能多次调用 send_frames_to_claude 工具。
        """

    def _build_prompt(self, frames: list[str], transcript_text: str) -> str:
        """构建发送给 LLM Provider 的分析 Prompt，附加严格的输出格式约束。"""

    def _parse_plan(self, llm_output: str) -> list[Chapter]:
        """
        解析 LLM 输出的章节 JSON，使用别名匹配 + 模糊正则容错。
        时间字段同时支持秒数整数和 "mm:ss" 格式。
        """
```

---

## ReAct 循环示例

```
Thought: 需要了解视频整体结构，先分析开头和结尾的帧
Action: send_frames_to_llm(frames=["frame_0s.jpg", "frame_60s.jpg", "frame_3600s.jpg"])
Observation: LLM 返回：视频共约 60 分钟，识别到 5 个主题段落……

Thought: 需要确认中间段落的边界，再分析 20-40 分钟的帧
Action: send_frames_to_llm(frames=["frame_1200s.jpg", "frame_1800s.jpg", "frame_2400s.jpg"])
Observation: LLM 返回：20 分钟处是环境配置，30 分钟处开始核心代码……

Thought: 已有足够信息制定计划
Action: create_plan(chapters=[...])
```

---

## 工具定义

```python
# 注册到 ToolRegistry
PLANNER_TOOLS = [
    Tool(
        name="send_frames_to_llm",
        description="将指定帧（base64 编码）发送给 LLM Provider 进行视觉分析，"
                    "返回帧内容的文字描述和结构分析。"
                    "当需要了解视频某段的内容时使用此工具。",
        execute=send_frames_to_llm
    ),
    Tool(
        name="create_plan",
        description="当已分析足够的帧、对视频结构有清晰认识后，"
                    "调用此工具输出最终的转换计划。",
        execute=create_plan
    )
]
```

---

## 错误处理

| 错误情况 | 处理方式 |
|---------|---------|
| 采样帧数量为 0 | 抛出 `PlannerError("无采样帧可用")` |
| LLM 输出解析失败 | 重试一次，附加"上次输出格式不符"的提示；仍失败则返回默认单章计划 |
| 章节时间覆盖不完整（有空白段） | 自动补全为 "其他内容" 章节 |
| 超过最大步数（默认 10 步） | 以当前已有信息输出计划，记录警告日志 |
