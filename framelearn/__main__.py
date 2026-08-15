"""CLI entry point for FrameLearn."""

import os
import shutil
import sys

from framelearn.command_parser import CommandParser
from framelearn.errors import FrameLearnError
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


def _parse_flags(args: list[str]) -> tuple[str, dict]:
    """Extract --flag value pairs from args, return (remaining_input, flags).

    Supported flags:
        --subtitle <path>   Path to existing subtitle file (skip ASR)
        --debug             Print full LLM prompts/responses for the parser
    """
    flags: dict = {}
    remaining: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--subtitle" and i + 1 < len(args):
            flags["subtitle"] = args[i + 1]
            i += 2
        elif args[i] == "--debug":
            flags["debug"] = True
            i += 1
        else:
            remaining.append(args[i])
            i += 1
    return " ".join(remaining), flags


def _run_once(
    user_input: str, parser: CommandParser, router: CommandRouter, flags: dict = {}
) -> int:
    """Parse and execute a single input. Returns exit code.

    Any domain failure (usage error via ValueError, or a business/feature
    failure via FrameLearnError) is mapped to a nonzero exit code so shell
    scripts, batch jobs, and CI can reliably detect failure.
    """
    try:
        command = parser.parse(user_input)
        if flags.get("debug") or not parser._is_traditional_command(user_input):
            print(f"[→ {command}]")
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    except Exception as e:
        print(f"❌ 解析失败：{e}")
        return 1

    try:
        return router.execute(command, flags=flags)
    except ValueError as e:
        print(f"❌ {e}")
        return 1
    except FrameLearnError as e:
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
                user_input = input("> ").strip()
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

    if len(sys.argv) < 2:
        _repl(workspace)
        return

    _check_codex()

    # Extract --flag options before passing to parser
    user_input, flags = _parse_flags(sys.argv[1:])

    parser = CommandParser(debug=bool(flags.get("debug")))
    router = CommandRouter(workspace=workspace)

    try:
        code = _run_once(user_input, parser, router, flags=flags)
        sys.exit(code)
    finally:
        router.close()


if __name__ == "__main__":
    main()
