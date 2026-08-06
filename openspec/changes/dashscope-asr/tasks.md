# 任务列表：阿里百炼 ASR 支持

## 前置条件

- [x] ASRAdapter（硅基流动版本）已实现
- [x] FFmpegHelper 已实现
- [x] 已有阿里云账号 + 已开通百炼服务
- [x] 已有阿里云 OSS Bucket（私有）
- [x] 已配置 DASHSCOPE_API_KEY / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET

---

## 任务

### Task 1：安装依赖 + 更新配置

- [x] 添加 `oss2` 依赖：`uv add oss2`
- [x] 更新 `settings.toml` 添加 `[asr]` 和 `[asr.oss]` 配置
- [x] 更新 `.env.example` 添加 `DASHSCOPE_API_KEY`、`OSS_*`

**验收**：`uv sync` 无报错

---

### Task 2：创建 asr_backends 包结构

- [x] 创建 `framelearn/pipeline/asr_backends/__init__.py`
- [x] 将现有硅基流动逻辑移入 `asr_backends/siliconflow.py`
- [x] 更新 `asr_adapter.py` 路由到对应 backend
- [x] 确保已有测试全部通过（不破坏现有功能）

**验收**：
```bash
pytest test/src/test_pipeline.py -v
# 所有 ASRAdapter 测试通过
```

---

### Task 3：实现 OssClient

**文件**：`framelearn/pipeline/asr_backends/oss_client.py`

- [x] `__init__`：从 .env 读取凭证，初始化 oss2.Bucket
- [x] `upload(local_path, object_key) -> str`
- [x] `sign_url(object_key, ttl_seconds) -> str`
- [x] `delete(object_key)`
- [x] 参数验证（bucket / key_id / key_secret 不能为空）

**验收**：
```python
client = OssClient()
key = client.upload("/tmp/test.m4a", "test/test.m4a")
url = client.sign_url(key, 3600)
assert url.startswith("https://")
client.delete(key)
```

---

### Task 4：实现 AudioChunker

**位置**：`framelearn/pipeline/asr_backends/dashscope.py` 内部方法

- [x] `split_audio(audio_path, chunk_duration, temp_dir) -> list[AudioChunk]`
- [x] 使用 ffprobe 获取音频总时长
- [x] 用 FFmpeg 切片（`-ss` + `-t` + `-c copy`）
- [x] 生成 `AudioChunk` 列表，含 `start_sec` 和 `duration_sec`

**验收**：
```python
chunks = split_audio("audio.m4a", chunk_duration=1800, temp_dir="/tmp")
# 一个 90 分钟音频 → 3 段
assert len(chunks) == 3
assert chunks[0].start_sec == 0
assert chunks[1].start_sec == 1800
assert chunks[2].start_sec == 3600
```

---

### Task 5：实现百炼 API 调用层

**位置**：`framelearn/pipeline/asr_backends/dashscope.py`

- [x] `_submit_task(signed_url) -> str`（提交识别任务，返回 task_id）
- [x] `_poll_task(task_id, timeout) -> dict`（轮询直到完成，返回结果 JSON URL）
- [x] `_download_result(result_url) -> dict`（下载结果 JSON）
- [x] 错误处理：4xx 直接报错，轮询超时标记失败

**验收**：
```python
task_id = backend._submit_task(signed_url)
result = backend._poll_task(task_id, timeout=300)
assert result["task_status"] == "SUCCEEDED"
```

---

### Task 6：实现并行上传 + 提交

**位置**：`DashscopeBackend._upload_and_submit_all()`

- [x] 使用 `ThreadPoolExecutor(max_workers=6)` 并行上传 + 提交
- [x] 每个线程：上传 → 生成签名 URL → 提交任务
- [x] 收集所有 `task_id`
- [x] 异常处理：单段失败不中断其他段

**验收**：
- 3 段音频并行上传，总时间约为单段时间（而不是 3 倍）

---

### Task 7：实现结果合并 + 时间偏移

**位置**：`DashscopeBackend._merge_results()`

- [x] 遍历每段结果，给时间戳加 `chunk.start_sec` 偏移
- [x] 合并所有 `TranscriptSegment`
- [x] 生成 `full_text`
- [x] 生成 SRT 内容（`_build_srt`）
- [x] 返回 `TranscriptResult(has_timestamps=True, srt=...)`

**验收**：
```python
# 第 2 段的第 1 句在 chunk 内时间戳为 5s，chunk.start_sec=1800
# 合并后该句时间戳应为 1805s
assert segments[n].start == 1805.0
```

---

### Task 8：OSS 临时文件清理

**位置**：`DashscopeBackend.transcribe()` 的 `finally` 块

- [x] 所有段识别完成后，删除 OSS 中对应的文件
- [x] 清理失败只记录警告，不影响主流程
- [x] 清理本地临时切片文件

---

### Task 9：更新 VideoPipeline 保存 SRT

**文件**：`framelearn/pipeline/video_pipeline.py`

- [x] `transcript.has_timestamps is True` 时，保存 `src/subtitle.srt`
- [x] 打印提示：`✅ 字幕文件：src/subtitle.srt`

**验收**：
```
output/video-name/
  src/
    subtitle.txt    # 始终存在（清洗后的文本）
    subtitle.srt    # 仅 dashscope 时存在（标准 SRT）
```

---

### Task 10：单元测试

**文件**：`test/src/test_pipeline.py` 新增

- [ ] `TestOssClient` — mock oss2.Bucket，测试上传/签名/删除
- [ ] `TestAudioChunker` — mock FFmpeg，测试切片计划
- [ ] `TestDashscopeBackend` — mock HTTP，测试提交/轮询/合并
- [ ] `TestTimeOffset` — 单元测试时间偏移计算
- [ ] `TestSrtGeneration` — 单元测试 SRT 格式

**验收**：
```bash
pytest test/src/test_pipeline.py -v
# 全部通过
```

---

### Task 11：手动集成测试

**测试视频**：`/Users/iwill/Documents/李哥考研/第四节分类任务(1).mp4`（1.4GB）

- [ ] 配置好 .env 中的 DASHSCOPE_API_KEY 和 OSS_*
- [ ] 设置 `settings.toml` 中 `asr.provider = "dashscope"`
- [ ] 运行：`framelearn run /path/to/video.mp4`
- [ ] 验证：
  - 音频切片正确（约 X 段）
  - OSS 上传成功
  - 识别任务并行提交
  - 时间戳合并正确
  - 生成 `subtitle.srt` 文件
  - OSS 临时文件已清理

**验收**：
```
output/第四节分类任务(1)/
  index.md
  notes.md
  src/
    frame_*.jpg
    subtitle.txt
    subtitle.srt    ← 新增，含词级时间戳
```

---

## 任务依赖图

```
Task 1 (配置依赖)
  ↓
Task 2 (重构 backend)
  ↓
Task 3 (OssClient) ──┐
Task 4 (Chunker)   ──┼── Task 6 (并行上传提交)
Task 5 (API 调用)  ──┘        ↓
                          Task 7 (合并)
                              ↓
                          Task 8 (清理) → Task 9 (SRT 保存)
                              ↓
                          Task 10 (测试) → Task 11 (集成测试)
```

---

## 预计工作量

| 任务 | 预计时间 |
|------|----------|
| 1-2（配置+重构） | 1 小时 |
| 3（OssClient） | 1-2 小时 |
| 4（切片） | 1 小时 |
| 5（API 调用） | 1-2 小时 |
| 6（并行） | 1 小时 |
| 7（合并） | 1 小时 |
| 8-9（清理+SRT） | 1 小时 |
| 10（测试） | 2-3 小时 |
| 11（集成测试） | 1 小时（含等待 API 时间） |
| **总计** | **10-13 小时（2 个工作日）** |
