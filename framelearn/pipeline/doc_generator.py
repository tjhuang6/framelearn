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


DocMode = Literal["notes", "textbook"]


class DocumentGenerator:
    """Generate markdown tutorial from keyframes + subtitle."""

    def __init__(self):
        self.vision_mode = config_get("runtime.vision_mode", "appserver")
        self.text_mode = config_get("runtime.text_mode", "appserver")

    def generate(
        self,
        keyframes: list[Path],
        subtitle: str,
        video_title: str,
        mode: DocMode = "textbook",
    ) -> str:
        """Generate markdown tutorial.

        Args:
            keyframes: List of paths to keyframe images
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
        keyframes: list[Path],
        subtitle: str,
        mode: DocMode,
    ) -> str:
        frames_desc = "\n".join(
            f"关键帧 {i+1}: {frame.name}"
            for i, frame in enumerate(keyframes[:20])
        )
        template = _TEXTBOOK_PROMPT if mode == "textbook" else _NOTES_PROMPT
        return template.format(
            subtitle=subtitle[:12000],
            frames_description=frames_desc,
        )

    def _generate_via_appserver(
        self,
        keyframes: list[Path],
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

        return result.final_text or ""

    def _generate_via_api(
        self,
        keyframes: list[Path],
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

        for frame in keyframes[:20]:
            if not frame.exists():
                continue
            try:
                with open(frame, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}
                })
            except Exception as e:
                print(f"⚠️  无法读取关键帧 {frame.name}：{e}")

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
