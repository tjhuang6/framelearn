# 问答模块接口设计

## 职责

教材生成完成后，允许用户针对视频内容提问，返回基于实际视频内容的准确回答。

不把整个教材塞进上下文，而是：
1. 将教材向量化存入 Chroma 向量数据库
2. 问题来时先检索最相关的段落
3. 用检索结果 + 原始帧辅助构建上下文回答

---

## 架构

基于 HelloAgents 第8章的 RAGTool + 记忆系统：

```
用户提问
   ↓
QueryExpander（查询扩展，生成 3 个近义查询）
   ↓
VectorSearch（Chroma 检索，取 top-5 段落）
   ↓
ContextBuilder（GSSC：选取最相关的段落，控制 token 数）
   ↓
LLM Provider（生成回答）
   ↓
Memory（存储对话历史，支持多轮追问）
```

---

## 数据结构

### 向量数据库文档格式

教材每个章节拆成多个文档片段存入 Chroma：

```python
@dataclass
class TutorialChunk:
    chunk_id: str           # "{chapter_index}_{segment_index}"
    chapter_title: str
    chapter_index: int
    text: str               # 文字稿内容（书面整理后）
    timestamp_sec: float    # 对应视频时间戳
    video_url: str          # 带时间戳的跳转链接
    has_code: bool          # 是否包含代码内容
    frame_path: str | None  # 对应关键帧路径（可选）
```

分块策略：
- 每个 `ContentSegment`（约 30 秒内容）作为一个 chunk
- 代码块单独作为一个 chunk，保持完整
- 相邻 chunk 有 1 个 chunk 的重叠（防止语义被截断）

### 问答接口

```python
@dataclass
class QAQuery:
    question: str
    session_id: str = "default"     # 支持多会话
```

```python
@dataclass
class QAAnswer:
    answer: str
    source_chunks: list[TutorialChunk]  # 引用的来源片段
    timestamp_links: list[str]          # 视频时间戳链接（方便跳转）
```

---

## 接口

```python
class QAModule(SimpleAgent):
    def __init__(
        self,
        llm: HelloAgentsLLM,
        chroma_path: str = "output/chroma_db"
    ): ...

    def index_tutorial(self, tutorial_path: str, analyzed_chapters: list[AnalyzedChapter]):
        """
        将教材向量化存入 Chroma。
        - 按章节 + 片段分块
        - 使用 sentence-transformers 生成嵌入向量
        - 存储时附带元数据（时间戳、章节、是否有代码）
        """

    def ask(self, query: QAQuery) -> QAAnswer:
        """
        处理用户提问，返回基于检索的回答。
        支持多轮对话（通过 session_id 区分）。
        """

    def _retrieve(self, question: str, n_results: int = 5) -> list[TutorialChunk]:
        """
        多查询扩展（MQE）检索：
        生成 3 个近义查询，分别检索，结果去重后取 top-5。
        """

    def _build_context(self, chunks: list[TutorialChunk], question: str) -> str:
        """
        用 ContextBuilder（GSSC）从检索结果中选取最相关的内容，
        控制总 token 数不超过上限（默认 4000 token）。
        """
```

---

## 回答 Prompt

```
你是一个编程教学助手，专门回答关于以下教学视频的问题。
视频标题：{video_title}

以下是从教材中检索到的相关内容：

{retrieved_context}

对话历史：
{conversation_history}

用户问题：{question}

请基于以上内容回答问题。如果检索内容中没有直接答案，
可以结合上下文推断，但要说明这是推断。
如果问题涉及截图、画面细节或"那段代码长什么样"等视觉内容，
请回复：该问题需要查看截图，并附上对应时间戳链接。
如果问题超出视频内容范围，请如实说明。
```

---

## 多轮对话支持

使用 HelloAgents 的 `MemoryTool` 存储对话历史：

```python
# 每个 session_id 对应独立的对话历史
memory_tool = MemoryTool()
memory_tool.add(session_id, role="user", content=question)
memory_tool.add(session_id, role="assistant", content=answer)
```

检索时将最近 3 轮对话拼入 Prompt，支持追问（"刚才说的那个方法怎么用？"）。

---

## CLI 交互

```bash
$ python -m framelearn ask "第 3 步为什么要用虚拟环境？"

🔍 正在检索相关内容……
📖 找到 3 段相关内容（来自章节 2、4）

在第二章（00:08:20）中讲解了虚拟环境的作用……

📌 相关视频片段：
  - 00:08:20 https://bilibili.com/video/xxx?t=500
  - 00:15:33 https://bilibili.com/video/xxx?t=933
```

---

## 错误处理

| 情况 | 处理方式 |
|-----|---------|
| 向量数据库未初始化 | 提示用户先运行 `framelearn run` 生成教材 |
| 检索结果为空 | 直接用问题调用 Claude，告知"未找到相关内容" |
| 问题超出视频内容 | Claude 在回答中说明，不强制回答 |
| 多轮追问上下文丢失 | session_id 不存在时从空历史开始 |
