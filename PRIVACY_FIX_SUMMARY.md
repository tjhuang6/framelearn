# 隐私与数据边界修复总结

## 修复内容

本次修复解决了会话和远程媒体数据边界缺少说明的问题，新增了以下功能和文档：

### 1. 隐私与数据生命周期文档

**文件**: `docs/privacy-and-data-lifecycle.md`

详细说明了：
- **数据流向总览表**：列出所有外部服务（OSS、DashScope、SiliconFlow、Vision API 等）、触发条件、数据内容和保留周期
- **详细说明**：
  - 阿里云 OSS 音频上传（DashScope ASR 专用）
  - Vision API 关键帧分析
  - Codex app-server 会话持久化（`~/.framelearn/sessions.db`）
  - 文本和 Vision API 调用
- **本地数据清理**：会话数据库、视频缓存、临时文件的清理方法
- **运行时隐私提示**：配置 `runtime.privacy_hints = true` 后每次任务显示实际使用的外部服务
- **完全离线模式**：如何配置避免数据上传（当前限制：本地 ASR 尚未实现）
- **安全建议**：敏感内容处理、API 密钥管理、会话数据库加密
- **常见问题**：数据用于训练、OSS 删除失败、会话数据库清空等

### 2. 会话数据库管理工具

**文件**: `framelearn/session_manager.py`

提供了完整的 CLI 命令：
- `framelearn session list` - 列出所有会话（ID、标题、消息数、更新时间）
- `framelearn session info` - 显示数据库统计信息（大小、会话数、消息分布、时间范围）
- `framelearn session delete <id>` - 删除指定会话及其消息
- `framelearn session clear` - 清空所有会话（需确认）
- `framelearn session export <id> [path]` - 导出会话为 JSON 格式

### 3. 会话持久化开关

**修改文件**:
- `framelearn/config.py` - 新增默认配置项
- `framelearn/app_server/persistence.py` - SessionDB 支持 `enabled=False` 禁用持久化
- `framelearn/app_server/runtime.py` - RuntimeAdapter 读取配置决定是否启用持久化
- `settings.toml` - 新增配置项

**配置项**:
```toml
[runtime]
persist_sessions = true   # false 时所有对话仅存在内存
privacy_hints = false     # true 时每次任务显示外部服务使用情况
```

### 4. 运行时隐私跟踪

**文件**: `framelearn/privacy_tracker.py`

- 新增 `PrivacyTracker` 类，跟踪每次任务使用的外部服务
- 在 `VideoPipeline` 中集成，自动检测：
  - OSS 上传（DashScope ASR）
  - ASR 服务商和模型
  - Vision API 服务商和模型（Agent 关键帧选择时）
  - Text API 服务商和模型（文档生成时）
  - 会话持久化状态
- 任务结束时输出摘要（需启用 `runtime.privacy_hints`）

**示例输出**:
```
🔒 本次任务使用的外部服务：
   • 阿里云 OSS（临时音频切片，任务完成后删除）
   • 阿里云 DashScope ASR (qwen-audio-3.0-asr-flash-filetrans)
   • Vision API 关键帧分析 (siliconflow/Qwen3.6-35B-A3B)
   • Codex app-server 文档生成
   • 本地 SQLite 会话持久化 (/Users/iwill/.framelearn/sessions.db)
```

### 5. 命令解析器更新

**修改文件**: `framelearn/command_parser.py`, `framelearn/router.py`

- 新增 `session` 为有效的传统命令
- 路由器支持 `session` 命令调度到 `session_manager`

### 6. 测试覆盖

**文件**: `test/test_privacy_and_sessions.py`

测试覆盖：
- PrivacyTracker 启用/禁用
- 配置默认值
- SessionDB 禁用模式
- 会话管理命令（list, info）

## 数据库结构

`~/.framelearn/sessions.db` SQLite 数据库结构：

**sessions 表**:
- `id` (TEXT, PRIMARY KEY) - 会话 UUID
- `title` (TEXT) - 会话标题
- `thread_id` (TEXT) - Codex thread ID
- `created_at` (REAL) - 创建时间戳
- `updated_at` (REAL) - 更新时间戳

**messages 表**:
- `id` (INTEGER, PRIMARY KEY) - 消息 ID
- `session_id` (TEXT, FOREIGN KEY) - 所属会话
- `role` (TEXT) - 角色（user/assistant/tool）
- `content` (TEXT) - 消息内容
- `tool_calls` (TEXT) - 工具调用 JSON
- `tool_call_id` (TEXT) - 工具调用 ID
- `reasoning` (TEXT) - 推理过程 JSON
- `codex_thread_id` (TEXT) - Codex 线程 ID
- `codex_turn_id` (TEXT) - Codex 轮次 ID
- `provider_item_id` (TEXT) - 服务商项目 ID
- `created_at` (REAL) - 创建时间戳

## 环境变量支持

新增环境变量：
- `FRAMELEARN_SESSION_DB` - 自定义会话数据库路径（默认 `~/.framelearn/sessions.db`）

## 配置文件更新

`settings.toml` 新增：
```toml
[runtime]
persist_sessions = true   # 会话持久化开关
privacy_hints = false     # 隐私提示开关
```

## 用户可见变更

### CLI 新命令
```bash
framelearn session list
framelearn session info
framelearn session delete <session_id>
framelearn session clear
framelearn session export <session_id> [output.json]
```

### 帮助文本更新
`framelearn help` 现在包含会话管理命令说明

### 文档更新
- README.md 新增会话管理命令示例
- README.md 新增隐私文档链接

## 测试验证

✅ 所有测试通过：
```
🧪 FrameLearn Privacy & Session Management Tests
✅ Privacy tracker test completed
✅ Configuration defaults test completed
✅ Session persistence disabled test completed
✅ Session management test completed
```

✅ CLI 命令验证：
```bash
$ framelearn session info
💾 会话数据库信息
   位置: /Users/iwill/.framelearn/sessions.db
   大小: 48.00 KB (49,152 bytes)
   会话数: 11
   消息总数: 48
```

## 实现细节

### 关键设计决策

1. **向后兼容**：默认 `persist_sessions = true`，保持现有行为
2. **优雅降级**：禁用持久化时 SessionDB 所有方法变为 no-op，不影响上层逻辑
3. **运行时可配置**：通过 `settings.toml` 和环境变量控制，无需修改代码
4. **最小侵入**：PrivacyTracker 通过全局状态传递，不影响函数签名
5. **故障隔离**：OSS 删除失败不影响主流程（silent fail + 建议配置生命周期规则）

### 代码修改统计

- 新增文件：4 个（`privacy_tracker.py`, `session_manager.py`, `privacy-and-data-lifecycle.md`, `test_privacy_and_sessions.py`）
- 修改文件：7 个（`persistence.py`, `runtime.py`, `config.py`, `router.py`, `command_parser.py`, `video_pipeline.py`, `settings.toml`, `README.md`）
- 总新增代码：约 600 行

## 后续建议

1. **本地 Whisper ASR**：实现完全离线模式的最后一环
2. **会话数据库加密**：考虑使用 SQLCipher 加密敏感对话历史
3. **自动清理策略**：允许配置会话自动过期时间（如 30 天后自动删除）
4. **审计日志**：记录外部服务调用日志到独立文件，便于合规审查
5. **数据匿名化**：提供工具将会话数据库中的敏感信息脱敏后导出，用于调试或分享

## 相关 Issue

解决了：
- 当前用户文档没有集中说明数据去向、保留周期、清理方式
- 缺少关闭持久化的方法
- 没有运行时提示实际使用的外部服务和数据类型

## 参考文档

- [隐私与数据生命周期说明](docs/privacy-and-data-lifecycle.md)
- [会话管理 API](framelearn/session_manager.py)
- [隐私跟踪器实现](framelearn/privacy_tracker.py)
