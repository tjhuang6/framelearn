"""Document generator using Codex app-server or Vision API."""

from pathlib import Path
from typing import Literal

from framelearn.config import get as config_get


# ── 笔记版 prompt ─────────────────────────────────────────────────
_NOTES_PROMPT = """你是一个编程课堂笔记整理助手。根据视频字幕，生成结构化的课堂笔记。

# 字幕原文

<subtitle>
{subtitle}
</subtitle>

# 关键帧列表

<frames>
{frames_description}
</frames>

# 输出要求

- 按视频内容的自然段落划分章节（## 标题）
- 每个章节列出 3-5 条核心知识点（bullet points）
- 提取所有代码片段，标注语言
- 引用关键帧（格式：![说明](src/frame_001.jpg)）
- 语言简练，去除口水词，保留技术信息
- 输出 Markdown 格式

"""

# ── 教材版 prompt ─────────────────────────────────────────────────
_TEXTBOOK_PROMPT = """你是一个技术图书编辑，负责将编程课视频整理成正式教材。

字幕是老师的原话，你需要：
1. 去掉口水词（"那么"、"就是说"、"大家注意"、"咱们"、"啊"、"嗯"等）
2. **保留老师的讲解逻辑和节奏**——老师先铺垫什么、后解释什么、用什么例子引入，这个顺序要保留
3. 把口语句式改成书面语，但不要改变意思，不要做知识压缩
4. 每个概念要有引入、解释、示例、小结，像教材章节一样完整
5. 代码要完整保留，加注释说明每行的作用
6. 在合适位置引用关键帧（格式：![说明文字](src/frame_001.jpg)）

# 字幕原文

<subtitle>
{subtitle}
</subtitle>

# 关键帧列表

<frames>
{frames_description}
</frames>

# 输出要求

- 用 ## 划分章节，章节标题反映该段的核心内容
- 正文用流畅的书面语段落，不用 bullet points 列知识点
- 代码块完整，有注释
- 关键帧在相关段落后引用
- 输出 Markdown 格式

"""

# ── 顺序讲稿版 prompt ─────────────────────────────────────────────────
_VISUAL_SCRIPT_PROMPT = """你是视频字幕转图文讲稿助手。

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
{frames_description}
</frames>

# 输出要求

1. **按字幕时间顺序逐段转写**
   - 每段对应讲解的一个自然段落
   - 段落结构：老师说什么 → 你写什么
   - 不要把"先讲 A 再讲 B"重排成"B 的知识点、A 的知识点"

2. **插入关键帧**
   - 在讲到对应时间时插入：`![](src/frame_00h03m45s.jpg)`
   - 如果字幕提到"看这张图"、"如图所示"，立即在此处插图
   - 如果附近没有关键帧，可以说明"（讲师展示了画面，但未被抽帧）"

3. **口语书面化**
   - ❌ "那么这个呢就是说我们这个FastAPI啊"
   - ✅ "FastAPI 的路由机制如下"
   - 保留讲解的逻辑顺序，去除冗余口头禅

4. **代码片段**
   - 提取代码，标注语言：```python
   - 如果字幕有逐行讲解，保留讲解内容

5. **格式**
   - 用 `##` 分段（按内容命名，如 `## FastAPI 路由基础`）
   - 不要用 bullet points 列知识点
   - 正文是连贯的段落叙述

6. **图片说明**
   - 每张图后加一句话说明图片内容
   - 例：`![](src/frame_00h03m45s.jpg)`
   - *图为 FastAPI 路由代码示例*

直接输出 Markdown，不要解释。
"""


DocMode = Literal["notes", "textbook", "visual_script"]


class DocumentGenerator:
    """Generate markdown tutorial from keyframes + subtitle."""

    def __init__(self):
        self.vision_mode = config_get("runtime.vision_mode", "appserver")
        self.text_mode = config_get("runtime.text_mode", "appserver")

    def generate(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        video_title: str,
        mode: DocMode = "textbook",
    ) -> str:
        """Generate markdown tutorial.

        Args:
            keyframes: List of (frame_path, timestamp_seconds) tuples
            subtitle: Cleaned subtitle text
            video_title: Title of the video
            mode: "notes" (bullet-point summary) or "textbook" (prose tutorial)

        Returns:
            Generated markdown content
        """
        if self.vision_mode == "appserver":
            return self._generate_via_appserver(keyframes, subtitle, video_title, mode)
        else:
            return self._generate_via_api(keyframes, subtitle, video_title, mode)

    def _build_prompt(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        mode: DocMode,
    ) -> str:
        def format_timestamp(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            if h > 0:
                return f"{h:02d}:{m:02d}:{s:02d}"
            else:
                return f"{m:02d}:{s:02d}"

        frames_desc = "\n".join(
            f"关键帧 {i+1} ({format_timestamp(ts)}): {frame.name}"
            for i, (frame, ts) in enumerate(keyframes[:20])
        )
        template = _TEXTBOOK_PROMPT if mode == "textbook" else _NOTES_PROMPT
        return template.format(
            subtitle=subtitle[:12000],
            frames_description=frames_desc,
        )

    def _generate_via_appserver(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        video_title: str,
        mode: DocMode,
    ) -> str:
        """Generate via codex app-server."""
        from framelearn.app_server.session import AppServerSession

        prompt = self._build_prompt(keyframes, subtitle, mode)

        session = AppServerSession(workspace=".")
        result = session.run_turn(prompt)
        session.close()

        if result.error:
            raise RuntimeError(f"Document generation failed: {result.error}")

        # Codex writes the content to a file and returns a summary in final_text.
        # Prefer reading the actual written .md file over the summary message.
        for path in result.written_files:
            if path.endswith(".md"):
                try:
                    return Path(path).read_text(encoding="utf-8")
                except Exception:
                    continue

        # Fallback: return whatever final_text we got
        return result.final_text or ""

    def _generate_via_api(
        self,
        keyframes: list[tuple[Path, float]],
        subtitle: str,
        video_title: str,
        mode: DocMode,
    ) -> str:
        """Generate via provider_adapter (Vision API)."""
        import base64
        from framelearn.provider_adapter import ProviderAdapter

        # Build text prompt
        text_prompt = self._build_prompt(keyframes, subtitle, mode)

        # Encode keyframes to base64 (limit to first 20)
        content = [{"type": "text", "text": text_prompt}]

        for frame_path, timestamp in keyframes[:20]:
            if not frame_path.exists():
                continue
            try:
                with open(frame_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}
                })
            except Exception as e:
                print(f"⚠️  无法读取关键帧 {frame_path.name}：{e}")

        # Call Vision API
        adapter = ProviderAdapter()
        provider = config_get("runtime.vision_provider", "deepseek")
        model = config_get("runtime.vision_model", "deepseek-reasoner")

        try:
            response = adapter.chat(
                messages=[{"role": "user", "content": content}],
                provider=provider,
                model=model,
            )
            return response
        except Exception as e:
            raise RuntimeError(f"Vision API 调用失败：{e}")
