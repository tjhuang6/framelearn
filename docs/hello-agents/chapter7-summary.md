# 第七章《构建你的 Agent 框架》总结

## 概述

本章从零构建 **HelloAgents** 框架——一个轻量级、教学友好的 Agent 框架。核心理念是"万物皆为工具"：Memory、RAG、MCP 等模块，在 HelloAgents 里都被统一抽象为工具，消除不必要的抽象层，让学习者专注于"Agent 调用工具"这一核心逻辑。

框架遵循三个设计原则：**分层解耦、职责单一、接口统一**。

---

## 框架整体架构

```
hello_agents/
├── core/
│   ├── agent.py          Agent 基类（定义 run/add_message 等接口）
│   ├── llm.py            HelloAgentsLLM（统一 LLM 调用接口）
│   ├── message.py        消息数据结构
│   ├── config.py         配置管理（单例）
│   └── exceptions.py     统一异常体系
├── agents/
│   ├── simple_agent.py   基础对话 Agent
│   ├── react_agent.py    ReAct Agent
│   ├── reflection_agent.py  Reflection Agent
│   └── plan_solve_agent.py  Plan-and-Solve Agent
└── tools/
    ├── base.py           工具基类（BaseTool）
    ├── registry.py       工具注册表（ToolRegistry）
    ├── chain.py          工具链
    ├── async_executor.py 异步工具执行器
    └── builtin/
        ├── calculator.py
        └── search.py
```

---

## HelloAgentsLLM：统一 LLM 接口

### 为什么要封装

第四章的 `HelloAgentsLLM` 是简单的单提供商封装。框架化版本解决了三个问题：
1. 多提供商支持（OpenAI、ModelScope、智谱、Kimi 等）
2. 本地模型集成（Ollama、VLLM）
3. 减少配置负担（自动检测提供商）

### 自动检测机制

`_auto_detect_provider` 按优先级推断服务商：

```
优先级 1：检查特定服务商的环境变量
  MODELSCOPE_API_KEY → "modelscope"
  OPENAI_API_KEY     → "openai"
  ZHIPU_API_KEY      → "zhipu"

优先级 2：解析 LLM_BASE_URL
  "api-inference.modelscope.cn" → "modelscope"
  "localhost:11434"             → "ollama"
  "localhost:8000"              → "vllm"

优先级 3：分析 API Key 格式（辅助判断）

默认：返回 "auto"
```

好处：用户只需配置 `.env`，代码里 `HelloAgentsLLM()` 不传任何参数，框架自动配好。

---

## Agent 基类设计

### Message 类

用 Pydantic `BaseModel` 验证数据：

```python
class Message(BaseModel):
    content: str
    role: str  # "user" | "assistant" | "system"
    timestamp: datetime
```

选择 Pydantic 的原因：强制类型校验，序列化/反序列化方便，IDE 有自动补全。

### Agent 基类

```python
class Agent(ABC):
    def run(self, input_text: str) -> str:
        # 公开接口：记录历史、调用 _execute、处理异常
        ...

    @abstractmethod
    def _execute(self, input_text: str) -> str:
        # 子类实现：具体的 Agent 逻辑
        ...
```

`run` 是模板方法（Template Method 模式）：固定骨架（历史管理、异常处理），把核心逻辑委托给子类的 `_execute`。好处：所有 Agent 共享相同的外部接口，内部逻辑各自实现。

### Config 单例

```python
class Config:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = Config()
        return cls._instance
```

配置用单例是因为：整个应用只需要一份配置，避免多处实例化导致配置不一致。

---

## 四种 Agent 范式的框架化

### SimpleAgent

基础对话 Agent，核心是维护 `_history` 消息列表，把历史消息拼进每次 LLM 调用。

关键点：`_history` 只在同一实例内持久，重启进程后丢失。这是第8章记忆系统要解决的问题。

### ReActAgent

框架化 ReAct 与第四章的手写版相比，三个主要改进：
1. 用 `ToolRegistry` 统一管理工具，而不是手动传入工具函数
2. 提示词模板可配置（不再硬编码）
3. 更严谨的格式约束，降低解析失败率

核心循环：

```python
while current_step < max_steps:
    prompt = template.format(tools=registry.get_tools_description(), ...)
    response = llm.invoke(messages)
    thought, action = parse_output(response)
    if action.startswith("Finish"):
        return parse_final_answer(action)
    observation = registry.execute_tool(tool_name, tool_input)
    history.append(f"Action: {action}\nObservation: {observation}")
```

### ReflectionAgent

框架版采用**通用化 Prompt**（不再专门针对代码生成），通过 `custom_prompts` 参数支持用户深度定制：

```python
agent = MyReflectionAgent(
    name="代码优化助手",
    llm=llm,
    custom_prompts={
        "initial": "你是Python专家，请编写:{task}",
        "reflect": "请审查代码效率:\n任务:{task}\n代码:{content}",
        "refine": "根据反馈优化:\n任务:{task}\n反馈:{feedback}"
    }
)
```

### PlanAndSolveAgent

与第四章实现基本一致，框架化后的改进是把规划 Prompt 和执行 Prompt 提取为可配置参数。

---

## 工具系统

### BaseTool

```python
class BaseTool(ABC):
    name: str
    description: str    # 最重要！LLM 靠这个决定何时调用这个工具

    @abstractmethod
    def execute(self, input_data: str) -> str: ...

    def to_openai_schema(self) -> dict: ...  # 用于 Function Calling
```

**工具描述是整个系统最重要的部分**。描述写得清不清楚，直接决定 Agent 能不能正确使用工具。

### ToolRegistry

```python
class ToolRegistry:
    def register(self, tool: BaseTool): ...
    def execute_tool(self, name: str, input: str) -> str: ...
    def get_tools_description(self) -> str: ...  # 生成供 Prompt 使用的描述字符串
    def to_openai_schema(self) -> list: ...       # 生成 Function Calling Schema
```

Registry 与 Agent 解耦：Agent 不关心有哪些工具，只调用 `registry.execute_tool(name, input)`。

### 工具链（ToolChain）

把多个工具串联，前一个工具的输出作为后一个的输入：

```python
chain = ToolChain([
    ToolStep("search", input_template="搜索:{query}", output_key="search_result"),
    ToolStep("calculator", input_template="计算:{search_result}", output_key="result")
])
```

适用场景：固定顺序的多步处理流程（搜索 → 提取 → 计算）。

### 异步工具执行器

```python
class AsyncToolExecutor:
    async def execute_tools_parallel(self, tasks: list) -> list:
        # 用线程池并行执行多个工具，asyncio.gather 等待全部完成
```

当多个工具调用相互独立时（如同时搜索多个关键词），并行执行可以显著降低总耗时。

---

## 与 FrameLearn 的关联

HelloAgents 第7章是 FrameLearn 的**直接技术基础**，五个模块都继承自这里的设计：

| Hello-Agents 第7章知识点 | FrameLearn 中的体现 |
|---|---|
| `SimpleAgent` 基类 | FrameLearn 五个模块（规划/分析/生成/问答）均继承 `SimpleAgent` |
| `ToolRegistry` 工具注册机制 | 工具执行器：注册 `download_video`、`extract_audio`、`run_ocr` 等工具 |
| `BaseTool` 抽象基类 | FrameLearn 的每个工具（yt-dlp 封装、ffmpeg 封装等）实现 `execute` 方法 |
| `HelloAgentsLLM` 多提供商支持 | FrameLearn 默认用 Claude API，但框架允许切换到其他提供商 |
| `AsyncToolExecutor` 并行执行 | 内容分析器筛选关键帧时，多帧可以并行送给 Claude API 分析 |
| 工具描述设计 | 工具执行器里每个工具的 description 决定规划 Agent 何时调用它 |

FrameLearn 的**工具执行器**完全对应第7章的工具系统：

```
FrameLearn 工具注册表：
  download_video    → "从 YouTube/Bilibili URL 下载视频文件到本地"
  extract_audio     → "从视频文件提取 MP3 音频"
  transcribe_audio  → "将音频文件转为带时间戳的文字稿"
  extract_frames    → "按时间范围从视频提取帧图像"
  run_ocr           → "从图像中提取文字内容"
```

---

*来源：[Hello-Agents 第七章](https://github.com/datawhalechina/Hello-Agents)，Datawhale 开源课程*
