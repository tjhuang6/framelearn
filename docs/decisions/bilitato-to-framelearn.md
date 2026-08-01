# Bilitato → FrameLearn 迁移决策笔记

> 本文记录从 Bilitato（B站 AI 助手 Chrome 扩展）分析中，决定借鉴哪些设计、
> 以及在 FrameLearn（Python CLI）中如何对应实现。

---

## 背景

Bilitato 和 FrameLearn 有高度相似的核心问题：
- 都需要处理视频字幕/转写文本，送给 LLM 生成结构化内容
- 都需要对 LLM 的 JSON 输出做解析，且 LLM 的字段命名不稳定
- 都需要处理"内容太长"的问题（字幕 / 音频 / 上下文）

Bilitato 是 JavaScript，FrameLearn 是 Python。不能直接复制代码，但核心逻辑可以移植。

---

## 决策一：移植字幕清洗流水线

### 借鉴来源

`utils/subtitleProcessor.js` 的四步清洗流水线。

### 要借鉴的核心逻辑

**第一步：文本规范化**
- 全角字符转半角（`！` → `!`，`，` → `,`，全角空格 → 半角空格）
- 去除括号内容（`[音乐]`、`（字幕）`、`【广告】`）
- 去首尾空格，合并连续空格

**第二步：有效性过滤**
- 过滤长度 < 2 的句子
- 过滤互动词：点赞、投币、关注、转发、收藏、评论区、一键三连、弹幕、订阅
- 过滤纯填充词：嗯、啊、呃、额、那个、这个、就是、其实、然后、所以……
- 对以填充词开头的句子，去掉填充词前缀后重新检验

**第三步：时间窗口合并**
- 30 秒窗口内的连续句子合并成一个块
- 每个块加 `[m:ss]` 时间戳前缀
- 条件：相邻句子时间间隔 ≤ 2 秒，且在同一 30 秒窗口内

**第四步：Jaccard 去重**
- 把文本拆成连续两字的集合（bigram），计算相邻块的 Jaccard 相似度
- 相似度 > 0.85 的相邻块合并，保留较长的那个

### FrameLearn 中的对应位置

`framelearn/tools/transcriber.py` 在调用 Whisper 得到原始转写结果后，立即执行这四步清洗，输出干净的带时间戳文本块列表。

### Python 实现要点

```python
import re
from typing import List, Dict

def normalize(item: dict) -> dict:
    text = item.get("text", "")
    # 全角转半角
    text = "".join(
        chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c
        for c in text
    )
    text = text.replace("　", " ")
    # 去括号内容
    text = re.sub(r"[\[（【(][^\]）】)]*[\]）】)]", "", text)
    return {**item, "text": text.strip()}

def jaccard_bigram(s1: str, s2: str) -> float:
    g1 = {s1[i:i+2] for i in range(len(s1) - 1)}
    g2 = {s2[i:i+2] for i in range(len(s2) - 1)}
    if not g1 or not g2:
        return 0.0
    return len(g1 & g2) / len(g1 | g2)
```

---

## 决策二：借鉴 Prompt 格式约束模式

### 借鉴来源

`utils/promptBuilder.js` 的 `TASK_PROMPT_FORMAT_RULES` 和双标签协议。

### 核心模式

**Prompt 末尾强制附加格式约束**：

不只是说"请输出 JSON"，而是在 Prompt 最后附加一段"技术规范"，精确说明：
- 只输出 X，严禁包含 Y
- 字段名必须是 a/b/c，禁止使用 d/e/f
- 数字字段必须是 JSON 数字，禁止输出中文数字或特殊值

Bilitato 的实践证明这大幅减少了 LLM 输出不稳定的问题。

**双标签协议**（当需要同时输出多个部分时）：

```
请输出以下内容：
<PLAN_START>
（章节计划 JSON）
<PLAN_END>
<NOTES_START>
（补充说明）
<NOTES_END>
```

用标签包裹而不是要求整体输出 JSON，是因为 LLM 在 JSON 外面加解释性文字的概率极高，标签方案对这种情况更鲁棒。

### FrameLearn 中的应用

规划 Agent 的 Prompt 末尾需要附加：

```
【输出规范】
只输出 JSON 数组，严禁包含 Markdown 代码块或解释性文字。
格式：[{"chapter": "字符串", "start_sec": 数字, "end_sec": 数字, "focus": "字符串"}]
start_sec/end_sec 必须是秒数整数，禁止输出时间字符串格式（如 "01:23"）。
```

---

## 决策三：移植 AI 输出容错解析

### 借鉴来源

`utils/resultNormalize.js` 的 `normalizeSegments` 和 `parseTimeToSeconds`。

### 核心问题

让 LLM 输出章节计划 JSON 时，即使 Prompt 里写了字段名要求，LLM 仍然可能输出：
- `start_time` 而不是 `start_sec`
- `title` 而不是 `chapter`
- `"01:23"` 而不是 `83`

### 两层容错策略

**层一：精确别名匹配**

为每个语义字段定义别名列表：
```python
START_ALIASES = ["start_sec", "start", "from", "begin", "time_start", "开始", "起始"]
END_ALIASES   = ["end_sec",   "end",   "to",   "finish", "time_end",  "结束", "截止"]
LABEL_ALIASES = ["chapter", "label", "title", "name", "章节", "标题"]
```

遍历别名列表，找到第一个存在且非空的字段。

**层二：模糊正则匹配**

如果别名都没命中，用正则扫描所有 key：
```python
START_FUZZY = [re.compile(r"start"), re.compile(r"^from$"), re.compile(r"begin")]
```

**时间格式解析**：
```python
def parse_time_to_seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    # 解析 "01:23" → 83, "1:23:45" → 5025
    parts = str(value).strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]
```

### FrameLearn 中的应用位置

`framelearn/planner.py` 的 `_parse_plan_output()` 方法，以及 `framelearn/analyzer.py` 的关键帧筛选结果解析。

---

## 决策四：移植分块 + 重叠 + 合并模式

### 借鉴来源

`utils/asrChunking.js` 的音频分块策略。

### 核心问题

Whisper（本地模型）处理长音频时：
- 不同模型有文件大小限制（Whisper large-v3 约 25MB）
- 长视频的音频文件可能超限，需要切块分别转写再拼接
- 直接切块会在句子中间截断，导致转写错误

### 三个核心算法

**1. 安全时长估算**

```python
def estimate_safe_chunk_seconds(
    total_bytes: int,
    total_duration_sec: float,
    max_bytes: int,
    safety_ratio: float = 0.72
) -> int:
    bytes_per_sec = total_bytes / total_duration_sec
    estimated = int((max_bytes * safety_ratio) / bytes_per_sec)
    return max(45, min(estimated, 600))  # 限制在 [45s, 600s]
```

**2. 有重叠的分块计划**

```python
def build_overlapped_chunk_plan(
    total_duration_sec: float,
    chunk_duration_sec: int,
    overlap_sec: int = 4
) -> list[dict]:
    # 每块有 4 秒与下一块重叠
    # 重叠目的：避免在句子中间切断
    step = chunk_duration_sec - overlap_sec
    chunks = []
    start = 0
    while start < total_duration_sec:
        end = min(start + chunk_duration_sec, total_duration_sec)
        chunks.append({"start_sec": start, "end_sec": end})
        start += step
    return chunks
```

**3. 合并时去重叠区域**

多块转写结果合并时，用文本相似度（而非时间戳）判断重叠边界：

```python
def merge_chunk_rows(existing: list, new_chunk: list, overlap_sec: float) -> list:
    # 跳过 new_chunk 开头与 existing 结尾重复的部分
    # 用文本匹配而不是时间戳，因为重叠区域的时间戳会有偏差
```

### FrameLearn 中的应用位置

`framelearn/tools/transcriber.py`，`Whisper.transcribe()` 方法的长音频处理逻辑。

---

## 决策五：不移植的部分

| Bilitato 模块 | 不移植的原因 |
|---|---|
| `contentCache.js`（chrome.storage） | FrameLearn 是 CLI 工具，缓存直接用文件系统（JSON 文件）|
| `supabaseClient.js` | FrameLearn 暂不需要云端缓存 |
| `contentUi.js` / `contentPage.js` | Chrome Extension DOM 操作，完全不相关 |
| `providerAdapter.js`（多提供商） | FrameLearn 只用 Claude API，用 HelloAgentsLLM 足够；未来需要多提供商时可参考 |

---

## 优先级排序

实现时按以下顺序处理：

1. **字幕清洗流水线**（最高优先级）：Whisper 输出质量直接影响后续所有模块
2. **AI 输出容错解析**（高优先级）：规划 Agent 第一步就要用到
3. **分块 + 重叠 + 合并**（高优先级）：长视频支持的基础
4. **Prompt 格式约束模式**（中优先级）：影响整体稳定性，但可以迭代调整
