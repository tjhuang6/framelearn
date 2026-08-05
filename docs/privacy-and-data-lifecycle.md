# 隐私与数据生命周期说明

本文档说明 FrameLearn 在处理视频和对话时的数据流向、保留周期、清理方式以及如何禁用持久化。

## 数据流向总览

FrameLearn 在运行时会与以下外部服务交互：

| 数据类型 | 外部服务 | 触发条件 | 数据内容 | 保留周期 |
|---------|---------|---------|---------|---------|
| **音频切片** | 阿里云 OSS | ASR provider=dashscope | 视频提取的音频切片（临时上传） | 任务完成后自动删除 |
| **ASR 转写** | 阿里云 DashScope | ASR provider=dashscope | OSS 签名 URL（不含原始音频） | 由服务商保留，具体见其隐私政策 |
| **ASR 转写** | 硅基流动 SenseVoice | ASR provider=siliconflow | 音频 base64 编码 | 由服务商保留，具体见其隐私政策 |
| **关键帧图像** | Vision API 服务商 | vision_mode=api | 视频截图 base64 编码 | 由服务商保留，具体见其隐私政策 |
| **字幕文本** | Vision/Text API | vision_mode=api 或 text_mode=api | 视频字幕纯文本 | 由服务商保留，具体见其隐私政策 |
| **对话历史** | 本地 SQLite | text_mode=appserver | 用户输入、assistant 输出、tool 调用、reasoning | 永久保留（直到手动删除） |
| **Codex 会话** | Codex app-server | text_mode=appserver | 用户输入、文件路径、工具执行结果 | 由 Codex 管理（通常仅内存） |

## 详细说明

### 1. 阿里云 OSS 音频上传（DashScope ASR 专用）

**触发条件**：
- `asr.provider = "dashscope"`
- 处理本地视频文件时

**数据流**：
1. FFmpeg 从视频提取音频
2. 音频按 `asr.chunk_duration`（默认 30 分钟）切片
3. 每个切片上传到配置的 OSS bucket（`asr.oss.bucket`）
4. 生成临时签名 URL（有效期 `asr.oss.url_ttl`，默认 24 小时）
5. 签名 URL 发送给 DashScope ASR API
6. **任务完成后，FrameLearn 自动调用 `oss.delete()` 删除所有切片**

**保留周期**：
- OSS 切片：任务结束后立即删除（最佳努力，网络错误可能导致残留）
- DashScope API 日志：由阿里云保留，具体见 [百炼隐私政策](https://help.aliyun.com/zh/model-studio/user-guide/privacy-policy)

**如何避免**：
```toml
[asr]
provider = "siliconflow"  # 改用不需要 OSS 的服务商
```

### 2. Vision API 关键帧分析

**触发条件**：
- `runtime.vision_mode = "api"`
- Agent 关键帧选择开启（`agent.keyframe_selection = true`）

**数据流**：
1. FFmpeg 截取关键帧为 JPEG
2. 图片编码为 base64
3. 发送到 `runtime.vision_provider` 配置的服务商（如 SiliconFlow、Gemini、Claude）
4. 服务商返回图像分析结果

**保留周期**：
- 本地关键帧：保存在 `output/<视频名>/src/frame_*.jpg`，永久保留（直到手动删除）
- 服务商日志：由各服务商自行管理，具体见各家隐私政策

**如何避免**：
```toml
[agent]
keyframe_selection = false  # 关闭 LLM 驱动的关键帧选择
```

### 3. Codex app-server 会话持久化

**触发条件**：
- `runtime.text_mode = "appserver"`
- 执行 `ask` 命令或自然语言对话

**数据流**：
1. 用户输入立即写入 `~/.framelearn/sessions.db`
2. Codex app-server 返回的 assistant 消息、tool 调用、reasoning 写入同一数据库
3. `RuntimeAdapter` 标记 `persisted=True`，避免重复写入

**数据库结构**：
- `sessions` 表：会话 ID、标题、thread_id、创建/更新时间
- `messages` 表：角色、内容、工具调用、reasoning、Codex 元数据、时间戳

**保留周期**：
- **永久保留**，直到用户手动清理

**如何清理**：
```bash
# 列出所有会话
framelearn session list

# 删除指定会话
framelearn session delete <session_id>

# 清空所有会话历史
framelearn session clear

# 查看数据库文件大小
framelearn session info
```

**如何禁用**：
```toml
[runtime]
persist_sessions = false  # 关闭会话持久化（所有对话仅存在内存）
```

### 4. 文本和 Vision API 调用

**触发条件**：
- `runtime.text_mode = "api"` 或 `runtime.vision_mode = "api"`

**数据流**：
1. 字幕文本、用户问题通过 HTTPS 发送到配置的 API 端点
2. Vision 场景下，图片编码为 base64 包含在请求体中

**保留周期**：
- 由各 API 服务商管理，具体见各家隐私政策：
  - [DeepSeek 隐私政策](https://www.deepseek.com/privacy-policy)
  - [OpenAI 隐私政策](https://openai.com/policies/privacy-policy)
  - [Google AI 隐私政策](https://ai.google.dev/gemini-api/terms)
  - [Anthropic 隐私政策](https://www.anthropic.com/privacy)
  - [SiliconFlow 隐私政策](https://siliconflow.cn/privacy)

## 本地数据清理

### 清理会话数据库

```bash
# 完整删除数据库文件（不可恢复）
rm ~/.framelearn/sessions.db

# 或使用命令行清理工具
framelearn session clear --confirm
```

### 清理视频处理缓存

```bash
# 删除特定视频的输出目录（包括关键帧、字幕、生成文档）
rm -rf output/<视频名>

# 清理所有输出
rm -rf output/
```

### 清理临时文件

DashScope ASR 的临时切片文件默认保留在 `output/<视频名>/temp/`，可通过配置控制：

```toml
[asr]
keep_temp_files = false  # 任务结束后自动删除 temp/ 目录
```

## 运行时隐私提示

启用 `runtime.privacy_hints = true` 后，每次任务会在控制台输出实际使用的外部服务：

```toml
[runtime]
privacy_hints = true  # 默认 false
```

示例输出：
```
🔒 本次任务将使用以下外部服务：
   • 阿里云 OSS (临时上传音频切片，任务完成后删除)
   • 阿里云 DashScope ASR (音频转写)
   • SiliconFlow Vision API (关键帧分析)
   • 本地 SQLite (对话历史持久化，位于 ~/.framelearn/sessions.db)
```

## 完全离线模式

如需完全避免数据上传，可使用以下配置：

```toml
[runtime]
text_mode = "appserver"      # Codex 本地运行
vision_mode = "appserver"    # 使用 Codex 的视觉能力
asr_mode = "local"           # （未实现）使用本地 Whisper

[asr]
provider = "local_whisper"   # （规划中）本地 ASR

[runtime]
persist_sessions = false     # 关闭会话持久化
```

**当前限制**：
- 本地 Whisper ASR 尚未实现，必须使用云端 ASR
- Codex app-server 的 vision 能力取决于其自身配置（可能仍调用云端 API）

## 安全建议

1. **敏感内容视频**：建议先手动审查视频内容，避免包含密码、密钥、个人身份信息
2. **企业内部视频**：确认公司政策允许上传到第三方 AI 服务
3. **API 密钥管理**：
   - 使用 `.env` 文件存储密钥，不要提交到版本控制
   - 定期轮换 API 密钥
   - 使用只读权限的 OSS 凭证（仅用于 ASR 上传）
4. **会话数据库**：
   - `~/.framelearn/sessions.db` 以明文存储对话历史
   - 如需加密，可使用 macOS FileVault 或 Linux LUKS 加密 home 目录

## 常见问题

### Q: 我的视频会被服务商用于训练吗？

A: 取决于服务商的数据使用政策。大部分企业 API（OpenAI、Anthropic、DeepSeek）承诺不使用 API 数据训练模型，但免费层或试用账户可能例外。请查阅各家最新隐私政策。

### Q: OSS 切片删除失败会怎样？

A: `oss_client.py` 的 `delete()` 方法设计为"最佳努力"（silent fail）。如网络错误导致删除失败，切片会残留在 OSS bucket 中。建议：
1. 在 OSS 控制台配置生命周期规则（如 7 天后自动删除 `framelearn-audio/` 前缀的对象）
2. 定期检查 bucket 并手动清理

### Q: 如何确认会话数据库已清空？

```bash
# 查看数据库文件大小和行数
framelearn session info

# 或直接用 SQLite 检查
sqlite3 ~/.framelearn/sessions.db "SELECT COUNT(*) FROM messages;"
```

### Q: 可以把会话数据库放在其他位置吗？

当前硬编码在 `~/.framelearn/sessions.db`。如需修改，可编辑 `framelearn/app_server/persistence.py`：

```python
def __init__(self, db_path: Optional[str] = None):
    if db_path is None:
        db_path = os.getenv("FRAMELEARN_SESSION_DB", str(Path.home() / ".framelearn" / "sessions.db"))
```

然后在 `.env` 中设置：
```bash
FRAMELEARN_SESSION_DB=/path/to/custom/sessions.db
```

## 相关文档

- [ASR 后端实现](../framelearn/pipeline/asr_backends/dashscope.py)
- [会话持久化实现](../framelearn/app_server/persistence.py)
- [Provider 适配器](../framelearn/provider_adapter.py)
- [配置文件说明](../settings.toml)
