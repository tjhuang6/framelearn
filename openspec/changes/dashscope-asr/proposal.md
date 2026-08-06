# 提案：阿里百炼 ASR 支持

## 问题

当前 `ASRAdapter` 只支持硅基流动 SenseVoice：
- **无时间戳**：无法生成 SRT，也无法把字幕和关键帧对齐
- **长音频受限**：硅基流动的文件大小上限约 100MB，1.4GB 视频的音轨无法直接上传

## 目标

为 `ASRAdapter` 增加阿里百炼 paraformer-v2 支持：

- **词级时间戳** → 可以生成标准 SRT 字幕文件
- **30 分钟分段 + 并行识别** → 支持 3 小时以上长视频
- **中英混合** → `language_hints: ["zh", "en"]`，适合编程教学
- **OSS 中转** → 百炼通过 HTTP URL 拉取音频，需要先上传 OSS 生成签名链接

---

## 技术方案

```
音频文件（.m4a）
  ↓
FFmpeg 切片（每段 30 分钟，无重叠）
  ↓
并行上传 → 阿里云 OSS（私有存储）
  ↓
并行生成签名 URL（24 小时有效）
  ↓
并行提交百炼 paraformer-v2 识别任务
  ↓
轮询等待所有任务完成
  ↓
给每段结果加上分段起始时间偏移
  ↓
合并 → 去重叠 → 输出完整结果
  ↓
生成 SRT + JSON
  ↓
识别完成后删除 OSS 临时文件
```

---

## 新增配置

### settings.toml

```toml
[asr]
provider = "dashscope"                 # dashscope | siliconflow
model = "paraformer-v2"
language_hints = ["zh", "en"]
disfluency_removal = false
chunk_duration = 1800                  # 分段时长（秒），默认 30 分钟
overlap = 0                            # 第一版不做重叠
poll_interval = 5                      # 轮询间隔（秒）
poll_timeout = 1800                    # 单段最大等待时间（秒）

[asr.oss]
bucket = ""                            # OSS Bucket 名称（必填）
region = "oss-cn-hangzhou"             # OSS 节点
prefix = "framelearn-audio/"          # 上传路径前缀
url_ttl = 86400                        # 签名 URL 有效期（秒）
```

### .env.example 新增

```bash
# 阿里百炼 API Key
DASHSCOPE_API_KEY=your_dashscope_key

# 阿里云 OSS（供百炼下载音频用）
OSS_ACCESS_KEY_ID=your_oss_key_id
OSS_ACCESS_KEY_SECRET=your_oss_key_secret
```

---

## 与硅基流动的关系

两种 provider 共用同一个 `ASRAdapter` 接口，通过 `settings.toml` 的 `asr.provider` 切换：

```python
adapter = ASRAdapter()          # 自动从 settings.toml 读取 provider
result = adapter.transcribe(audio_path)
# result.has_timestamps: dashscope=True, siliconflow=False
```

`settings.toml` 默认仍然是 `siliconflow`（无需额外配置即可使用）。用户想要时间戳时切换到 `dashscope`。

---

## 不做的事（第一版）

- 重叠分段（先不做，第二版根据实际丢字情况再加）
- 热词表（不影响功能正确性，第二版迭代）
- SRT 字幕精细断句（先生成基础 SRT，格式优化第二版）
