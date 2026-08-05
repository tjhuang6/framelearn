# Pi-agent 自动 Git 提交使用说明

## 功能

`.pi-auto-commit.sh` 是 pi-agent 的包装脚本，在每次 pi-agent 执行完成后自动检测文件变更并提交到 git。

## 使用方法

### 基本用法

代替直接调用 `pi`，使用包装脚本：

```bash
# 旧方式（不自动提交）
pi -p "任务描述" --session-id task-name

# 新方式（自动提交）
./.pi-auto-commit.sh -p "任务描述" --session-id task-name
```

### 示例

```bash
cd /Users/iwill/Documents/PythonProjects/FrameLearn-fix

# 单个任务
./.pi-auto-commit.sh -p "修复配置重复字段问题" --session-id config-cleanup

# 后台任务（结合 terminal 工具）
terminal(
    command="./.pi-auto-commit.sh -p '修复bug' --session-id fix-bug",
    background=true,
    notify_on_complete=true
)
```

### 提交信息格式

脚本自动生成的提交信息格式：

```
pi-agent: <session-id>

<任务描述前60字符>
```

例如：
```
pi-agent: config-cleanup

修复配置重复字段问题
```

## 工作原理

1. **记录初始状态**：执行 pi 前记录 `git status`
2. **执行 pi-agent**：透传所有参数给 `/opt/homebrew/bin/pi`
3. **检测变更**：对比执行前后的 git 状态
4. **自动提交**：如有变更，执行 `git add -A && git commit`
5. **显示结果**：输出提交的 hash 和消息

## 特性

- ✅ 透传所有 pi 参数（`--session-id`、`-p`、`--provider` 等）
- ✅ 保留 pi 的原始退出码
- ✅ 仅在有文件变更时才提交
- ✅ 自动从参数中提取任务描述和 session-id 作为提交信息
- ✅ 显示将要提交的文件列表（`git status --short`）

## 注意事项

### 1. 必须在项目根目录调用

脚本内部会 `cd` 到 `/Users/iwill/Documents/PythonProjects/FrameLearn-fix`，确保在正确的 git 仓库中提交。

### 2. 需要干净的工作区（可选）

如果希望每次提交都只包含 pi-agent 的变更，在运行前确保工作区干净：

```bash
git status  # 确认无未提交变更
./.pi-auto-commit.sh -p "任务" --session-id task
```

### 3. 与现有变更混合

脚本会提交**所有**未暂存的变更（`git add -A`），包括你手动修改但未提交的文件。如不希望如此，先手动提交现有变更：

```bash
git add specific_file.py
git commit -m "手动修改"
./.pi-auto-commit.sh -p "pi任务" --session-id task  # 现在只提交 pi 的变更
```

### 4. --no-session 的问题

如果使用 `--no-session`，脚本无法提取 session-id，提交信息会是：

```
pi-agent: auto-commit

<任务描述前60字符>
```

**强烈建议**始终使用 `--session-id <有意义的id>`，这样提交信息更清晰。

## 与 Hermes 集成

在 Hermes 中通过 `terminal` 工具调用：

```python
# 单次任务
terminal(
    command="./.pi-auto-commit.sh -p '修复XYZ' --session-id fix-xyz",
    workdir="/Users/iwill/Documents/PythonProjects/FrameLearn-fix",
    pty=true
)

# 后台任务
terminal(
    command="./.pi-auto-commit.sh -p '长时间任务' --session-id long-task",
    workdir="/Users/iwill/Documents/PythonProjects/FrameLearn-fix",
    background=true,
    notify_on_complete=true,
    pty=true
)
```

## 查看历史提交

```bash
# 查看最近的 pi-agent 提交
git log --oneline --grep="pi-agent" -10

# 查看特定 session 的提交
git log --oneline --grep="config-cleanup" -10
```

## 禁用自动提交

如果某次任务不想自动提交，直接用原生 pi：

```bash
/opt/homebrew/bin/pi -p "任务" --session-id task
```

## 故障排查

### 脚本找不到

```bash
# 检查脚本是否存在且可执行
ls -la /Users/iwill/Documents/PythonProjects/FrameLearn-fix/.pi-auto-commit.sh
chmod +x /Users/iwill/Documents/PythonProjects/FrameLearn-fix/.pi-auto-commit.sh
```

### Git 提交失败

脚本会因为任何 git 错误而停止（`set -e`）。常见问题：
- 不在 git 仓库中
- 没有配置 git 用户名/邮箱
- 工作区有冲突

检查：
```bash
cd /Users/iwill/Documents/PythonProjects/FrameLearn-fix
git status
git config user.name
git config user.email
```

### 提交信息不符合预期

手动测试参数提取：

```bash
./.pi-auto-commit.sh -p "测试任务描述" --session-id test-task
git log -1  # 查看最后一次提交
```

---

**创建时间**: 2026-08-06  
**适用范围**: `/Users/iwill/Documents/PythonProjects/FrameLearn-fix`  
**依赖**: bash, git, pi (0.83.0+)
