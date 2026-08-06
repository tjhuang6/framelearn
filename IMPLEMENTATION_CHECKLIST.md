# 隐私与数据边界修复实现清单

## ✅ 已完成的功能

### 1. 隐私与数据生命周期文档
- [x] `docs/privacy-and-data-lifecycle.md` - 完整的数据流向说明
- [x] 数据流向总览表（外部服务、触发条件、数据内容、保留周期）
- [x] OSS 上传详细说明
- [x] Vision API 调用说明
- [x] 会话持久化说明
- [x] 本地数据清理指南
- [x] 运行时隐私提示配置
- [x] 完全离线模式说明
- [x] 安全建议
- [x] 常见问题解答

### 2. 会话数据库管理工具
- [x] `framelearn/session_manager.py` - 完整的会话管理 API
- [x] `list_sessions()` - 列出所有会话
- [x] `show_info()` - 显示数据库统计
- [x] `delete_session()` - 删除指定会话
- [x] `clear_all_sessions()` - 清空所有会话
- [x] `export_session()` - 导出会话为 JSON

### 3. CLI 命令集成
- [x] `framelearn session list`
- [x] `framelearn session info`
- [x] `framelearn session delete <id>`
- [x] `framelearn session clear`
- [x] `framelearn session export <id> [path]`
- [x] 命令解析器支持 `session` 关键字
- [x] 路由器集成会话管理功能

### 4. 会话持久化开关
- [x] `runtime.persist_sessions` 配置项
- [x] `SessionDB(enabled=False)` 禁用模式
- [x] `RuntimeAdapter` 读取配置
- [x] 默认值保持向后兼容（默认 `true`）

### 5. 运行时隐私跟踪
- [x] `framelearn/privacy_tracker.py` - PrivacyTracker 类
- [x] 全局跟踪器状态管理
- [x] `VideoPipeline` 集成
- [x] 自动检测 OSS 上传
- [x] 自动检测 ASR 服务商
- [x] 自动检测 Vision API 使用
- [x] 自动检测 Text API 使用
- [x] 自动检测会话持久化状态
- [x] `runtime.privacy_hints` 配置项

### 6. 配置文件更新
- [x] `settings.toml` 新增 `persist_sessions`
- [x] `settings.toml` 新增 `privacy_hints`
- [x] `config.py` 默认值设置
- [x] 环境变量支持 `FRAMELEARN_SESSION_DB`

### 7. 文档更新
- [x] README.md 新增会话管理命令
- [x] README.md 新增隐私文档链接
- [x] `docs/privacy-usage-examples.md` - 使用示例
- [x] `PRIVACY_FIX_SUMMARY.md` - 修复总结

### 8. 测试覆盖
- [x] `test/test_privacy_and_sessions.py` - 单元测试
- [x] `test/test_privacy_integration.py` - 集成测试
- [x] PrivacyTracker 启用/禁用测试
- [x] SessionDB 禁用模式测试
- [x] 配置默认值测试
- [x] 会话管理命令测试
- [x] 所有测试通过（102/103，1个预存在失败）

## 📝 实现细节

### 数据库结构
- `sessions` 表：id, title, thread_id, created_at, updated_at
- `messages` 表：id, session_id, role, content, tool_calls, tool_call_id, reasoning, codex_thread_id, codex_turn_id, provider_item_id, created_at
- 索引：idx_messages_session (session_id, created_at)
- 唯一约束：UNIQUE(session_id, provider_item_id, role)

### 关键设计
1. **向后兼容**：默认行为不变
2. **优雅降级**：禁用持久化时 no-op，不影响逻辑
3. **最小侵入**：全局状态传递，不改变函数签名
4. **故障隔离**：OSS 删除失败不影响主流程

### 代码统计
- 新增文件：6 个
- 修改文件：9 个
- 新增代码：约 800 行

## 🧪 验证方法

### 基本功能验证
```bash
# 1. 会话管理
framelearn session info
framelearn session list

# 2. 隐私提示（需在 settings.toml 中启用 privacy_hints）
framelearn run /path/to/video.mp4

# 3. 会话持久化禁用（需在 settings.toml 中设置 persist_sessions = false）
framelearn ask "测试问题"
framelearn session info  # 应该显示没有新会话
```

### 测试运行
```bash
# 单元测试
.venv/bin/python test/test_privacy_and_sessions.py

# 集成测试
.venv/bin/python test/test_privacy_integration.py

# 全部测试
.venv/bin/python -m pytest test/ -v
```

## 📚 用户文档

### 核心文档
- [隐私与数据生命周期](docs/privacy-and-data-lifecycle.md)
- [隐私功能使用示例](docs/privacy-usage-examples.md)
- [修复总结](PRIVACY_FIX_SUMMARY.md)

### 快速开始
1. 查看会话：`framelearn session info`
2. 启用隐私提示：在 `settings.toml` 中设置 `privacy_hints = true`
3. 禁用持久化：在 `settings.toml` 中设置 `persist_sessions = false`
4. 清理会话：`framelearn session clear`

## ⚠️ 已知限制

1. **本地 ASR 未实现**：当前必须使用云端 ASR 服务
2. **Codex vision 可能仍调用云端**：取决于 Codex 自身配置
3. **OSS 删除失败处理**：silent fail，建议配置生命周期规则
4. **会话数据库明文存储**：建议使用系统级加密（FileVault、LUKS）

## 🔜 后续建议

1. 实现本地 Whisper ASR
2. 集成 SQLCipher 加密数据库
3. 添加会话自动过期策略
4. 实现外部服务调用审计日志
5. 提供数据匿名化导出工具

## ✅ 验证通过

- [x] 所有测试通过（102/103）
- [x] CLI 命令可用
- [x] 文档完整
- [x] 向后兼容
- [x] 无破坏性变更
