# 隐私功能使用示例

## 示例 1: 查看会话数据库统计

```bash
# 查看数据库信息
$ framelearn session info

💾 会话数据库信息
   位置: /Users/iwill/.framelearn/sessions.db
   大小: 48.00 KB (49,152 bytes)

📊 统计信息
   会话数: 11
   消息总数: 48

   消息分布:
     • assistant: 32
     • user: 13
     • tool: 3

   最早会话: 2026-08-01 22:12:12
   最近更新: 2026-08-05 23:48:19
```

## 示例 2: 列出并管理会话

```bash
# 列出所有会话
$ framelearn session list

📚 共 11 个会话：

  • 549fb038-272e-45a4-a1b2-1c36320465dd
    标题: (无标题)
    消息数: 3
    更新: 2026-08-05 23:48:19
    Thread: 019fd29c-5131-7e02-99bb-a1adc1706623

  • 52046c36-985b-45a1-b149-1953589b33ad
    标题: (无标题)
    消息数: 6
    更新: 2026-08-02 14:23:52
    Thread: 019fc124-2c28-7251-9acb-48235682fe16

# 删除特定会话
$ framelearn session delete 549fb038-272e-45a4-a1b2-1c36320465dd

✅ 已删除会话 549fb038-272e-45a4-a1b2-1c36320465dd
   删除了 3 条消息
```

## 示例 3: 导出会话为 JSON

```bash
# 导出到文件
$ framelearn session export 52046c36-985b-45a1-b149-1953589b33ad session_backup.json

✅ 已导出到 session_backup.json

# 导出到标准输出
$ framelearn session export 52046c36-985b-45a1-b149-1953589b33ad

{
  "session": {
    "id": "52046c36-985b-45a1-b149-1953589b33ad",
    "title": "(无标题)",
    "thread_id": "019fc124-2c28-7251-9acb-48235682fe16",
    "created_at": "2026-08-02T14:20:15.123456",
    "updated_at": "2026-08-02T14:23:52.654321"
  },
  "messages": [
    {
      "role": "user",
      "content": "解释一下什么是虚拟环境",
      "created_at": "2026-08-02T14:20:15.234567"
    },
    {
      "role": "assistant",
      "content": "虚拟环境是 Python 项目隔离依赖的工具...",
      "created_at": "2026-08-02T14:20:18.345678"
    }
  ]
}
```

## 示例 4: 清空所有会话

```bash
$ framelearn session clear

⚠️  此操作将删除所有会话历史，不可恢复！
确认清空所有会话？(yes/no): yes

✅ 已清空所有会话
   删除了 11 个会话、48 条消息
   数据库已压缩

# 或使用 --confirm 跳过确认
$ framelearn session clear --confirm
```

## 示例 5: 禁用会话持久化

编辑 `settings.toml`：

```toml
[runtime]
persist_sessions = false  # 禁用会话持久化
```

此后所有对话仅存在内存中，进程结束后丢失。

## 示例 6: 启用隐私提示

编辑 `settings.toml`：

```toml
[runtime]
privacy_hints = true  # 启用隐私提示
```

处理视频时会显示实际使用的外部服务：

```bash
$ framelearn run /path/to/video.mp4

📹 开始处理视频：video.mp4
🎤 语音识别中...
🎵 提取音轨...
✂️  切分音频（每段 30 分钟）...
⬆️  上传并提交 2 段...
⏳ 等待识别完成（2 个任务）...
✨ 清洗字幕...
🖼️  抽取关键帧...
🔍 关键帧去重...
📝 生成课堂笔记...
📖 生成visual_script版...

🔒 本次任务使用的外部服务：
   • 阿里云 OSS（临时音频切片，任务完成后删除）
   • 阿里云 DashScope ASR (qwen-audio-3.0-asr-flash-filetrans)
   • Codex app-server 文档生成
   • 本地 SQLite 会话持久化 (/Users/iwill/.framelearn/sessions.db)

📂 输出目录：./output/video
📄 教材文件：./output/video/index.md
🖼️  关键帧数：15
```

## 示例 7: 完全离线配置（尚不完整）

```toml
[runtime]
text_mode = "appserver"      # Codex 本地运行
vision_mode = "appserver"    # 使用 Codex 的视觉能力
persist_sessions = false     # 不保存会话

[asr]
# 当前必须使用云端 ASR，本地 Whisper 尚未实现
provider = "dashscope"
```

**限制**：当前无法完全离线，因为：
1. ASR 必须使用云端服务（DashScope 或 SiliconFlow）
2. Codex app-server 的 vision 能力可能仍调用云端 API

## 示例 8: 自定义会话数据库路径

在 `.env` 中设置：

```bash
FRAMELEARN_SESSION_DB=/path/to/custom/sessions.db
```

然后所有会话数据将保存到指定路径。

## 示例 9: 手动清理 OSS 残留

如果网络错误导致 OSS 切片未删除，可手动清理：

### 方法 1: 配置 OSS 生命周期规则

在阿里云 OSS 控制台：
1. 打开 Bucket 设置
2. 添加生命周期规则：
   - 前缀：`framelearn-audio/`
   - 操作：删除
   - 时间：7 天

### 方法 2: 手动删除

```bash
# 使用阿里云 CLI
aliyun oss ls oss://framelearn-audio/framelearn-audio/
aliyun oss rm -r oss://framelearn-audio/framelearn-audio/
```

## 示例 10: 查看隐私政策

```bash
$ cat docs/privacy-and-data-lifecycle.md

# 或在浏览器中打开
$ open docs/privacy-and-data-lifecycle.md
```

完整说明包括：
- 数据流向总览表
- 每种外部服务的详细说明
- 保留周期和清理方式
- 完全离线模式配置
- 安全建议和常见问题
