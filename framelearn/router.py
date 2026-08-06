"""Command router for dispatching parsed commands to appropriate modules."""

import os
from typing import Optional

from framelearn.config import get as config_get
from framelearn.errors import FeatureNotAvailableError, PipelineExecutionError


HELP_TEXT = """
FrameLearn - AI Agent for converting programming tutorial videos to text tutorials

用法：
  framelearn <命令或自然语言描述>

命令格式：
  run <URL或路径>      处理视频并生成教材
  ask <问题>           询问教材内容
  summarize           总结学习过程
  session <操作>       管理会话数据库
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

  # 会话管理
  framelearn session list              列出所有会话
  framelearn session info              显示数据库统计
  framelearn session delete <id>       删除指定会话
  framelearn session clear             清空所有会话
  framelearn session export <id>       导出会话为 JSON

支持的视频格式：.mp4, .mkv, .avi, .mov, .flv, .wmv, .webm
支持的视频来源：YouTube, Bilibili, 本地文件

数据隐私说明：docs/privacy-and-data-lifecycle.md
"""


class CommandRouter:
    """Route parsed commands to appropriate FrameLearn modules."""

    def __init__(self, workspace: Optional[str] = None):
        self._workspace = workspace or os.getcwd()

    def execute(self, command: str, flags: dict = {}) -> int:
        """
        Execute a parsed command.

        Returns:
            int: ``0`` on success. Handlers never return a nonzero code
                themselves — a failure that should map to a nonzero exit
                code is always signaled by raising (ValueError for usage
                errors, a FrameLearnError subclass for business failures
                or unimplemented features). The explicit return keeps the
                "succeeded" signal unambiguous for callers.

        Raises:
            ValueError: If command format is invalid or parameters are missing.
            FrameLearnError: If the command was well-formed but could not
                be completed (e.g. pipeline failure, unimplemented feature).
        """
        parts = command.split(maxsplit=1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "run":
            return self._run_pipeline(args, flags=flags)
        elif cmd == "ask":
            return self._ask_question(args)
        elif cmd == "summarize":
            return self._summarize_learning()
        elif cmd == "session":
            return self._manage_session(args)
        elif cmd == "help":
            return self._show_help()
        else:
            raise ValueError(f"未知命令：{cmd}")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _run_pipeline(self, source: str, flags: dict = {}) -> int:
        if not source:
            raise ValueError("缺少视频 URL 或文件路径")

        subtitle_path = flags.get("subtitle")

        if source.startswith("http"):
            if not self._is_valid_video_url(source):
                raise ValueError("无效的视频链接，仅支持 YouTube 和 Bilibili")
            print("提示：请先手动下载视频，然后使用本地文件路径")
            raise FeatureNotAvailableError("在线视频下载功能尚未实现")
        else:
            if not os.path.isfile(source):
                raise ValueError(f"文件不存在：{source}")
            if not self._is_video_file(source):
                raise ValueError("不支持的文件格式，仅支持常见视频格式")

            if subtitle_path and not os.path.isfile(subtitle_path):
                raise ValueError(f"字幕文件不存在：{subtitle_path}")

            from framelearn.pipeline import VideoPipeline

            pipeline = VideoPipeline(source, subtitle_path=subtitle_path)
            result = pipeline.run()

            if result.error:
                raise PipelineExecutionError(result.error)

            print(f"\n📂 输出目录：{result.output_dir}")
            print(f"📄 教材文件：{result.markdown_path}")
            print(f"🖼️  关键帧数：{len(result.keyframes)}")
            return 0

    def _ask_question(self, question: str) -> int:
        if not question:
            raise ValueError("缺少问题内容")

        return self._ask_via_api(question)

    def _ask_via_api(self, question: str) -> int:
        """Ask via provider_adapter (direct API call)."""
        from framelearn.provider_adapter import call_text_llm
        print("使用 API 模式...")
        answer = call_text_llm(question, max_tokens=2000)
        print(answer)
        return 0

    def _summarize_learning(self) -> int:
        print("请运行：/summarize-learning")
        print("提示：这是一个 Claude Code skill，需在 Claude Code 环境中使用")
        return 0

    def _show_help(self) -> int:
        print(HELP_TEXT)
        return 0

    def _manage_session(self, args: str) -> int:
        """Manage session database."""
        from framelearn.session_manager import (
            list_sessions, show_info, delete_session, clear_all_sessions, export_session
        )
        
        parts = args.split(maxsplit=1)
        if not parts:
            print("❌ 缺少操作参数，可选：list, info, delete, clear, export")
            return 0
        
        operation = parts[0]
        operand = parts[1] if len(parts) > 1 else ""
        
        if operation == "list":
            list_sessions()
        elif operation == "info":
            show_info()
        elif operation == "delete":
            if not operand:
                print("❌ 缺少会话 ID")
                return 0
            delete_session(operand)
        elif operation == "clear":
            confirm = operand.lower() == "--confirm"
            clear_all_sessions(confirm=confirm)
        elif operation == "export":
            if not operand:
                print("❌ 缺少会话 ID")
                return 0
            session_parts = operand.split(maxsplit=1)
            session_id = session_parts[0]
            output_path = session_parts[1] if len(session_parts) > 1 else None
            export_session(session_id, output_path)
        else:
            print(f"❌ 未知操作：{operation}")
            print("可选操作：list, info, delete, clear, export")
        return 0

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
