"""Document generator using Codex app-server or Vision API."""

from pathlib import Path

from framelearn.config import get as config_get


_PROMPT_TEMPLATE = """你是一个编程教程整理助手。根据视频关键帧和字幕，生成结构化的 Markdown 教材。

# 字幕文字

<subtitle>
{subtitle}
</subtitle>

# 关键帧

<frames>
{frames_description}
</frames>

# 要求

1. 提取章节结构（使用 ## 标题）
2. 每个章节总结核心要点（3-5 条）
3. 引用关键帧（格式：![说明文字](src/frame_001.jpg)）
4. 提取代码片段并标注语言（```python 等）
5. 保持专业但易懂的语言风格

输出 Markdown 格式。
"""


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
    ) -> str:
        """Generate markdown tutorial.

        Args:
            keyframes: List of paths to keyframe images
            subtitle: Cleaned subtitle text
            video_title: Title of the video

        Returns:
            Generated markdown content
        """
        if self.vision_mode == "appserver":
            return self._generate_via_appserver(keyframes, subtitle, video_title)
        else:
            return self._generate_via_api(keyframes, subtitle, video_title)

    def _generate_via_appserver(
        self,
        keyframes: list[Path],
        subtitle: str,
        video_title: str,
    ) -> str:
        """Generate via codex app-server."""
        from framelearn.app_server.session import AppServerSession

        # Prepare frames description
        frames_desc = "\n".join([
            f"关键帧 {i+1}: {frame.name}"
            for i, frame in enumerate(keyframes[:20])  # Limit to 20 frames
        ])

        prompt = _PROMPT_TEMPLATE.format(
            subtitle=subtitle[:10000],  # Limit subtitle length
            frames_description=frames_desc,
        )

        # TODO: Support sending actual images via localImage
        # For now, just send text prompt
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
    ) -> str:
        """Generate via provider_adapter (Vision API)."""
        # TODO: Implement in Task #28
        raise NotImplementedError("API mode not yet implemented")
