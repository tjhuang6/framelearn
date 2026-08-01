# 设计：阿里百炼 ASR 支持

## 模块结构

```
framelearn/pipeline/
  asr_adapter.py          ← 现有，扩展：加 DashscopeBackend
  asr_backends/
    __init__.py
    siliconflow.py        ← 现有逻辑移入
    dashscope.py          ← 新增
    oss_client.py         ← 新增：OSS 上传和签名 URL
```

---

## ASRAdapter 接口（不变）

上层调用方式不变，只通过 settings.toml 的 `asr.provider` 切换后端：

```python
@dataclass
class TranscriptSegment:
    text: str
    start: Optional[float] = None   # 秒（dashscope 有，siliconflow 无）
    end: Optional[float] = None

@dataclass
class TranscriptResult:
    segments: list[TranscriptSegment]
    full_text: str
    has_timestamps: bool
    srt: Optional[str] = None       # 新增：SRT 内容（仅 dashscope）
```

---

## DashscopeBackend 设计

### 核心流程

```python
class DashscopeBackend:
    def transcribe(self, audio_path: str) -> TranscriptResult:
        # 1. 切片
        chunks = self._split_audio(audio_path)
        
        # 2. 并行上传 OSS + 生成签名 URL
        signed_urls = self._upload_to_oss(chunks)
        
        # 3. 并行提交识别任务
        task_ids = self._submit_tasks(signed_urls)
        
        # 4. 轮询等待全部完成
        results = self._poll_all_tasks(task_ids)
        
        # 5. 合并，加时间偏移
        merged = self._merge_results(results, chunks)
        
        # 6. 清理 OSS
        self._cleanup_oss(chunks)
        
        return merged
```

---

## 切片设计

```python
@dataclass
class AudioChunk:
    index: int
    path: Path
    start_sec: float          # 在原始音频中的起始时间
    duration_sec: float
    oss_key: Optional[str] = None
    signed_url: Optional[str] = None

def _split_audio(self, audio_path: str) -> list[AudioChunk]:
    """用 FFmpeg 切成 30 分钟一段（第一版不重叠）"""
    pass
```

FFmpeg 切片命令：

```bash
# 切出第 i 段（从 start_sec 开始，duration_sec 秒）
ffmpeg -i audio.m4a \
  -ss {start_sec} \
  -t {duration_sec} \
  -c copy \
  -y chunk_{i:03d}.m4a
```

---

## OSS 客户端设计

```python
class OssClient:
    def __init__(self):
        import oss2
        auth = oss2.Auth(
            os.getenv("OSS_ACCESS_KEY_ID"),
            os.getenv("OSS_ACCESS_KEY_SECRET"),
        )
        self.bucket = oss2.Bucket(
            auth,
            f"https://{region}.aliyuncs.com",
            bucket_name,
        )

    def upload(self, local_path: str, object_key: str) -> str:
        """上传文件，返回 object_key"""
        self.bucket.put_object_from_file(object_key, local_path)
        return object_key

    def sign_url(self, object_key: str, ttl_seconds: int) -> str:
        """生成有效期内的下载签名 URL"""
        return self.bucket.sign_url("GET", object_key, ttl_seconds)

    def delete(self, object_key: str):
        """删除 OSS 中的文件"""
        self.bucket.delete_object(object_key)
```

---

## 百炼 API 调用

### 提交任务（异步）

```python
POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription

Headers:
  Authorization: Bearer {DASHSCOPE_API_KEY}
  X-DashScope-Async: enable

Body:
{
  "model": "paraformer-v2",
  "input": {
    "file_urls": ["https://oss-signed-url/chunk.m4a"]
  },
  "parameters": {
    "timestamp_alignment_enabled": true,
    "diarization_enabled": false,
    "disfluency_removal_enabled": false,
    "language_hints": ["zh", "en"]
  }
}

Response:
{
  "output": {
    "task_id": "abc123",
    "task_status": "PENDING"
  }
}
```

### 轮询任务状态

```python
GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}

Response:
{
  "output": {
    "task_status": "SUCCEEDED",  # PENDING | RUNNING | SUCCEEDED | FAILED
    "results": [
      {
        "transcription_url": "https://..."  # 结果 JSON 的下载地址
      }
    ]
  }
}
```

### 结果格式

从 `transcription_url` 下载的 JSON：

```json
{
  "transcripts": [
    {
      "channel_id": 0,
      "text": "完整转录文字",
      "sentences": [
        {
          "text": "这是一句话",
          "begin_time": 1200,    // 毫秒
          "end_time": 3800,
          "words": [
            {"text": "这", "begin_time": 1200, "end_time": 1400},
            {"text": "是", "begin_time": 1400, "end_time": 1600},
            ...
          ]
        }
      ]
    }
  ]
}
```

---

## 时间偏移合并

每段结果的时间戳是相对于本段起始的，需要加上分段偏移：

```python
def _merge_results(
    self,
    results: list[dict],
    chunks: list[AudioChunk],
) -> TranscriptResult:
    all_segments = []
    for result, chunk in zip(results, chunks):
        for sentence in result["transcripts"][0]["sentences"]:
            all_segments.append(TranscriptSegment(
                text=sentence["text"],
                start=(sentence["begin_time"] / 1000) + chunk.start_sec,
                end=(sentence["end_time"] / 1000) + chunk.start_sec,
            ))
    
    full_text = " ".join(s.text for s in all_segments)
    srt = _build_srt(all_segments)
    
    return TranscriptResult(
        segments=all_segments,
        full_text=full_text,
        has_timestamps=True,
        srt=srt,
    )
```

---

## SRT 生成

```python
def _build_srt(segments: list[TranscriptSegment]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _seconds_to_srt_time(seg.start)
        end = _seconds_to_srt_time(seg.end)
        lines.append(f"{i}\n{start} --> {end}\n{seg.text}\n")
    return "\n".join(lines)

def _seconds_to_srt_time(seconds: float) -> str:
    ms = int(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
```

---

## VideoPipeline 变更

当 `has_timestamps=True` 时，额外保存 SRT 文件：

```python
# 在 video_pipeline.py 的 Step 3 后添加
if transcript.has_timestamps and transcript.srt:
    srt_path = src_dir / "subtitle.srt"
    srt_path.write_text(transcript.srt, encoding="utf-8")
    print(f"✅ 字幕文件：{srt_path}")
```

---

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| OSS 上传失败 | 重试 3 次，失败后报错并清理已上传文件 |
| 百炼任务提交失败（4xx） | 直接报错，提示检查 API key |
| 轮询超时（超过 poll_timeout） | 标记该段失败，其余段继续合并 |
| 任务 FAILED | 记录失败原因，继续处理其余段 |
| 所有段均失败 | 报错 |
| 部分段失败 | 警告 + 跳过失败段，继续生成不完整结果 |
| OSS 清理失败 | 只记录警告，不影响主流程 |

---

## 并行设计

```python
import concurrent.futures

def _upload_and_submit(self, chunk: AudioChunk) -> tuple[AudioChunk, str]:
    """上传单段 + 提交识别任务，返回 (chunk, task_id)"""
    oss_key = self.oss.upload(str(chunk.path), ...)
    url = self.oss.sign_url(oss_key, ...)
    task_id = self._submit_task(url)
    chunk.oss_key = oss_key
    return chunk, task_id

with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
    futures = {
        executor.submit(self._upload_and_submit, chunk): chunk
        for chunk in chunks
    }
    for future in concurrent.futures.as_completed(futures):
        chunk, task_id = future.result()
        task_ids[task_id] = chunk
```

---

## 新增依赖

```toml
[project.dependencies]
oss2 = ">=2.18.0"    # 阿里云 OSS Python SDK
```

---

## 文件变更汇总

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `framelearn/pipeline/asr_adapter.py` | 修改 | 增加 provider 路由；TranscriptResult 增加 srt 字段 |
| `framelearn/pipeline/asr_backends/__init__.py` | 新增 | 包初始化 |
| `framelearn/pipeline/asr_backends/siliconflow.py` | 新增 | 从 asr_adapter.py 抽出现有逻辑 |
| `framelearn/pipeline/asr_backends/dashscope.py` | 新增 | 百炼完整实现 |
| `framelearn/pipeline/asr_backends/oss_client.py` | 新增 | OSS 上传 / 签名 / 删除 |
| `framelearn/pipeline/video_pipeline.py` | 修改 | 有时间戳时保存 .srt 文件 |
| `settings.toml` | 修改 | 添加 `[asr]` 和 `[asr.oss]` 配置 |
| `.env.example` | 修改 | 添加 `DASHSCOPE_API_KEY`、`OSS_*` |
| `test/src/test_pipeline.py` | 修改 | 新增 dashscope backend 测试 |
