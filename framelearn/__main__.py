"""CLI entry point for FrameLearn."""

import os
import sys

from framelearn.command_parser import CommandParser
from framelearn.router import CommandRouter


def main():
    """Main entry point for FrameLearn CLI."""
    # 1. Parse command line arguments
    if len(sys.argv) < 2:
        print("用法：framelearn <命令或自然语言描述>")
        print("示例：")
        print('  framelearn "帮我处理这个视频 https://..."')
        print('  framelearn ask "第 3 章讲了什么"')
        print('  framelearn help')
        sys.exit(1)

    user_input = " ".join(sys.argv[1:])

    # 2. Get API key from environment
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ 错误：未找到 ANTHROPIC_API_KEY 环境变量")
        print("请设置：export ANTHROPIC_API_KEY=your_key_here")
        sys.exit(1)

    # 3. Initialize parser and router
    parser = CommandParser(api_key=api_key)
    router = CommandRouter()

    # 4. Parse intent
    try:
        command = parser.parse(user_input)
        if not parser._is_traditional_command(user_input):
            print(f"[解析意图] → {command}")
    except ValueError as e:
        print(f"❌ 错误：{e}")
        print("\n提示：使用传统命令格式：")
        print('  framelearn run "https://..."')
        print('  framelearn run "/path/to/video.mp4"')
        print('  framelearn ask "你的问题"')
        sys.exit(1)
    except Exception as e:
        print(f"❌ 解析失败：{e}")
        print("请检查网络连接或稍后重试")
        sys.exit(1)

    # 5. Execute command
    try:
        router.execute(command)
    except ValueError as e:
        print(f"❌ 执行失败：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 未知错误：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
