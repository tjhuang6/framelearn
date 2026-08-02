"""Command router for dispatching parsed commands to appropriate modules."""

import os
from typing import Optional

from framelearn.app_server.runtime import RuntimeAdapter
from framelearn.config import get as config_get


HELP_TEXT = """
FrameLearn - AI Agent for converting programming tutorial videos to text tutorials

用法：
  framelearn <命令或自然语言描述>

命令格式：
  run <URL或路径>      处理视频并生成教材
  ask <问题>           询问教材内容
  summarize           总结学习过程
  help                显示此帮助信息

示例：
  # 自然语言（推荐）
  framelearn "帮我处理这个视频 https://bilibili.com/video/BV1xx..."
  framelearn "处理本地视频 /path/to/video.mp4"
  framelearn "第 3 章讲了什么"
  framelearn "总结一下我学到的"

  # 传统命令格式
  framelearn run "https://youtube.com/watch?v=xxx"
  framelearn run "/path/to/video.mp4"
  framelearn ask "为什么要用虚拟环境"
  framelearn summarize

支持的视频格式：.mp4, .mkv, .avi, .mov, .flv, .wmv, .webm
支持的视频来源：YouTube, Bilibili, 本地文件
"""


class CommandRouter:
    """Route parsed commands to appropriate FrameLearn modules."""

    def __init__(self, workspace: Optional[str] = None):
        self._workspace = workspace or os.getcwd()
        self._runtime: Optional[RuntimeAdapter] = None

    def execute(self, command: str, flags: dict = {}):
        """
        Execute a parsed command.

        Raises:
            ValueError: If command format is invalid or parameters are missing
        """
        parts = command.split(maxsplit=1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "run":
            self._run_pipeline(args, flags=flags)
        elif cmd == "ask":
            self._ask_question(args)
        elif cmd == "summarize":
            self._summarize_learning()
        elif cmd == "help":
            self._show_help()
        else:
            raise ValueError(f"未知命令：{cmd}")

    def close(self):
        if self._runtime:
            self._runtime.close()
            self._runtime = None

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _run_pipeline(self, source: str):
        if not source:
            raise ValueError("缺少视频 URL 或文件路径")

        if source.startswith("http"):
            if not self._is_valid_video_url(source):
                raise ValueError("无效的视频链接，仅支持 YouTube 和 Bilibili")
            print("❌ 在线视频下载功能尚未实现")
            print("提示：请先手动下载视频，然后使用本地文件路径")
            return
        else:
            if not os.path.isfile(source):
                raise ValueError(f"文件不存在：{source}")
            if not self._is_video_file(source):
                raise ValueError("不支持的文件格式，仅支持常见视频格式")

            # Process local video file
            from framelearn.pipeline import VideoPipeline

            pipeline = VideoPipeline(source)
            result = pipeline.run()

            if result.error:
                print(f"❌ {result.error}")
            else:
                print(f"\n📂 输出目录：{result.output_dir}")
                print(f"📄 教材文件：{result.markdown_path}")
                print(f"🖼️  关键帧数：{len(result.keyframes)}")

    def _ask_question(self, question: str):
        if not question:
            raise ValueError("缺少问题内容")

        text_mode = config_get("runtime.text_mode", "appserver")

        if text_mode == "api":
            self._ask_via_api(question)
        else:
            self._ask_via_appserver(question)

    def _ask_via_appserver(self, question: str):
        """Ask via codex app-server (default)."""
        runtime = self._get_runtime()

        def _ui(event: dict):
            method = event.get("method", "")
            params = event.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = params.get("delta", "")
                print(delta, end="", flush=True)

        result = runtime.run_turn(question, ui_callback=_ui)
        print()  # newline after streaming output

        if result.error:
            print(f"❌ {result.error}")
        elif result.final_text:
            # final_text already streamed via ui_callback; nothing more to print
            pass

    def _ask_via_api(self, question: str):
        """Ask via provider_adapter (direct API call)."""
        from framelearn.provider_adapter import call_text_llm
        print("使用 API 模式...")
        answer = call_text_llm(question, max_tokens=2000)
        print(answer)

    def _summarize_learning(self):
        print("请运行：/summarize-learning")
        print("提示：这是一个 Claude Code skill，需在 Claude Code 环境中使用")

    def _show_help(self):
        print(HELP_TEXT)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_runtime(self) -> RuntimeAdapter:
        if self._runtime is None:
            self._runtime = RuntimeAdapter(workspace=self._workspace)
        return self._runtime

    def _is_valid_video_url(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url or "bilibili.com" in url

    def _is_video_file(self, path: str) -> bool:
        video_exts = ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm']
        return any(path.lower().endswith(ext) for ext in video_exts)
