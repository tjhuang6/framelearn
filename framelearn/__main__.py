"""CLI entry point for FrameLearn."""

import sys

from framelearn.command_parser import CommandParser
from framelearn.router import CommandRouter


def main():
    """Main entry point for FrameLearn CLI."""
    if len(sys.argv) < 2:
        print("用法：framelearn <命令或自然语言描述>")
        print("示例：")
        print('  framelearn "帮我处理这个视频 https://..."')
        print('  framelearn ask "第 3 章讲了什么"')
        print('  framelearn help')
        sys.exit(1)

    user_input = " ".join(sys.argv[1:])

    parser = CommandParser()
    router = CommandRouter()

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
        print("请检查 .env 文件中的 TEXT_PROVIDER 和 TEXT_API_KEY 配置")
        sys.exit(1)

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
