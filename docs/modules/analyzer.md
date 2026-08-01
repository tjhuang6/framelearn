# 内容分析器接口设计

## 职责

接收全量阶段提取的帧和 OCR 文字，判断哪些帧值得纳入教材，并将关键帧与 Whisper 文字稿按时间戳对齐。输出"帧 + 对应文字稿片段"的配对列表，供文档生成器使用。

---

## 核心决策：哪些帧是"关键帧"

判断标准（满足其中之一即纳入）：

| 标准 | 说明 |
|-----|------|
| 代码发生变化 | 当前帧的 OCR 文字与前一帧显著不同（Jaccard 相似度 < 0.7）|
| 出现错误信息 | 帧中包含 "Error"、"Traceback"、"Exception"、"错误" 等关键词 |
| 终端输出 | 出现命令提示符（`$`、`>`、`>>>`）且有输出内容 |
| 新文件/新窗口 | 文件名、窗口标题或顶部 import 语句发生明显变化 |
| 场景切换点 | 对应 `detect_scene_changes` 返回的时间戳附近（±2 秒）|

不满足任何标准的帧直接丢弃，保持输出精简。

---

## 数据结构

### 输入

```python
@dataclass
class AnalyzerInput:
    chapters: list[Chapter]                         # 来自规划 Agent 的转换计划
    frames_by_chapter: dict[int, FrameExtractionResult]  # chapter_index → 帧列表
    ocr_results: list[OcrResult]                    # 每帧的 OCR 结果
    transcript: list[TranscriptRow]                 # 清洗后的带时间戳文字稿
    scene_changes: list[float]                      # 场景切换时间戳
```

### 输出

```python
@dataclass
class AnalyzedChapter:
    chapter: Chapter
    segments: list[ContentSegment]      # 该章节的内容片段列表

@dataclass
class ContentSegment:
    key_frame_path: str                 # 关键帧路径
    frame_timestamp: float              # 帧时间戳（秒）
    transcript_text: str                # 该帧对应时间窗口的文字稿
    ocr_text: str                       # 帧内 OCR 文字（代码内容）
    segment_type: str                   # "code_change" | "error" | "terminal" | "new_file" | "scene"
```

---

## 接口

```python
class ContentAnalyzer(SimpleAgent):
    def __init__(self, llm: HelloAgentsLLM): ...

    def analyze(self, input: AnalyzerInput) -> list[AnalyzedChapter]:
        """
        对每个章节：
        1. 筛选该章节的关键帧
        2. 将关键帧与文字稿按时间戳对齐
        3. 可选：发送给 LLM Provider 确认筛选结果（视频内容复杂时）
        返回带内容片段的章节列表。
        """

    def _filter_key_frames(
        self,
        frames: list[str],
        timestamps: list[float],
        ocr_results: list[OcrResult],
        scene_changes: list[float]
    ) -> list[tuple[str, float, str]]:  # (frame_path, timestamp, segment_type)
        """本地规则筛选，不调用 LLM。"""

    def _align_with_transcript(
        self,
        frame_timestamp: float,
        transcript: list[TranscriptRow],
        window_sec: float = 30.0
    ) -> str:
        """
        取 frame_timestamp ± window_sec 时间窗口内的文字稿句子，
        拼合为一段文字稿文本。
        """
```

---

## 时间戳对齐逻辑

每个关键帧有时间戳 `t`。文字稿对齐取以下范围：
- 向前：`t - 15` 秒（关键帧前的解释）
- 向后：`t + 15` 秒（关键帧后的继续讲解）

如果与相邻关键帧的时间窗口重叠，按两帧的中点切分，避免重复。

```
关键帧 A (t=120s)    关键帧 B (t=160s)
  [105s .............. 140s]  ← A 的文字稿窗口
                 [140s .............. 175s]  ← B 的文字稿窗口
                  ↑
              中点 140s 处切分
```

---

## 是否调用 Claude

内容分析器的关键帧筛选**优先使用本地规则**（速度快、成本低）。

以下情况才发送给 Claude 辅助确认：
- 某章节筛选出的关键帧数量 < 2（可能漏掉了重要内容）
- 某章节的 OCR 置信度普遍很低（代码识别失败，无法用文字差异判断）

Claude 辅助确认的 Prompt：
```
以下是第 {n} 章（{title}）的帧描述和时间戳。
请确认哪些帧展示了关键的代码变化或重要教学内容，
哪些帧可以安全跳过（如停留在同一段代码的重复帧）。
```

---

## 错误处理

| 情况 | 处理方式 |
|-----|---------|
| 某章节帧全部不满足关键帧标准 | 保留该章节中 OCR 文字最多的帧（至少保留 1 帧）|
| 文字稿时间段与帧时间戳对齐失败 | 返回空文字稿，不抛出异常 |
| Claude 辅助确认解析失败 | 退回到本地规则的筛选结果 |
