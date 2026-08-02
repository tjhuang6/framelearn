# OpenSpec: 视频顺序讲稿生成（分段 + visual_script 模式）

> 状态：实施前规格，现保留用于追溯。`visual_script`、按 SRT/字数分段、段落缓存和逐段生成已经进入 `DocumentGenerator`，但具体类名、触发条件、重试和输出以 [`pipeline-overview.md`](pipeline-overview.md) 为准；本文中的“当前问题”和成本估算已不代表当前代码。

## 目标

解决当前问题：
1. ✅ 关键帧带时间戳（已完成）
2. ❌ 只用前 12000 字 + 前 20 帧 → **全量处理**
3. ❌ 生成教材而非讲稿 → **visual_script 模式**

最终效果：
- 3 小时视频的全部字幕和关键帧都被使用
- 生成按时间顺序的图文讲稿，不是知识点重排的教材
- 保留老师的讲解顺序、口语风格、教学过程

---

## 1. 新增 visual_script 模式

### Prompt 设计

```markdown
你是视频字幕转图文讲稿助手。

**任务**：把视频字幕（ASR 转写）转换为图文 Markdown 讲稿。

**核心原则**：
1. 严格保持老师讲解的时间顺序，不重排内容
2. 不总结、不提炼、不删减教学过程
3. 不补充视频中没有说过的知识
4. 把口语转成自然、完整的书面语（去掉"然后"、"这个"等口头禅）
5. 在时间轴对应位置插入关键帧

# 输入

## 字幕（按时间顺序）
<subtitle>
{subtitle}
</subtitle>

## 关键帧（时间戳 + 路径）
<frames>
关键帧 1 (03:45): frame_00h03m45s.jpg
关键帧 2 (15:22): frame_00h15m22s.jpg
...
</frames>

# 输出要求

1. **按字幕时间顺序逐段转写**
   - 每段对应 30-120 秒的讲解
   - 段落结构：老师说什么 → 你写什么
   - 不要把"先讲 A 再讲 B"重排成"B 的知识点、A 的知识点"

2. **插入关键帧**
   - 在讲到对应时间时插入：`![](src/frame_00h03m45s.jpg)`
   - 如果字幕提到"看这张图"、"如图所示"，立即在此处插图
   - 如果附近没有关键帧，说明"（讲师展示了画面，但未被抽帧）"

3. **口语书面化**
   - ❌ "那么这个呢就是说我们这个FastAPI啊"
   - ✅ "FastAPI 的路由机制如下"
   - 保留讲解的逻辑顺序，去除冗余口头禅

4. **代码片段**
   - 提取代码，标注语言：```python
   - 如果字幕有逐行讲解，保留讲解内容

5. **格式**
   - 分段用 `##` 标题（按时间命名，如 `## 03:00-06:30 FastAPI 路由基础`）
   - 不要用 bullet points 列知识点
   - 正文是连贯的段落叙述

# 示例

## 输入
字幕：
```
[00:03:45] 好那现在我们来看一下这个FastAPI的路由啊，然后呢这个路由的话就是说...
[00:04:12] 然后你看这张图，这个就是我们的代码...
```

关键帧：
```
关键帧 1 (03:45): frame_00h03m45s.jpg
```

## 输出
```markdown
## 03:45-04:30 FastAPI 路由机制

FastAPI 的路由机制基于装饰器。通过 `@app.get()` 可以定义一个 GET 请求的路由。

![FastAPI 路由代码示例](src/frame_00h03m45s.jpg)

代码中展示了基本的路由定义：

\```python
@app.get("/items/{item_id}")
def read_item(item_id: int):
    return {"item_id": item_id}
\```

这段代码定义了一个路径参数 `item_id`，FastAPI 会自动进行类型转换和验证。
```

---

## 2. 分段生成流程

### 数据结构

```python
@dataclass
class Segment:
    index: int                          # 段落索引
    start_time: float                   # 起始秒数
    end_time: float                     # 结束秒数
    subtitle: str                       # 这段的字幕
    keyframes: list[tuple[Path, float]] # 这段的关键帧
```

### 切分策略

```python
def split_segments(
    subtitle: str,
    keyframes: list[tuple[Path, float]],
    segment_duration: int = 90  # 90 秒一段（可配置 30-120）
) -> list[Segment]:
    """
    按时间切分字幕和关键帧。
    
    - 如果有 SRT 时间戳：按时间精确切分
    - 如果只有纯文本：按字数估算（每秒 ~3-5 字）
    """
    pass
```

### 生成流程

```python
class SegmentedDocumentGenerator:
    def generate(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        video_title: str,
        mode: Literal["visual_script", "notes", "textbook"] = "visual_script",
    ) -> str:
        # 1. 切分
        segments = split_segments(subtitle, keyframes)
        
        # 2. 逐段生成
        segment_results = []
        for seg in segments:
            md = self._generate_segment(seg, mode)
            segment_results.append(md)
        
        # 3. 合并
        full_md = self._merge_segments(segment_results, video_title)
        return full_md
    
    def _generate_segment(self, segment: Segment, mode: str) -> str:
        """生成单段讲稿（调用 LLM）"""
        prompt = self._build_prompt(
            keyframes=segment.keyframes,
            subtitle=segment.subtitle,
            mode=mode
        )
        
        if self.vision_mode == "appserver":
            return self._call_appserver(prompt, segment.keyframes)
        else:
            return self._call_api(prompt, segment.keyframes)
    
    def _merge_segments(self, segments: list[str], title: str) -> str:
        """合并所有段落为完整文档"""
        return f"# {title}\n\n" + "\n\n".join(segments)
```

---

## 3. 配置项

`settings.toml` 新增：

```toml
[doc_generation]
mode = "visual_script"          # visual_script / notes / textbook
segment_duration = 90           # 每段时长（秒）
max_keyframes_per_segment = 10  # 每段最多关键帧数
```

---

## 4. 实现顺序

### Phase 1: 新增 visual_script prompt（30 分钟）
- 在 `doc_generator.py` 加 `_VISUAL_SCRIPT_PROMPT`
- 修改 `_build_prompt()` 支持 3 种模式
- 测试单段生成效果

### Phase 2: 实现切分逻辑（1 小时）
- 新增 `segment_splitter.py`
- 实现 `split_segments()` 函数
  - 优先用 SRT 时间戳切分
  - fallback 按字数估算
- 为每段分配对应的关键帧

### Phase 3: 分段生成 + 合并（1 小时）
- 新增 `SegmentedDocumentGenerator` 类
- 实现逐段调用 LLM
- 实现段落合并（保留 `##` 标题）
- 添加进度显示（"生成中 3/18 段..."）

### Phase 4: 集成到 pipeline（30 分钟）
- `video_pipeline.py` 切换到 `SegmentedDocumentGenerator`
- 保留旧的 `DocumentGenerator` 作为 fallback
- 配置项控制是否启用分段

---

## 5. 成本估算（3 小时视频）

### 当前方案（单次生成）
```
字幕：前 12000 字
关键帧：前 20 张
LLM 调用：1 次
成本：~0.5 元（GPT-4V）
```

### 新方案（分段生成）
```
字幕：全部（~5 万字）
关键帧：全部（~80 张）
切分：120 段（每段 90 秒）
LLM 调用：120 次
成本：~15 元（Qwen3-VL-8B）或 ~3 元（DeepSeek-V3.2 纯文本）
```

**优化策略**：
- 用 Qwen3-VL-8B（0.7元/百万 tokens）而非 GPT-4V
- 或用 DeepSeek-V3.2（0.5元/百万 tokens）纯文本模式
- 关键帧只在需要时传给 LLM（如字幕提到"如图"）

---

## 6. 测试验收

### 输入
- 3 小时编程教程视频
- 80 个关键帧（带时间戳）
- 5 万字字幕

### 预期输出
```markdown
# Python FastAPI 从入门到实战

## 00:00-01:30 课程介绍

本节课将介绍 FastAPI 框架的核心特性...

## 01:30-03:00 安装与环境配置

首先需要安装 FastAPI 和 Uvicorn...

![安装命令](src/frame_00h01m45s.jpg)

\```bash
pip install fastapi uvicorn
\```

## 03:00-04:30 第一个 FastAPI 应用

...（120 段）
```

### 验证点
- ✅ 所有关键帧都被使用（不只前 20 张）
- ✅ 所有字幕都被转写（不只前 12000 字）
- ✅ 时间顺序正确（段落标题递增）
- ✅ 关键帧插在对应时间的段落里
- ✅ 口语书面化（无"这个"、"然后"）
- ✅ 保留讲解顺序（不重排知识点）

---

## 7. 风险与降级

### 风险 1：分段太多，成本过高
- **降级**：增大 `segment_duration` 到 120 秒
- **降级**：只对有关键帧的段落调用 Vision API，其他用纯文本 LLM

### 风险 2：段落合并不自然
- **降级**：让最后一次 LLM 调用负责"润色衔接"
- **降级**：加一个后处理步骤，检查段落边界

### 风险 3：SRT 时间戳不可用
- **降级**：按字数估算（每秒 3-5 字）
- **降级**：用关键帧时间戳反推字幕时间

---

## 8. 未来扩展（Agent 化）

在分段生成的基础上，可以加 Agent 决策：

```python
for segment in segments:
    # 1. 生成初稿
    draft = self._generate_segment(segment)
    
    # 2. Agent 检查
    issues = self._check_quality(draft, segment)
    
    # 3. 决策
    if "缺少关键帧" in issues:
        # 重新提取这段的帧
        new_frames = self._extract_more_frames(segment.start, segment.end)
        segment.keyframes.extend(new_frames)
        draft = self._generate_segment(segment)  # 重试
    
    if "图片不清楚" in issues:
        # 升级模型
        draft = self._generate_segment(segment, model="qwen3-vl-32b")
    
    segment_results.append(draft)
```

---

## 开始实现？

现在开始 **Phase 1**：加 `_VISUAL_SCRIPT_PROMPT`，30 分钟搞定。
