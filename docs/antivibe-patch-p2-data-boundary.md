# 会话和远程媒体数据边界说明缺失修复 - 技术报告

## 问题描述（antivibe-technical-report.md 第 225 行）

**问题**：DashScope 路径会把音频切片上传到 OSS，再通过签名 URL 交给 ASR；Vision API 会发送字幕和关键帧；Codex 对话、工具调用和 reasoning 会明文保存到 `~/.framelearn/sessions.db`。当前用户文档没有集中说明这些数据去向、保留周期、清理方式或关闭持久化的方法。

**建议**：补充隐私与数据生命周期文档；提供会话数据库清理/禁用选项；输出每次任务实际使用的外部服务和远程数据类型。

## pi-agent 执行结果

✅ **已完成** - pi-agent 完成了三项建议的全部内容

### 1. 隐私与数据生命周期文档

**`docs/privacy-and-data-lifecycle.md`**（新增，约 8.5KB）

逐一说明了每种数据流向：

| 数据类型 | 外部服务 | 触发条件 | 保留周期 |
|---------|---------|---------|---------|
| 音频切片 | 阿里云 OSS | ASR provider=dashscope | 任务完成后自动删除 |
| ASR 转写 | DashScope / SiliconFlow | 对应 provider | 由服务商保留 |
| 关键帧图像 | Vision API 服务商 | vision_mode=api | 由服务商保留 |
| 对话历史 | 本地 SQLite | text_mode=appserver | 永久保留（直到手动删除） |

还包含：完全离线模式配置、安全建议、常见问题（是否用于训练、OSS 删除失败怎么办等）。

配套文档：`docs/privacy-usage-examples.md`（使用示例）。

### 2. 会话数据库清理/禁用功能

**新增模块**：`framelearn/session_manager.py`

**新增 CLI 命令**：
```bash
framelearn session list      # 列出所有会话
framelearn session info      # 查看数据库大小/统计
framelearn session delete <id>  # 删除指定会话
framelearn session clear     # 清空所有会话历史（自动 VACUUM 压缩）
framelearn session export    # 导出会话
```

**新增禁用开关**（`persistence.py` + `settings.toml`）：
```toml
[runtime]
persist_sessions = false  # 关闭后所有对话仅存在内存，不写库
```

pi-agent 特别说明：**所有 CLI 命令测试都在真实数据库的临时副本上验证**，未触碰用户 `~/.framelearn/sessions.db` 中现有的 11 个会话和 48 条消息。

### 3. 运行时隐私提示

**新增模块**：`framelearn/privacy_tracker.py`

`VideoPipeline` 运行时自动侵测本次任务实际用到的外部服务（OSS、ASR、Vision API、Text API、会话持久化位置）。开启后任务结束打印清单：

```toml
[runtime]
privacy_hints = true  # 默认 false
```

```
🔒 本次任务将使用以下外部服务：
   • 阿里云 OSS (临时上传音频切片，任务完成后删除)
   • 阿里云 DashScope ASR (音频转写)
   • SiliconFlow Vision API (关键帧分析)
   • 本地 SQLite (对话历史持久化，位于 ~/.framelearn/sessions.db)
```

## 测试验证

pi-agent 报告：新增两个测试文件，覆盖：
- privacy tracker 开关行为
- SessionDB 禁用模式
- 配置默认值
- CLI 命令（list/info/delete/clear/export）

**测试结果**：`pytest test/` 102/103 通过。唯一失败是**修复前就存在的、与本次改动无关的** `test_agent_keyframe.py`（因关键帧命名格式变更导致，与其他并行任务交叉，已在缓存可追溯性报告中记录为待办）。

## 数据安全说明

pi-agent 在验证过程中明确报告：**所有测试操作都在真实数据库的临时副本上进行**，验证后确认：
```
Real database untouched — 11 sessions, 48 messages, matching what it was before testing.
```

这确保了修复验证过程没有破坏用户现有的会话数据。

## 相关文件

### 新增的文件
- `docs/privacy-and-data-lifecycle.md` - 隐私与数据生命周期文档
- `docs/privacy-usage-examples.md` - 使用示例文档
- `framelearn/session_manager.py` - 会话数据库管理模块
- `framelearn/privacy_tracker.py` - 运行时隐私提示追踪器
- 2 个新测试文件（覆盖 tracker、SessionDB 禁用模式、CLI 命令）

### 修改的文件
- `framelearn/app_server/persistence.py` - 新增 `enabled` 开关
- `settings.toml` - 新增 `runtime.persist_sessions`、`runtime.privacy_hints`
- CLI 入口 - 新增 `session` 子命令组

## 总结

pi-agent 完整实现了本问题建议的三项内容：

1. ✅ 隐私与数据生命周期文档（覆盖 4 类数据流，含离线模式和安全建议）
2. ✅ 会话数据库清理（`session list/info/delete/clear/export`）和禁用（`persist_sessions=false`）
3. ✅ 运行时隐私提示（`privacy_hints=true` 输出本次任务使用的外部服务清单）

测试验证严谨，操作在临时副本上进行，未影响用户真实数据。

---

**状态**: ✅ 已完成并测试通过（102/103，1 个既有无关失败）  
**修复人**: pi-agent (OpenAI Codex)  
**数据安全**: 验证过程未触碰真实用户数据（已确认）
