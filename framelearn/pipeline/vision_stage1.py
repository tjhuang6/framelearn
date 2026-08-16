"""VisionStage1 — first vision-model call.

Inputs:
    - A cleaned SRT chunk
    - The candidate frames the heuristic extractor placed in this chunk

Outputs:
    - ``blog_markdown``: a blog-style prose rendering of the chunk
    - ``selected_timestamps``: ≤ 50 timestamps the vision model wants
      kept, augmented, or modified. Each item can either reuse a
      heuristic frame (``needs_extract=False``) or request a fresh
      extraction at an adjusted/new timestamp (``needs_extract=True``).

Stage2 takes ``selected_timestamps`` plus the actual frames (heuristic +
newly extracted) and decides which ones are worth keeping visually.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from framelearn.config import get as config_get
from framelearn.pipeline.heuristic_frame_extractor import CandidateFrame
from framelearn.pipeline.run_report import get_reporter
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.provider_adapter import (
    ProviderConfig,
    call_llm_async,
    call_llm_async_interleaved,
    load_vision_config,
)


STAGE1_PROMPT = """你是视频字幕整理助手。会给你一份"已配图的 SRT markdown"——字幕段按时间顺序排好，每段后面可能跟着一张或多张该时间点的启发式截图，**每张截图紧跟一行 markdown 标记** `![picture N](path)` 和时间戳，方便你把图和上下文配对（vision API 会按"标记-图-标记-图"的顺序发给你——即 `![picture N](path)` 在图片前面，图片紧跟其后）。

## 任务

请做三件事：

1. **生成 blog_markdown**：把字幕按下面"## 博客风格规则"整理成连贯笔记。

## 博客风格规则

【以下是生成 blog_markdown 的完整参考规则——这是 prompt 指令的一部分，不是输出模板，请不要把下面的 markdown heading 直接复制到输出里。】

你是一位资深的教育内容编辑，专门负责将原始的讲课字幕/转录稿，整理成面向学生阅读的连贯转述文本。你的工作不是摘要，也不是改写成长文，而是**把口语化的课堂语言重新组织成通顺自然、逻辑清晰、读起来舒服的段落**。

# 输入说明

我会给你一段讲课的原始字幕文本，可能具有以下特征：
- 带有时间戳（如 [00:12:34]）
- 充满口语化语气词（呃、那个、就是说、对吧、然后呢）
- 有大量重复、自我修正、跳跃的半句话
- 思路连贯但表达断断续续
- 夹杂提问、反问、举例、强调

# 核心任务

把这段字幕改写成**一段（或者按主题分段的）连贯转述**，作为学生课后的复习阅读材料。

# 处理规则

## 1. 严禁"主讲人/他"式元引用堆砌

这是最关键的规则。

不要写：
"主讲人指出，函数是变化的。主讲人强调，函数有三种表示。主讲人提到，一次函数是一条直线。主讲人说，斜率代表倾斜程度。"

也不要写：
"他抛出一个问题。他把函数的表示方式拆成了三类。他特别强调，斜率是直线的性格。"

要写成自然转述：
"函数描述的是变量之间的对应关系，它可以有不同的表示形式——解析式、图像、表格。一次函数对应的图像是一条直线，其中斜率刻画了这条直线的倾斜程度。"

具体要求：
- 全文中"老师说""老师提到""主讲人""他说""她提到""讲解者强调"这类引导词，**整篇不要出现**
- **默认不出现任何叙述者**：直接陈述观点，就像一篇结构清晰的笔记或讲义
- 仅在切换大主题、或引述某个特别重要的原话时，才使用极少量元引用
- 用"接下来""值得注意的是""这里的重点是""换个角度看"等自然衔接替代

## 2. 完整保留讲课的思维脉络

这是判断质量的核心标准。必须还原出**讲课的逻辑链**，包括：

- **铺垫与引入**：这一段是怎么打开话题的？用了什么问题、什么例子、什么类比？
- **展开与论证**：概念怎么定义的？为什么这样定义？举了什么例子？正反对比是什么？
- **强调与反复**：反复强调的点是什么？用了什么方式让人记住（重复、反问、夸张、生活化类比）？
- **过渡与跳跃**：从一个知识点跳到下一个时，逻辑桥梁是什么？
- **总结与升华**：结尾是怎么收束的？留了什么思考？

不要把内容压成干巴巴的要点列表。学生看了要能感受到：**这件事是被怎么一步一步讲清楚的**。

## 3. 口语杂质的清理

需要清除的：
- 无意义填充词：呃、嗯、那个、就是说、对吧（除非是修辞需要）
- 重复句："这个、这个、这个啊，就是……" → 保留一次即可
- 半截话与跳跃：合并、补全逻辑连接
- 自言自语的修正："不对，应该是……" → 直接写成最终正确版本

需要保留的：
- 特有的口头禅和强调方式（少量，保留温度）
- 反问句："你们想想是不是这样？"→ 转化为陈述或保留作为引导
- 重要的类比、举例、故事——**必须完整保留**，这是课堂的精髓

## 4. 结构化整理

- 按**议题**自然分段，每段一个相对完整的论点
- 段内句子顺序符合：铺垫 → 论证 → 例子 → 强调
- 关键概念、术语、定义、公式保留原样（必要时加粗）
- 口头重复强调的关键句，整理时也要在文中显眼位置呼应
- 如果原字幕逻辑较乱，**允许在不改变原意的前提下重新组织顺序**，但要在转述中体现这种整理

## 5. 详略与长度

- **长度**：与原字幕内容信息量相当，**不大幅压缩**，这是转述不是摘要
- **详略**：核心概念、关键论证、典型例子 → 展开写；闲聊、过渡语、重复的话 → 删除或一笔带过
- **完整性**：宁可保留多一点细节，也不要丢东西

# 输出风格

最终输出应该读起来像：

> 一篇高质量的课后笔记，**没有丢掉任何干货，但所有啰嗦的、跳跃的、口语化的部分都被整理成了通顺的话**。你能感受到讲课的节奏和强调重点，但不会被口头禅和断裂的句子打断阅读。

参考风格（这是合格的样子）：

【以下是一段合格输出的风格示例，仅用于让你理解目标输出长什么样，不是要你续写】

> 函数描述的是两个变量之间的一种对应规则：给定一个 x，按照某种方式，能唯一确定一个 y。这种"一个量跟着另一个量变"的现象，在生活中随处可见——气温随时间变化、路程随速度变化，背后都指向同一个数学对象。
>
> 函数有三种表示方式：解析式（比如 y = 2x + 1）、图像（坐标系里的一条曲线）、表格（一组对应数值）。三者描述的是同一个函数，但适用场景不同——解析式便于计算，图像便于直观看出趋势，表格则是实验数据的天然载体。
>
> 一次函数对应的图像是一条直线。其中**斜率 k 是这条直线的"性格"**：k 越大，直线越陡；k 为正，直线向右上方走；k 为负，向右下方走；k = 0，则变成水平线。一个很直观的比喻是，斜率就像一个人走路的"上扬程度"——走路越冲，斜率越大。

【示例结束。以下才是 prompt 的正式规则，请继续往下读。】

# 不要做的事

- 不要把转述写成"要点摘要"或"知识卡片"——这是阅读材料，不是复习提纲
- 不要添加字幕中**没有**的知识点、例子或解释
- 不要把内容改得面目全非、丢掉个人风格
- 不要每句话都用"首先""其次""再次""最后""综上所述"机械连接
- 不要在每段开头都来一句"在这一部分，主要讲了……"
- 不要把提问和回答写成 Q&A 对话体（除非原始字幕明确是对话教学）

# 特别提醒

转述时，处理口语特征的建议：

| 原始口语 | 处理方式 |
|---------|---------|
| "懂了吗？""对吧？""是不是？" | 删除 |
| "举个例子""我跟你讲" | 删除过渡，但**保留后面的例子** |
| "重点是""关键在于""记住啊" | 强化为段落中的强调句，必要时加粗 |
| "这个、这个、那个、那个" | 删除，直接接后续内容 |
| "就是说、就是、其实" | 删除 |
| 自嘲、玩笑、生活化类比 | **必须保留**，这是课堂的灵魂 |

# 工作流程

收到字幕后，请按以下顺序处理：

1. **通读一遍**，识别出核心议题有几个，议题之间的逻辑顺序是什么
2. **逐议题处理**，把该议题下所有零碎的字幕句子合并、改写、补全
3. **串联议题**，用自然的过渡句衔接，保留讲课的节奏
4. **通读检查**，确认：没有"主讲人/他说"堆砌 / 没有信息丢失 / 没有添加原字幕没有的内容 / 读起来是否通顺

现在，请基于以上规则，开始处理我提供的字幕内容。

2. **决定每张 picture N 的去留**：
   - 内容对得上 + 时间点对 → **保留**（needs_extract=false，source_frame_path 写成 markdown 里出现过的那个 `path`，srt_id 取最近的段号）
   - 时间点差 ±2 秒 → **调整 timestamp**（source_frame_path 仍指向同一张图，timestamp 用更准的秒数）
   - 内容真不行（黑屏、过渡帧、模糊）→ **重截**（needs_extract=true，source_frame_path=null，给新 timestamp）
   - 截屏多余或质量太差 → **删除**（needs_extract=false，source_frame_path=null，从列表里去掉这张图）

3. **新增截图**（可选）：启发式漏了老师提到的关键图（PPT / 代码 / 表格 / 屏幕），needs_extract=true + 新 timestamp。

## 输入

<SRT_MD>
{chunk_text}
</SRT_MD> 

## 输出 下述json格式 不要有任何多余的解释

{{
  "blog_markdown": "## 标题\n\n[博客式段落...]",
  "selected_timestamps": [
    {{"srt_id": <int>, "timestamp": <float seconds>, "needs_extract": <bool>, "source_frame_path": "<path|null>", "reason": "<string>"}},
    ...
  ]
}}

约束：
- selected_timestamps 数量 ≤ {max_images}
- needs_extract=true 时 source_frame_path 必须是 null
- needs_extract=false 时 source_frame_path 必须是输入 SRT_MD 里 `![](...)` 出现过的图片路径
- timestamp 允许相对启发式帧调整 ±2 秒
- 不要输出 markdown 之外的解释文字



## 真实例子

输入 SRT_MD ：
<SRT_MD>
"text": "00:00:00,000 --> 00:00:22,500\n宽呢224..."}
"text": "![picture 1](.../frame_00h00m00s000ms_interval_001.jpg)\n\n"
"image_url": {"url": "data:image/jpeg;base64,<...>"
"text": "00:00:22,500 --> 00:00:27,000\n我们在卷积的时候..."
</SRT_MD>

期望输出 JSON：

```json
{{
  "blog_markdown": "## 图像数据的基本构成与卷积操作\n\n图像本质上由 RGB 三通道构成，每通道对应一个二维矩阵……（合并所有段的博客叙述）",
  "selected_timestamps": [
    {{"srt_id": 1, "timestamp": 0.0, "needs_extract": false, "source_frame_path": "src/frame_00h00m00s000ms_interval_001.jpg", "reason": "内容匹配，时间点准确"}},
    {{"srt_id": 3, "timestamp": 362.7, "needs_extract": false, "source_frame_path": "src/frame_00h06m02s700ms_scene_001.jpg", "reason": "内容匹配，时间点准确"}}
  ]
}}
```

注意上面的例子覆盖了两种状态：
- **保留**（needs_extract=false，source_frame_path 引用 SRT_MD 里出现过的图片路径）
- 4 态里的另 3 种（重截 / 删除 / 幻觉路径）下面给一个完整范例：

```json
{{
  "blog_markdown": "## ...",
  "selected_timestamps": [
    {{"srt_id": 1, "timestamp": 5.2, "needs_extract": false, "source_frame_path": "src/frame_..._interval_001.jpg", "reason": "保留：图片清晰，跟段 1 内容匹配"}},
    {{"srt_id": 2, "timestamp": 6.5, "needs_extract": true, "source_frame_path": null, "reason": "重截：原图是过渡帧，更准的时间点是 6.5s"}},
    {{"srt_id": 2, "timestamp": 7.0, "needs_extract": false, "source_frame_path": null, "reason": "删除：跟段 2 内容不相关，是过渡帧"}}
  ]
}}
```
"""


@dataclass
class SelectedTimestamp:
    srt_id: int
    timestamp: float
    needs_extract: bool
    source_frame_path: str | None
    reason: str


@dataclass
class VisionStage1Output:
    blog_markdown: str
    selected_timestamps: list[SelectedTimestamp]


def _build_srt_md_segments(
    segments: Iterable, frames: list[CandidateFrame]
) -> list[dict]:
    """Build a list of interleaved text/image segments for Stage1's
    vision call.

    Each frame is attached to the subtitle segment whose midpoint is
    closest in time, then rendered as ``{type: text, text: ...}`` /
    ``{type: image, path: ...}`` segments in document order. The
    resulting segment list, when sent via
    :func:`call_llm_async_interleaved`, lets the model pair each
    ``![picture N](path)`` markdown reference with the N-th image in
    the multimodal content array — same trick Anthropic/OpenAI vision
    docs recommend.

    Returns segments like::

        [
          {"type": "text", "text": "1. 老师讲到卷积层..."},
          {"type": "image", "path": "src/frame_..._scene_001.jpg"},
          {"type": "image", "path": "src/frame_..._interval_013.jpg"},
          {"type": "text", "text": "2. 接下来看 padding..."},
          ...
        ]
    """
    # Materialize segments into (index, text, start_sec, end_sec).
    seg_rows: list[tuple[int, str, float, float]] = []
    for i, seg in enumerate(segments, start=1):
        text = getattr(seg, "text", "") or ""
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        seg_rows.append((i, text, start, end))

    # Bucket frames by their nearest segment index (preserving input order).
    attached: dict[int, list[CandidateFrame]] = {i: [] for i, _, _, _ in seg_rows}
    for f in frames:
        if not seg_rows:
            attached.setdefault(0, []).append(f)
            continue
        nearest_i = min(
            seg_rows,
            key=lambda row: abs((row[2] + row[3]) / 2.0 - f.timestamp_sec),
        )[0]
        attached[nearest_i].append(f)

    # Build interleaved segments. The markdown `![picture N](path)`
    # marker is sent BEFORE the image, so the model reads "this is where
    # picture N will appear" and then receives the N-th image right
    # after — guaranteeing the order-based pairing between marker and
    # image is unambiguous.
    out: list[dict] = []
    pic_counter = 0
    for i, text, start, end in seg_rows:
        # SRT-style timestamp header + the segment text + a blank line,
        # matching the user's preferred markdown layout.
        ts_header = _format_srt_timestamp(start, end)
        out.append({"type": "text", "text": f"{ts_header}\n{text}\n"})
        for f in attached.get(i, []):
            pic_counter += 1
            # Marker first, image second.
            out.append(
                {
                    "type": "text",
                    "text": f"![picture {pic_counter}]({f.path})\n\n",
                }
            )
            out.append({"type": "image", "path": f.path})
    # Any frames we couldn't attach (no segments) get tacked on the end.
    for f in attached.get(0, []):
        pic_counter += 1
        out.append({"type": "text", "text": f"![picture {pic_counter}]({f.path})\n\n"})
        out.append({"type": "image", "path": f.path})
    return out


def _format_srt_timestamp(start_sec: float, end_sec: float) -> str:
    """Format a timestamp range in SRT style: ``HH:MM:SS,mmm``.

    Mirrors the format used by the cleaned subtitle.srt file so the
    Stage1 prompt reads like real SRT content.
    """
    def _fmt(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec - h * 3600 - m * 60
        s_int = int(s)
        ms = int(round((s - s_int) * 1000))
        return f"{h:02d}:{m:02d}:{s_int:02d},{ms:03d}"
    return f"{_fmt(start_sec)} --> {_fmt(end_sec)}"


def _format_picture_index(frames: list[CandidateFrame]) -> str:
    """Build a compact `{chunk_text}` placeholder body for the prompt
    template — a numbered list of every picture the model will see.

    The actual SRT_MD content (with each picture's `![](path)` reference
    sitting right next to its image in the content array) lives in the
    interleaved body segments. This index just gives the model a
    human-readable manifest at the top of the prompt.
    """
    if not frames:
        return "（无候选帧）"
    lines = ["## 候选帧清单"]
    for i, f in enumerate(frames, start=1):
        lines.append(f"- picture {i}: `{f.path}` @ {f.timestamp_sec:.1f}s")
    return "\n".join(lines)


def _parse_stage1(raw: str, frames: list[CandidateFrame]) -> VisionStage1Output | None:
    """Parse LLM response into VisionStage1Output, or None on failure."""
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    candidate = fenced.group(1) if fenced else raw
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    blog = data.get("blog_markdown")
    selected = data.get("selected_timestamps")
    if not isinstance(blog, str) or not isinstance(selected, list):
        return None

    frame_paths = {f.path for f in frames}
    parsed: list[SelectedTimestamp] = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        try:
            srt_id = int(item["srt_id"])
            timestamp = float(item["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        needs_extract = bool(item.get("needs_extract", False))
        src = item.get("source_frame_path")
        reason = str(item.get("reason", ""))

        # Four valid output states per the prompt:
        #   needs_extract=true,  src=null          → 重截（ffmpeg 新截一张）
        #   needs_extract=false, src=<known path>  → 保留（用现有启发式帧）
        #   needs_extract=false, src=null          → 删除（不输出，不进 Stage2 / MD）
        #   needs_extract=false, src=<unknown>     → 删除（幻觉路径，丢掉比误截更安全）
        if needs_extract:
            src = None
            parsed.append(
                SelectedTimestamp(
                    srt_id=srt_id,
                    timestamp=timestamp,
                    needs_extract=True,
                    source_frame_path=None,
                    reason=reason,
                )
            )
        elif src in frame_paths:
            parsed.append(
                SelectedTimestamp(
                    srt_id=srt_id,
                    timestamp=timestamp,
                    needs_extract=False,
                    source_frame_path=src,
                    reason=reason,
                )
            )
        else:
            # needs_extract=False with null or unknown path → 删除
            get_reporter().record_fallback(
                "vision_stage1.frame_dropped",
                f"srt_id={srt_id} 的启发式帧被删除（path={src!r}）",
            )
            continue

    return VisionStage1Output(blog_markdown=blog, selected_timestamps=parsed)


def _fallback_output(chunk: SRTChunk, frames: list[CandidateFrame]) -> VisionStage1Output:
    """Conservative fallback when Stage1 keeps failing.

    blog_markdown = concatenated chunk text. selected_timestamps = every
    heuristic frame reused as-is (``needs_extract=False``).
    """
    blog = "\n\n".join(
        getattr(seg, "text", "") for seg in chunk.segments if getattr(seg, "text", "")
    )
    selected = [
        SelectedTimestamp(
            srt_id=i + 1,
            timestamp=f.timestamp_sec,
            needs_extract=False,
            source_frame_path=f.path,
            reason="heuristic fallback",
        )
        for i, f in enumerate(frames)
    ]
    return VisionStage1Output(blog_markdown=blog, selected_timestamps=selected)


class VisionStage1:
    """Call the vision model with text + heuristic frames for one chunk."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        max_images: int | None = None,
        max_retries: int = 2,
        timeout: int = 600,
    ):
        self.config = config or load_vision_config()
        self.max_images = (
            max_images
            if max_images is not None
            else int(config_get("chunking.max_images_per_chunk", 50))
        )
        self.max_retries = max_retries
        self.timeout = timeout

    async def process(
        self,
        chunk: SRTChunk,
        frames_in_chunk: list[CandidateFrame],
    ) -> VisionStage1Output:
        """Run Stage1 for one chunk. Returns a fallback on final failure."""
        # Build interleaved text/image segments so the model sees each
        # `![picture N](path)` reference adjacent to its image.
        body_segments = _build_srt_md_segments(chunk.segments, frames_in_chunk)

        # The instructions + task framing go in a leading text segment,
        # with the SRT_MD content (interleaved text/image) appended.
        # We still need to fill {chunk_text} in the template — use a
        # compact reference to the picture index inside the leading
        # instructions rather than duplicating the body.
        srt_md_index = _format_picture_index(frames_in_chunk)
        instruction_text = STAGE1_PROMPT.format(
            chunk_text=srt_md_index,
            max_images=self.max_images,
        )
        all_segments = [{"type": "text", "text": instruction_text}, *body_segments]

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_llm_async_interleaved(
                    all_segments,
                    self.config,
                    max_tokens=8192,
                    timeout=self.timeout,
                )
                parsed = _parse_stage1(response, frames_in_chunk)
                if parsed is None:
                    raise ValueError("Stage1 response did not match schema")
                # Enforce the ≤ max_images cap — keep the first N the model
                # ranked highest (it has no explicit rank field, so just
                # take the leading slice; downstream keeps decisions sane).
                if len(parsed.selected_timestamps) > self.max_images:
                    parsed.selected_timestamps = parsed.selected_timestamps[: self.max_images]
                return parsed
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    await asyncio.sleep(2 ** attempt)

        get_reporter().record_fallback(
            "vision_stage1.fallback",
            f"chunk {chunk.index} Stage1 失败（{last_error}），已降级到启发式保留",
        )
        return _fallback_output(chunk, frames_in_chunk)


def extract_new_frames(
    selected: list[SelectedTimestamp],
    video_path: str,
    chunk_index: int,
    output_dir: Path,
) -> list[CandidateFrame]:
    """FFmpeg-capture every ``needs_extract=True`` selection.

    Output paths: ``<output_dir>/chunk_<chunk_index>/extra_frame_<j>.jpg``.
    Skipped timestamps are not fatal — we keep going.
    """
    from framelearn.pipeline.ffmpeg_helper import FFmpegHelper

    output_dir = Path(output_dir)
    chunk_dir = output_dir / f"chunk_{chunk_index}"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    new_frames: list[CandidateFrame] = []
    for j, sel in enumerate(selected):
        if not sel.needs_extract:
            continue
        target = chunk_dir / f"extra_frame_{j:03d}.jpg"
        ok = FFmpegHelper.capture_single_frame(
            video_path, sel.timestamp, str(target)
        )
        if ok:
            new_frames.append(
                CandidateFrame(
                    path=str(target),
                    timestamp_sec=sel.timestamp,
                    source="stage1",
                )
            )
        else:
            get_reporter().record_skipped_frame(
                "vision_stage1.extract_new_frames",
                f"无法在 {sel.timestamp}s 截帧",
                detail={"chunk": chunk_index, "timestamp": sel.timestamp},
            )
    return new_frames