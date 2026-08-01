# 第四章《智能体经典范式构建》总结

## 概述

本章从零手写三种经典 Agent 范式：**ReAct**、**Plan-and-Solve**、**Reflection**。不依赖框架，直接用 Python + OpenAI 接口实现，目的是理解每种范式背后的决策逻辑，而不只是会调用 API。

---

## ReAct（Reasoning + Acting）

### 解决什么问题

纯推理（Chain-of-Thought）不能与外部世界交互；纯行动（直接调用工具）缺乏规划。ReAct 把两者结合：**思考指导行动，行动结果反过来修正思考**。

### 工作流程

```
Thought: 分析当前情况，决定下一步
Action: tool_name[tool_input]
Observation: 工具返回的结果
Thought: 根据结果调整……
（循环，直到 Action: Finish[最终答案]）
```

每一步的输出都是固定格式的文本，用正则表达式解析出 `Thought` 和 `Action`，然后执行工具，把 `Observation` 拼回上下文。

### 核心组件

**ToolExecutor（工具执行器）**

```python
class ToolExecutor:
    def registerTool(self, name, description, func): ...
    def getAvailableTools(self) -> str: ...  # 生成工具描述字符串，插进 Prompt
    def getTool(self, name) -> callable: ...
```

工具描述字符串直接插进系统 Prompt，告诉 LLM 有哪些工具可用。**描述写得好不好，直接决定 LLM 会不会正确选择工具。**

**系统 Prompt 结构**

```
你是一个能使用工具的智能体。
可用工具：
{tools_description}

格式要求（必须严格遵循）：
Thought: ...
Action: tool_name[input]
Observation: （由系统填入）
...
Action: Finish[最终答案]

问题：{question}
历史：{history}
```

### 特点与局限

优点：
- 可解释性强，每步 Thought 清晰可见
- 动态纠错：根据 Observation 随时调整方向
- 天然支持工具协同

局限：
- 高度依赖 LLM 格式遵循能力，提示词脆弱
- 串行执行，多步任务延迟高
- 步进式决策，缺乏全局规划，可能局部最优

### 调试技巧

- 打印完整的格式化 Prompt，追溯 LLM 决策来源
- 工具报错时打印原始 LLM 输出，判断是 LLM 格式问题还是解析逻辑问题
- 频繁出错时在 Prompt 里加 few-shot 示例（1-2 个完整 Thought-Action-Observation 成功案例）

---

## Plan-and-Solve（先规划，后执行）

### 解决什么问题

ReAct 是"走一步看一步"，对于逻辑路径确定的多步任务效率低，也容易在中间步骤迷失方向。Plan-and-Solve 先生成完整计划，再按计划逐步执行，保持全局一致性。

### 工作流程

两个 LLM 调用阶段：

```
阶段一（规划）：
输入：原始问题
输出：步骤列表 ["步骤1", "步骤2", "步骤3", ...]

阶段二（执行）：
对每个步骤：
  输入：原始问题 + 完整计划 + 前面步骤的结果
  输出：该步骤的解决方案
```

形式化表达：
- 规划：`P = π_plan(q)` → 生成 n 步计划
- 执行：`s_i = π_solve(q, P, (s_1, ..., s_{i-1}))` → 每步依赖前面结果

### 设计要点

**规划 Prompt** 要求模型输出 Python 列表格式，方便解析：

```
你是AI规划专家，将问题分解为步骤列表。
输出格式（必须是 Python 列表）：
```python
["步骤1", "步骤2", ...]
```
```

用反引号包裹 + 提取代码块内容 → 比让 LLM 输出纯 JSON 更稳定，因为模型更熟悉代码格式。

**执行 Prompt** 把完整计划和前序结果一起塞进上下文，让 LLM 有全局视野。

### 适用场景

- 逻辑路径明确、可预先分解的任务（多步数学推理、结构化报告）
- 对稳定性要求高、不希望 Agent 中途"跑偏"的场景
- 代码生成（先规划类/函数结构，再逐一实现）

局限：计划是静态的，执行中发现某步无法完成时无法自动调整（需要额外的"动态重规划"机制）。

---

## Reflection（执行 → 反思 → 优化）

### 解决什么问题

LLM 一次输出的质量有天花板。通过引入独立的"评审员"角色对输出进行批评，再让 LLM 根据反馈优化，迭代提升结果质量。这是"以成本换质量"的策略。

### 工作流程

```
初始执行：生成第一版输出（Draft）
↓
反思循环（最多 N 轮）：
  反思（Reflect）：评审员分析 Draft 的不足，给出具体改进建议
  检查：反馈包含"无需改进"? → 提前结束
  优化（Refine）：根据反馈生成改进版
  更新 Memory → 下一轮
↓
输出最终版本
```

### 三组 Prompt 的分工

**执行 Prompt**：直接、具体，说清楚任务要求和输出格式。

**反思 Prompt**：这是核心。角色设定要"极其严格"且**聚焦在具体维度**（如算法效率、逻辑连贯性）。太宽泛的评审会给出无用的"整体很好"之类的反馈。

**优化 Prompt**：把任务、上一版输出、反馈意见一起传入，让 LLM 知道"从哪里出发、往哪里改"。

### Memory 类

```python
class Memory:
    records = []  # [{"type": "execution"/"reflection", "content": ...}]

    def add_record(type, content): ...
    def get_trajectory() -> str: ...    # 序列化成文本插入 Prompt
    def get_last_execution() -> str: ... # 获取最新草稿
```

Memory 把历次执行和反馈序列化成文本，插入优化 Prompt，让 LLM 看到完整的迭代历史。

### 成本收益

成本：每轮迭代至少额外调用 LLM 两次（反思 + 优化），延迟高，不适合实时场景。

收益：把"合格"方案优化成"优秀"方案，发现初版的逻辑漏洞、算法低效、边界情况遗漏。

**适用**：对质量要求高、对延迟不敏感的场景（关键业务代码、技术文档、深度分析报告）。

---

## 三种范式对比

| 范式 | 核心策略 | 强项 | 适用任务 |
|------|---------|------|---------|
| ReAct | 边想边做，动态调整 | 环境适应性、工具协同 | 需要外部信息、探索性任务 |
| Plan-and-Solve | 先规划后执行 | 结构性、稳定性 | 逻辑确定、多步推理任务 |
| Reflection | 执行-反思-优化循环 | 输出质量提升 | 高质量要求、不急于完成的任务 |

---

## 与 FrameLearn 的关联

| Hello-Agents 第4章知识点 | FrameLearn 中的体现 |
|---|---|
| ReAct 范式（Thought→Action→Observation 循环） | 规划 Agent 的核心循环：分析采样帧 → 调用工具 → 观察返回 → 调整计划 |
| ToolExecutor（工具注册与调度） | FrameLearn 的工具执行器：注册 yt-dlp、ffmpeg、Whisper、OCR 四类工具 |
| 工具描述设计（描述决定工具选择） | 每个工具的 description 需要写清楚"在什么情况下调用"，让规划 Agent 做出正确决策 |
| Reflection 范式（自我批评机制） | 文档生成器的自我批评：检查所有章节是否覆盖、截图是否完整、代码块是否完整 |
| 格式化输出解析（Prompt + 正则） | 规划 Agent 输出章节计划（JSON）时，需要设计稳定的格式约束和容错解析 |

具体来说：
- **规划 Agent** 的 ReAct 循环：Thought 分析帧内容 → Action 调用 `send_frames_to_claude` → Observation 获取章节分析 → 输出转换计划
- **文档生成器**的自我批评：对应 Reflection 范式的第一轮反思，检查草稿是否完整，发现遗漏后回头补全
- **工具执行器**的注册机制：直接对应 `ToolExecutor` 模式，按名称注册和调用工具

---

*来源：[Hello-Agents 第四章](https://github.com/datawhalechina/Hello-Agents)，Datawhale 开源课程*
