"""Natural language command parser for FrameLearn."""

from framelearn.provider_adapter import call_text_llm


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
   - 背后是 Codex AI Agent，能处理任意编程和学习相关的任务
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

        Args:
            user_input: Raw user input (natural language or traditional command)

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
        """Check if input is already a traditional command format."""
        first_word = text.strip().split()[0] if text.strip() else ""
        return first_word in ["run", "ask", "summarize", "help"]

    def _parse_with_llm(self, text: str) -> str:
        """Parse natural language input using configured text LLM provider."""
        prompt = f"{SYSTEM_PROMPT}\n\n输入：{text}\n输出："
        return call_text_llm(prompt, max_tokens=100).strip()
