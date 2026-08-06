"""Natural language command parser for FrameLearn."""

import os

from dotenv import load_dotenv

load_dotenv()


SYSTEM_PROMPT = """
你是 FrameLearn 的命令解析器。
用户会用自然语言描述需求，你需要识别意图并输出标准命令。

支持的命令格式：
1. run <视频URL或本地路径>
   - 在线视频：下载并生成图文教材
   - 本地视频：直接处理本地视频文件
   - URL 示例：run https://bilibili.com/video/BV1xx...
   - 本地示例：run /Users/iwill/Downloads/tutorial.mp4

2. ask <问题或任务>
   - 用于一切非视频处理的需求：提问、写代码、解释概念、查看文件、修复 bug 等
   - 通过 API 调用 LLM，能处理任意编程和学习相关的任务
   - 示例：ask 第 3 章讲了什么
   - 示例：ask 帮我写一个冒泡排序
   - 示例：ask 解释一下什么是虚拟环境
   - 示例：ask 帮我看一下这个项目的结构

3. summarize
   - 总结最近的学习对话，创建独立笔记
   - 无需参数

4. help
   - 显示帮助信息

输出规则：
- 只输出命令，不要解释，不要添加任何额外文字
- ask 后面必须保留用户输入的原始措辞，不要改写、不要缩短
- 如果用户意图是处理视频但没提供 URL 或路径，输出：error: 缺少视频链接或文件路径
- 如果提供的本地路径不存在，输出：error: 文件不存在
- 如果意图完全不明确（例如和编程学习完全无关），输出：error: 无法理解意图，请明确说明需求
- 遇到任何编程、学习、代码、文件、项目相关的问题，都路由到 ask

示例：
输入：帮我把这个视频转成文档 https://bilibili.com/video/BV1xx...
输出：run https://bilibili.com/video/BV1xx...

输入：处理这个本地视频 /Users/iwill/Downloads/tutorial.mp4
输出：run /Users/iwill/Downloads/tutorial.mp4

输入：我想看看第 3 章为什么要用虚拟环境
输出：ask 我想看看第 3 章为什么要用虚拟环境

输入：帮我写一个快速排序
输出：ask 帮我写一个快速排序

输入：解释一下这个项目的结构
输出：ask 解释一下这个项目的结构

输入：帮我修复这个 bug
输出：ask 帮我修复这个 bug

输入：总结一下我刚才学到的知识
输出：summarize

输入：处理这个视频
输出：error: 缺少视频链接或文件路径

输入：帮我做个饭
输出：error: 无法理解意图，请明确说明需求
"""


class CommandParser:
    """Parse natural language input into standard FrameLearn commands."""

    def parse(self, user_input: str) -> str:
        """
        Parse user input into a standard command.

        Returns:
            Standard command string (e.g., "run https://..." or "ask <question>")

        Raises:
            ValueError: If parsing fails or input is invalid
        """
        if self._is_traditional_command(user_input):
            return user_input

        command = self._parse_with_llm(user_input)

        if command.startswith("error:"):
            raise ValueError(command[6:].strip())

        return command

    def _is_traditional_command(self, text: str) -> bool:
        first_word = text.strip().split()[0] if text.strip() else ""
        return first_word in ["run", "ask", "summarize", "help", "session"]

    def _parse_with_llm(self, text: str) -> str:
        """Parse natural language using available LLM backend.

        Priority:
          1. TEXT_PROVIDER + valid TEXT_API_KEY in .env  → use provider_adapter
          2. codex app-server available                  → use codex as parser
          3. Neither available                           → treat input as ask passthrough
        """
        provider = os.getenv("TEXT_PROVIDER", "").strip()
        api_key = os.getenv("TEXT_API_KEY", "").strip()
        key_looks_real = (
            bool(api_key)
            and not api_key.startswith("your_")
            and api_key != "sk-xxx"
            and len(api_key) > 10
        )

        if provider and key_looks_real:
            return self._parse_via_provider(text)

        return self._parse_via_codex(text)

    def _parse_via_provider(self, text: str) -> str:
        from framelearn.provider_adapter import call_text_llm
        prompt = f"{SYSTEM_PROMPT}\n\n输入：{text}\n输出："
        return call_text_llm(prompt, max_tokens=100).strip()

    def _parse_via_codex(self, text: str) -> str:
        """Rule-based intent parser — no LLM needed.

        When no external API key is configured, we use simple rules:
        - Input contains a video URL or video file path → run
        - Input contains "总结" / "summarize" → summarize
        - Everything else → ask (Codex handles it)

        This is intentionally simple. The heavy lifting is done by Codex
        during the ask turn, not during intent classification.
        """
        lower = text.lower().strip()

        # summarize intent
        if any(kw in lower for kw in ("总结", "summarize", "笔记")):
            # Make sure it's not asking about summarizing a video
            if not self._contains_video_source(text):
                return "summarize"

        # video processing intent
        if self._contains_video_source(text):
            source = self._extract_video_source(text)
            if source:
                return f"run {source}"
            return "error: 缺少视频链接或文件路径"

        # explicit video intent but no source
        video_intent_keywords = ("视频", "video", "youtube", "bilibili", "b站", "教程", "转成文档", "生成教材")
        if any(kw in lower for kw in video_intent_keywords):
            return "error: 缺少视频链接或文件路径"

        # Everything else → ask Codex
        return f"ask {text}"

    @staticmethod
    def _contains_video_source(text: str) -> bool:
        import re
        # URL
        if re.search(r"https?://", text):
            return True
        # Local video file path
        video_exts = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm")
        return any(text.lower().endswith(ext) or f"{ext} " in text.lower() for ext in video_exts)

    @staticmethod
    def _extract_video_source(text: str) -> str:
        import re
        # Extract URL
        m = re.search(r"https?://\S+", text)
        if m:
            return m.group(0)
        # Extract local file path (starts with / or ~)
        m = re.search(r"[/~]\S+\.(?:mp4|mkv|avi|mov|flv|wmv|webm)", text, re.IGNORECASE)
        if m:
            return m.group(0)
        return ""
