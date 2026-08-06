#!/bin/bash
# Pi-agent wrapper with auto git commit after edits
# Usage: ./.pi-auto-commit.sh -p "task description" --session-id task-name

set -e

REPO_DIR="/Users/iwill/Documents/PythonProjects/FrameLearn-fix"
cd "$REPO_DIR"

# 记录修改前的 git 状态
BEFORE_HASH=$(git rev-parse HEAD 2>/dev/null || echo "")
BEFORE_STATUS=$(git status --porcelain 2>/dev/null || echo "")

# 执行 pi-agent（传递所有参数）
/opt/homebrew/bin/pi "$@"
PI_EXIT_CODE=$?

# 检查是否有新的变更
AFTER_STATUS=$(git status --porcelain 2>/dev/null || echo "")

if [ "$AFTER_STATUS" != "$BEFORE_STATUS" ] || [ -n "$AFTER_STATUS" ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🔄 检测到文件变更，自动提交..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # 提取任务描述（从 -p 参数）
    TASK_DESC=""
    NEXT_IS_PROMPT=false
    for arg in "$@"; do
        if [ "$NEXT_IS_PROMPT" = true ]; then
            TASK_DESC="$arg"
            break
        fi
        if [ "$arg" = "-p" ] || [ "$arg" = "--print" ]; then
            NEXT_IS_PROMPT=true
        fi
    done
    
    # 提取 session-id
    SESSION_ID=""
    NEXT_IS_SESSION=false
    for arg in "$@"; do
        if [ "$NEXT_IS_SESSION" = true ]; then
            SESSION_ID="$arg"
            break
        fi
        if [ "$arg" = "--session-id" ]; then
            NEXT_IS_SESSION=true
        fi
    done
    
    # 生成提交信息
    if [ -n "$SESSION_ID" ]; then
        COMMIT_MSG="pi-agent: $SESSION_ID"
    else
        COMMIT_MSG="pi-agent: auto-commit"
    fi
    
    if [ -n "$TASK_DESC" ]; then
        # 截取前 60 字符作为摘要
        TASK_SUMMARY=$(echo "$TASK_DESC" | head -c 60)
        COMMIT_MSG="$COMMIT_MSG

$TASK_SUMMARY"
    fi
    
    # 显示即将提交的文件
    git status --short
    echo ""
    
    # Git add + commit
    git add -A
    git commit -m "$COMMIT_MSG" --quiet
    
    AFTER_HASH=$(git rev-parse HEAD)
    echo "✅ 已提交: $AFTER_HASH"
    echo "   消息: $(echo "$COMMIT_MSG" | head -1)"
    echo ""
else
    echo ""
    echo "ℹ️  无文件变更，跳过提交"
fi

exit $PI_EXIT_CODE
