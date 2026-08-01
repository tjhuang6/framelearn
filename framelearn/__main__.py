"""CLI entry point for FrameLearn."""

import os
import shutil
import sys

from framelearn.command_parser import CommandParser
from framelearn.router import CommandRouter


_BANNER = """
FrameLearn — AI 编程教程学习助手
输入你的问题或需求，直接回车发送。
输入 help 查看命令，输入 exit 或按 Ctrl+C 退出。
"""


def _check_codex():
    if not shutil.which("codex"):
        print("⚠️  警告：未找到 codex CLI，ask 命令将无法使用")
        print("   安装：npm install -g @openai/codex")
        print()


def _run_once(user_input: str, parser: CommandParser, router: CommandRouter):
    """Parse and execute a single input. Returns exit code."""
    try:
        command = parser.parse(user_input)
        if not parser._is_traditional_command(user_input):
            print(f"[→ {command}]")
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    except Exception as e:
        print(f"❌ 解析失败：{e}")
        return 1

    try:
        router.execute(command)
        return 0
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    except Exception as e:
        print(f"❌ {e}")
        return 1


def _repl(workspace: str):
    """Interactive REPL mode."""
    print(_BANNER)
    _check_codex()

    parser = CommandParser()
    router = CommandRouter(workspace=workspace)

    try:
        while True:
            try:
                user_input = input("你 > ").strip()
            except EOFError:
                print()
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q", "bye", "退出"):
                print("再见")
                break

            _run_once(user_input, parser, router)
            print()
    except KeyboardInterrupt:
        print("\n再见")
    finally:
        router.close()


def main():
    """Main entry point for FrameLearn CLI."""
    workspace = os.getcwd()

    # No arguments → enter interactive REPL
    if len(sys.argv) < 2:
        _repl(workspace)
        return

    _check_codex()

    user_input = " ".join(sys.argv[1:])
    parser = CommandParser()
    router = CommandRouter(workspace=workspace)

    try:
        code = _run_once(user_input, parser, router)
        sys.exit(code)
    finally:
        router.close()


if __name__ == "__main__":
    main()
