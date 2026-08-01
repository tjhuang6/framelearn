# 提案：视频处理流水线

## 问题

`framelearn run <视频>` 目前只打印 TODO，没有实际处理能力。用户无法从视频生成任何教材。

---

## 目标输出

给定一个本地视频文件，生成如下目录结构：

```
output/第四节分类任务/
  index.md          # 主文档（带时间戳、关键帧引用、章节结构）
  src/
    frame_001.jpg   # 关键帧截图
    frame_002.jpg
    ...
    subtitle.srt    # 完整字幕文件
    subtitle.json   # 字幕原始数据（含词级时间戳）
```

---

## 处理流水线

```
视频文件
  │
  ├── FFmpeg 提取音轨 → .m4a
  │      ↓
  │   切成 30 分钟分段（4 秒重叠）
  │      ↓
  │   上传阿里云 OSS → 生成签名 URL
  │      ↓
  │   并行提交百炼 paraformer-v2
  │      ↓
  │   合并分段结果 → 词级时间戳
  │      ↓
  │   字幕后处理（清洗 + 切分 + 去重）
  │      ↓
  │   生成 subtitle.srt + subtitle.json
  │
  └── FFmpeg 场景检测 + 定时抽帧
         ↓
      关键帧去重（感知哈希）
         ↓
      保存 src/frame_*.jpg（最多 100 帧）
         ↓
      关键帧与字幕时间轴对齐

关键帧（localImage）+ 字幕文字（text）
         ↓
Codex app-server 或 Vision API（按 settings.toml）
         ↓
分段结构化分析 → 章节标题 + 要点 + 代码片段
         ↓
生成 index.md
```

---

## 技术选型

| 环节 | 工具 | 理由 |
|------|------|------|
| 音轨提取 | FFmpeg | 本地，无需 API |
| 音频分段 | FFmpeg | 精确切分，支持重叠 |
| 临时存储 | 阿里云 OSS | 百炼需要 HTTP URL；国内稳定 |
| 语音识别 | 百炼 paraformer-v2 | 中文准确率高，词级时间戳，支持中英混合 |
| 关键帧提取 | FFmpeg 场景检测 | 本地，零成本 |
| 关键帧去重 | imagehash（感知哈希） | 去除相似帧，控制关键帧数量 |
| 视频理解 | Codex app-server 或 Vision API | 按 `settings.toml` 的 `vision_mode` 切换 |
| 文档生成 | Codex app-server 或 Text API | 按 `settings.toml` 的 `text_mode` 切换 |

---

## ASR 配置（settings.toml 新增）

```toml
[asr]
provider = "dashscope"        # dashscope（百炼）/ groq / openai / siliconflow
model = "paraformer-v2"
language_hints = ["zh", "en"]
disfluency_removal = false    # 保留语气词，时间轴更自然
chunk_duration = 1800         # 分段时长（秒，默认 30 分钟）
overlap = 4                   # 分段重叠（秒）

[asr.oss]
bucket = ""                   # 阿里云 OSS Bucket 名称
region = "oss-cn-hangzhou"    # OSS 节点
prefix = "framelearn-audio/"  # 上传路径前缀
url_ttl = 86400               # 签名 URL 有效期（秒，默认 24 小时）
```

---

## 新增 .env 配置

```bash
# 百炼 API Key（语音识别）
DASHSCOPE_API_KEY=your_key_here

# 阿里云 OSS（供百炼下载音频）
OSS_ACCESS_KEY_ID=your_key_here
OSS_ACCESS_KEY_SECRET=your_key_here
```

---

## 热词支持

处理任务时可选传入热词文件（提升专业术语识别率）：

```bash
framelearn run video.mp4 --vocab 课程词汇.txt
```

或在 `settings.toml` 中配置全局热词：

```toml
[asr]
hotwords = ["FastAPI", "WebSocket", "PyTorch", "asyncio"]
```

---

## 不做的事（第一版）

- 说话人分离（单人教学不需要）
- GPT 术语纠错（第二版迭代）
- 在线视频（YouTube/Bilibili 下载，第二版）
- Redis/Celery 任务队列（CLI 工具不需要）
- 字幕在线编辑界面

---

## 成本估算

| 环节 | 单价 | 1 小时视频 |
|------|------|-----------|
| 百炼 paraformer-v2 | ¥0.005/秒 | ¥18 |
| 阿里云 OSS 存储 | ¥0.12/GB/月 | 忽略（处理完即删） |
| OSS 流量 | ¥0.5/GB | < ¥1 |
| Vision API（关键帧分析） | 约 $0.01/图 × 50 帧 | ~$0.50 |
| **合计** | | **约 ¥21（~$3）** |

---

## 风险与对策

| 风险 | 对策 |
|------|------|
| OSS 上传失败 | 本地重试 3 次，失败后报错并清理临时文件 |
| 百炼识别超时 | 轮询间隔 5 秒，最多等 30 分钟，超时后单段重试 |
| 关键帧过多 | 感知哈希去重 + 最多 100 帧限制 |
| Vision API context 超限 | 分批发送，每批 ≤ 20 帧 |
| 临时文件残留 | `keep_temp_files = false` 时用 try/finally 保证清理 |
