# 文档生成器接口设计

> 状态：历史目标设计。当前实现是 `framelearn/pipeline/doc_generator.py`，输入为字幕与 `(Path, timestamp)` 关键帧，输出 `index.md`、`notes.md` 和可选分段缓存；没有本文所述 `AnalyzedChapter`/时间戳链接接口。请以 [`../pipeline-overview.md`](../pipeline-overview.md) 为准。

## 职责

接收内容分析器输出的"关键帧 + 对应文字稿"配对，生成完整的 Markdown 教材。

主要任务：
1. 将 Whisper 口语化文字稿整理成流畅的书面表达
2. 按章节结构组织内容，插入截图和代码块
3. 添加时间戳链接指向源视频
4. 生成后运行自我批评检查，补全遗漏

---

## 数据结构

### 输入

```python
@dataclass
class GeneratorInput:
    analyzed_chapters: list[AnalyzedChapter]    # 来自内容分析器
    plan: ConversionPlan                        # 来自规划 Agent
    video_url: str                              # 源视频 URL（用于生成时间戳链接）
    video_title: str
    output_dir: str                             # 输出目录（默认 "output/"）
```

### 输出

```python
@dataclass
class GeneratorOutput:
    tutorial_path: str              # Markdown 文件路径（output/tutorial.md）
    frame_dir: str                  # 关键帧目录（output/frames/）
    chapter_count: int
    total_words: int                # 教材总字数（估算）
    critique_issues: list[str]      # 自我批评发现的问题（已修复）
```

---

## 接口

```python
class DocGenerator(SimpleAgent):
    def __init__(self, llm: HelloAgentsLLM): ...

    def generate(self, input: GeneratorInput) -> GeneratorOutput:
        """
        为每个章节生成内容，写入 Markdown 文件。
        生成完成后运行自我批评检查。
        """

    def _generate_chapter(
        self,
        chapter: AnalyzedChapter,
        video_url: str
    ) -> str:
        """
        生成单个章节的 Markdown 内容：
        1. 写入 ## 章节标题和时间范围
        2. 调用 LLM Provider 整理文字稿 → 书面表达
        3. 插入关键帧截图
        4. 格式化代码块（OCR 文字 + 语言标签）
        5. 添加时间戳链接
        """

    def _run_critique(
        self,
        draft: str,
        plan: ConversionPlan
    ) -> tuple[str, list[str]]:
        """
        自我批评检查，返回修复后的内容和问题列表。
        检查项：所有章节是否覆盖、是否有章节缺少截图、代码块是否完整。
        """
```

---

## Markdown 输出格式

每个章节的格式：

```markdown
## 第二章：定义神经网络层

> 00:12:30 – 00:18:45 · [跳转到视频](https://bilibili.com/video/xxx?t=750)

我们来定义 `Layer` 类，它接收输入维度和输出维度作为参数。
初始化时，权重用随机数填充，偏置初始化为零。

![第二章关键帧](frames/chapter_2_frame_750s.jpg)

`forward` 方法接收输入张量，返回线性变换的结果：

```python
class Layer:
    def __init__(self, nin, nout):
        self.weights = [[random() for _ in range(nin)] for _ in range(nout)]
        self.bias = [0.0] * nout
```
```

规则：
- 章节标题用 `##`（二级标题），不用一级
- 时间范围用 blockquote 格式
- Bilibili 时间戳链接格式：`?t={秒数}`；YouTube：`?t={秒数}s`
- 代码块必须附带语言标签（从 OCR 上下文推断：Python / bash / json 等）
- 截图路径使用相对路径

---

## 文字稿整理 Prompt

```
以下是编程教学视频某段的原始转写文字稿（包含时间戳）。
请将其整理为清晰流畅的书面表达：
- 去除口语化表达（嗯、啊、就是说、然后）
- 去除重复和填充词
- 保留所有技术内容和步骤说明
- 不要添加原文没有的内容
- 用中文输出，约 100-200 字

原始文字稿：
{transcript_text}
```

---

## 自我批评检查项

```python
CRITIQUE_CHECKS = [
    "计划中的所有章节是否都出现在教材中？",
    "是否有章节没有任何截图？",
    "是否有代码块被截断（缺少结尾的 ``` ）？",
    "是否有章节的文字内容过短（少于 50 字）？",
]
```

发现问题后，生成器会回头补全对应章节，然后重新检查一次。

---

## 文件输出结构

```
output/
├── tutorial.md         # 完整教材
└── frames/
    ├── chapter_0/
    │   ├── frame_30s.jpg
    │   └── frame_120s.jpg
    ├── chapter_1/
    │   └── frame_480s.jpg
    └── ...
```

帧文件名格式：`frame_{时间戳整数}s.jpg`
