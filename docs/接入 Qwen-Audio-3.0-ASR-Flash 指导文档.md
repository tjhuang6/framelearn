# FrameLearn 接入 Qwen-Audio-3.0-ASR-Flash 指导文档

> 目标：将最长约 3 小时、中文为主且夹杂英文技术术语的教学视频转写为带时间戳的字幕，并为后续图文 Markdown 教材生成提供可靠的时间轴数据。
>
> 官方文档：<https://platform.qianwenai.com/docs/developer-guides/speech/asr>
>
> 模型说明：<https://platform.qianwenai.com/docs/developer-guides/speech/speech-to-text-models>
>
> 准确率优化：<https://platform.qianwenai.com/docs/developer-guides/speech/improve-recognition-accuracy>

## 1. 先明确两个模型 ID

Qwen-Audio-3.0-ASR-Flash 包含两种与 FrameLearn 相关的调用方式，二者不能混用。

| 模型 ID | 调用方式 | 单个音频限制 | 时间戳/字幕用途 | FrameLearn 建议 |
|---|---|---:|---|---|
| `qwen-audio-3.0-asr-flash` | 同步 HTTP，多模态生成端点 | 不超过 5 分钟、2 GB | 官方同步示例主要展示文本结果；接入时不能假定结果结构与异步 Filetrans 相同 | 只用于短音频或功能验证 |
| `qwen-audio-3.0-asr-flash-filetrans` | 异步 HTTP，录音文件转写端点 | 不超过 12 小时、2 GB | 返回句级和词级时间戳，适合生成 SRT/VTT | **FrameLearn 默认使用** |

### 1.1 本项目的最终选择

FrameLearn 的音频最长约 3 小时，因此生产链路应使用：

```text
qwen-audio-3.0-asr-flash-filetrans
```

不要因为产品名称里希望使用 `qwen-audio-3.0-asr-flash`，就把长音频强行送入同步端点。

- 同步模型最长 5 分钟。
- 3 小时音频若使用同步模型，需要切成 36 个 5 分钟片段。
- Filetrans 支持最长 12 小时，且官方推荐用于超过 5 分钟的录音文件。
- FrameLearn 仍建议把 3 小时音频切成 6 个约 30 分钟片段，以便并行、重试和显示进度，但这属于工程优化，不是模型限制。

推荐数据流：

```text
视频
  ↓
FFmpeg 提取 16 kHz 单声道 M4A
  ↓
按 30 分钟切片
  ↓
上传私有 OSS，生成临时签名 URL
  ↓
qwen-audio-3.0-asr-flash-filetrans 异步转写
  ↓
下载 transcription_url 对应的 JSON
  ↓
合并全局时间戳
  ↓
输出 subtitle.txt / subtitle.srt / subtitle.vtt / subtitle.json
  ↓
按时间轴与关键帧对齐，生成 Markdown 教材
```

## 2. 官方能力与本项目需要的字段

官方非实时语音识别文档确认以下模型支持时间戳：

- `qwen-audio-3.0-asr-flash-filetrans`
- `qwen-audio-3.0-asr-flash`
- `fun-asr`
- `fun-asr-flash`
- `paraformer-v2`

Qwen-Audio-3.0-ASR-Flash-Filetrans 的时间戳固定开启，无需设置 Paraformer 的 `timestamp_alignment_enabled`。

典型结果：

```json
{
  "transcripts": [
    {
      "sentences": [
        {
          "begin_time": 100,
          "end_time": 3820,
          "text": "现在我们使用 FastAPI 创建一个 WebSocket 服务。",
          "words": [
            {
              "begin_time": 100,
              "end_time": 596,
              "text": "现在"
            },
            {
              "begin_time": 596,
              "end_time": 844,
              "text": "我们"
            }
          ]
        }
      ]
    }
  ]
}
```

时间单位均为毫秒：

```text
sentences[].begin_time
sentences[].end_time
sentences[].words[].begin_time
sentences[].words[].end_time
```

FrameLearn 第一版可用句级时间戳生成 SRT；如需逐字高亮或更精细的字幕重排，再保留 `words`。

## 3. API 地址与鉴权

千问 AI 平台文档中的实际服务端点仍是 DashScope：

```text
API Base: https://dashscope.aliyuncs.com/api/v1
```

环境变量：

```bash
DASHSCOPE_API_KEY=your_dashscope_api_key
OSS_ACCESS_KEY_ID=your_oss_access_key_id
OSS_ACCESS_KEY_SECRET=your_oss_access_key_secret
```

禁止把真实 Key 写入：

- Python 源码
- `settings.toml`
- Git 提交
- 日志
- Markdown 文档

提交异步任务需要以下请求头：

```http
Authorization: Bearer ${DASHSCOPE_API_KEY}
Content-Type: application/json
X-DashScope-Async: enable
```

## 4. 推荐配置

修改 `settings.toml`：

```toml
[asr]
provider = "dashscope"
model = "qwen-audio-3.0-asr-flash-filetrans"
language_hints = ["zh", "en"]
diarization_enabled = false
chunk_duration = 1800
max_workers = 6
poll_interval = 5
poll_timeout = 3600
vocabulary_id = ""

[asr.oss]
bucket = "你的私有Bucket名称"
region = "oss-cn-hangzhou"
prefix = "framelearn-audio/"
url_ttl = 86400
```

参数说明：

- `language_hints = ["zh", "en"]`：声明音频包含中文和英文。
- `diarization_enabled = false`：当前教学视频基本为单人讲解，不启用说话人分离。
- `chunk_duration = 1800`：每段 30 分钟。
- `poll_interval = 5`：遵循官方建议，避免过于频繁地轮询任务。
- `url_ttl = 86400`：签名 URL 保留 24 小时，避免排队或重试期间过期。
- `vocabulary_id`：可选的预编译热词表 ID。

## 5. FrameLearn 现有代码必须先修复的问题

### 5.1 不要在 VideoPipeline 中硬编码 SiliconFlow

当前 `framelearn/pipeline/video_pipeline.py` 中存在：

```python
asr = ASRAdapter(provider="siliconflow")
```

应改为：

```python
asr = ASRAdapter()
```

否则 `settings.toml` 中的 `asr.provider = "dashscope"` 不会生效。

### 5.2 不要继续发送 Paraformer 专属参数

当前 `dashscope.py` 固定发送：

```python
{
    "timestamp_alignment_enabled": True,
    "diarization_enabled": False,
    "disfluency_removal_enabled": False,
    "language_hints": ["zh", "en"],
}
```

其中 `timestamp_alignment_enabled` 是 Paraformer 的时间戳校准参数。Qwen-Audio-3.0 Filetrans 的时间戳固定开启，不需要该参数。

应根据模型构造不同的参数：

```python
def _build_parameters(self) -> dict:
    if self.model == "qwen-audio-3.0-asr-flash-filetrans":
        parameters = {
            "channel_id": [0],
            "language_hints": self.language_hints,
            "diarization_enabled": self.diarization_enabled,
        }

        if self.vocabulary_id:
            parameters["vocabulary_id"] = self.vocabulary_id

        return parameters

    if self.model == "paraformer-v2":
        return {
            "channel_id": [0],
            "timestamp_alignment_enabled": True,
            "diarization_enabled": self.diarization_enabled,
            "disfluency_removal_enabled": self.disfluency_removal,
            "language_hints": self.language_hints,
        }

    raise ValueError(f"不支持的 DashScope ASR 模型：{self.model}")
```

## 6. 推荐的 DashScope 后端实现

FrameLearn 已经使用 `httpx`，因此核心转写链路无需额外安装 DashScope SDK。继续使用 REST API 最容易控制超时、重试和测试。

### 6.1 初始化配置

在 `DashscopeBackend.__init__` 中增加：

```python
self.model = config_get(
    "asr.model",
    "qwen-audio-3.0-asr-flash-filetrans",
)
self.language_hints = config_get("asr.language_hints", ["zh", "en"])
self.diarization_enabled = config_get("asr.diarization_enabled", False)
self.disfluency_removal = config_get("asr.disfluency_removal", False)
self.chunk_duration = config_get("asr.chunk_duration", 1800)
self.max_workers = config_get("asr.max_workers", 6)
self.poll_interval = config_get("asr.poll_interval", 5)
self.poll_timeout = config_get("asr.poll_timeout", 3600)
self.vocabulary_id = config_get("asr.vocabulary_id", "")
```

### 6.2 提交单个异步任务

```python
import httpx


def _submit_task(self, signed_url: str) -> str:
    payload = {
        "model": self.model,
        "input": {
            # Filetrans 单个任务建议提交一个公网可访问 URL。
            "file_urls": [signed_url],
        },
        "parameters": self._build_parameters(),
    }

    response = httpx.post(
        f"{self.API_BASE}/services/audio/asr/transcription",
        headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        },
        json=payload,
        timeout=30.0,
    )

    response.raise_for_status()
    body = response.json()

    task_id = body.get("output", {}).get("task_id")
    if not task_id:
        raise RuntimeError(
            "DashScope 响应中没有 task_id："
            f"request_id={body.get('request_id')}, body={body}"
        )

    return task_id
```

注意：日志中不要打印完整签名 URL，因为 URL 查询参数中包含临时访问签名。

### 6.3 轮询任务并下载结果

```python
import random
import time
import httpx


def _poll_task(self, task_id: str) -> dict:
    deadline = time.monotonic() + self.poll_timeout
    transient_failures = 0

    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"{self.API_BASE}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15.0,
            )

            if response.status_code == 429 or response.status_code >= 500:
                transient_failures += 1
                wait_seconds = min(
                    30,
                    self.poll_interval * (2 ** min(transient_failures, 3)),
                )
                time.sleep(wait_seconds + random.random())
                continue

            response.raise_for_status()
            body = response.json()
            output = body.get("output", {})
            status = output.get("task_status", "")

            if status == "SUCCEEDED":
                results = output.get("results", [])
                if not results:
                    raise RuntimeError(
                        f"任务成功但没有 results：task_id={task_id}"
                    )

                first_result = results[0]
                if first_result.get("subtask_status") not in (None, "SUCCEEDED"):
                    raise RuntimeError(
                        "子任务失败："
                        f"task_id={task_id}, result={first_result}"
                    )

                transcription_url = first_result.get("transcription_url")
                if not transcription_url:
                    raise RuntimeError(
                        f"缺少 transcription_url：task_id={task_id}"
                    )

                result_response = httpx.get(
                    transcription_url,
                    timeout=60.0,
                )
                result_response.raise_for_status()
                return result_response.json()

            if status == "FAILED":
                raise RuntimeError(
                    "DashScope 转写任务失败："
                    f"task_id={task_id}, "
                    f"code={output.get('code')}, "
                    f"message={output.get('message')}"
                )

            if status not in ("PENDING", "RUNNING"):
                raise RuntimeError(
                    f"未知任务状态：task_id={task_id}, status={status}"
                )

            transient_failures = 0
            time.sleep(self.poll_interval)

        except (httpx.TimeoutException, httpx.NetworkError):
            transient_failures += 1
            wait_seconds = min(
                30,
                self.poll_interval * (2 ** min(transient_failures, 3)),
            )
            time.sleep(wait_seconds + random.random())

    raise TimeoutError(
        f"DashScope 任务超时：task_id={task_id}, "
        f"timeout={self.poll_timeout}s"
    )
```

### 6.4 解析句子和词级时间戳

建议扩展 `asr_adapter.py` 的数据结构：

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TranscriptWord:
    text: str
    start: float
    end: float


@dataclass
class TranscriptSegment:
    text: str
    start: Optional[float] = None
    end: Optional[float] = None
    words: list[TranscriptWord] = field(default_factory=list)
```

解析函数：

```python
def _parse_transcript(self, result_json: dict, chunk_start_sec: float) -> list:
    from framelearn.pipeline.asr_adapter import (
        TranscriptSegment,
        TranscriptWord,
    )

    segments = []

    for transcript in result_json.get("transcripts", []):
        for sentence in transcript.get("sentences", []):
            sentence_start = (
                sentence.get("begin_time", 0) / 1000
                + chunk_start_sec
            )
            sentence_end = (
                sentence.get("end_time", 0) / 1000
                + chunk_start_sec
            )

            words = []
            for word in sentence.get("words", []):
                words.append(
                    TranscriptWord(
                        text=word.get("text", ""),
                        start=round(
                            word.get("begin_time", 0) / 1000
                            + chunk_start_sec,
                            3,
                        ),
                        end=round(
                            word.get("end_time", 0) / 1000
                            + chunk_start_sec,
                            3,
                        ),
                    )
                )

            segments.append(
                TranscriptSegment(
                    text=sentence.get("text", "").strip(),
                    start=round(sentence_start, 3),
                    end=round(sentence_end, 3),
                    words=words,
                )
            )

    return segments
```

合并分段：

```python
def _merge_results(self, results: list[tuple]) -> list:
    all_segments = []

    for chunk, result_json in results:
        all_segments.extend(
            self._parse_transcript(
                result_json=result_json,
                chunk_start_sec=chunk.start_sec,
            )
        )

    all_segments.sort(
        key=lambda segment: (
            float("inf") if segment.start is None else segment.start
        )
    )

    return all_segments
```

核心公式：

```python
global_start = chunk_start_sec + local_begin_time_ms / 1000
global_end = chunk_start_sec + local_end_time_ms / 1000
```

## 7. 音频提取和切片

当前 FrameLearn 已将音频转换为 16 kHz AAC/M4A。建议同时指定单声道和码率：

```bash
ffmpeg -i input.mp4 \
  -vn \
  -ac 1 \
  -ar 16000 \
  -c:a aac \
  -b:a 64k \
  -y audio.m4a
```

30 分钟无重叠切片：

```bash
ffmpeg -i audio.m4a \
  -f segment \
  -segment_time 1800 \
  -reset_timestamps 1 \
  -c copy \
  chunk_%03d.m4a
```

MVP 可以先无重叠。如果真实测试出现切片边界丢字，再加入约 2 秒重叠，并根据词级时间戳在合并阶段去重。

## 8. 生成 SRT、VTT 和 JSON

### 8.1 SRT

```python
def seconds_to_srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def build_srt(segments) -> str:
    blocks = []

    for index, segment in enumerate(segments, start=1):
        if segment.start is None or segment.end is None:
            continue
        if not segment.text.strip():
            continue

        blocks.append(
            "\n".join(
                [
                    str(index),
                    (
                        f"{seconds_to_srt_time(segment.start)} --> "
                        f"{seconds_to_srt_time(segment.end)}"
                    ),
                    segment.text.strip(),
                ]
            )
        )

    return "\n\n".join(blocks) + "\n"
```

### 8.2 VTT

```python
def seconds_to_vtt_time(seconds: float) -> str:
    return seconds_to_srt_time(seconds).replace(",", ".")


def build_vtt(segments) -> str:
    blocks = ["WEBVTT"]

    for segment in segments:
        if segment.start is None or segment.end is None:
            continue
        if not segment.text.strip():
            continue

        blocks.append(
            "\n".join(
                [
                    (
                        f"{seconds_to_vtt_time(segment.start)} --> "
                        f"{seconds_to_vtt_time(segment.end)}"
                    ),
                    segment.text.strip(),
                ]
            )
        )

    return "\n\n".join(blocks) + "\n"
```

### 8.3 JSON

```python
import json
from dataclasses import asdict


def build_transcript_json(segments) -> str:
    return json.dumps(
        {
            "segments": [asdict(segment) for segment in segments],
        },
        ensure_ascii=False,
        indent=2,
    )
```

建议输出：

```text
output/视频名称/src/
  subtitle.txt
  subtitle.srt
  subtitle.vtt
  subtitle.json
```

## 9. 使用课程热词提升中英文混合识别

编程教学视频常见问题是模型把英文技术词识别成中文同音词。应为课程维护热词表，例如：

```json
[
  {"text": "FastAPI", "weight": 4, "lang": "en"},
  {"text": "WebSocket", "weight": 4, "lang": "en"},
  {"text": "PyTorch", "weight": 4, "lang": "en"},
  {"text": "CrossEntropyLoss", "weight": 4, "lang": "en"},
  {"text": "DataLoader", "weight": 4, "lang": "en"},
  {"text": "反向传播", "weight": 4, "lang": "zh"},
  {"text": "梯度下降", "weight": 4, "lang": "zh"}
]
```

普通权重范围为 `1`～`5`，官方推荐权重 `4`。Qwen-Audio-3.0 系列还支持权重 `50` 的超级热词，但最多 50 个；过高权重可能影响其他内容，第一版不要默认使用超级热词。

推荐产品设计：

```text
用户上传课程词汇.txt
  ↓
后端创建或复用预编译热词表
  ↓
获得 vocabulary_id
  ↓
在 Filetrans 请求 parameters 中传入 vocabulary_id
```

如同时设置即时热词和 `vocabulary_id`，官方说明只有即时热词生效。第一版只实现一种方式，建议先实现可复用的预编译热词表。

## 10. 如果坚持使用 qwen-audio-3.0-asr-flash

同步模型端点：

```text
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
```

限制：

```text
单个文件不超过 5 分钟
单个文件不超过 2 GB
输入可使用 URL 或 Base64
```

对于大文件应使用 URL，不要把音频转成 Base64。

`httpx` 调用示例：

```python
import os
import httpx


def transcribe_short_audio(audio_url: str) -> dict:
    api_key = os.environ["DASHSCOPE_API_KEY"]

    response = httpx.post(
        (
            "https://dashscope.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation"
        ),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "disable",
        },
        json={
            "model": "qwen-audio-3.0-asr-flash",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": audio_url,
                                },
                            }
                        ],
                    }
                ]
            },
            "parameters": {
                "format": "m4a",
                "sample_rate": "16000",
                "vocabulary": {
                    "FastAPI": 4,
                    "WebSocket": 4,
                    "PyTorch": 4,
                },
            },
        },
        timeout=360.0,
    )
    response.raise_for_status()
    return response.json()


def extract_short_audio_text(response_json: dict) -> str:
    output = response_json.get("output", {})

    if isinstance(output.get("text"), str):
        return output["text"]

    nested_text = (
        output.get("output", {})
        .get("sentence", {})
        .get("text")
    )
    if isinstance(nested_text, str):
        return nested_text

    raise RuntimeError(
        f"无法从同步 ASR 响应中提取文本：{response_json}"
    )
```

同步接口的官方快速示例明确给出的文本路径是：

```text
output.text
output.output.sentence.text
```

它不是 OpenAI 格式，不存在 `choices[0].message.content`。

虽然模型总览将该模型列为支持时间戳，但同步快速示例没有展示与 Filetrans 完全相同的 `transcripts[].sentences[]` 结构。因此在 FrameLearn 的字幕生产链路中，不要假定同步响应一定可以直接按 Filetrans 结构解析。应先保存并检查真实原始响应；如果需要稳定的句/词级时间戳，使用 `qwen-audio-3.0-asr-flash-filetrans`。

## 11. 错误处理要求

至少处理以下错误：

| 类型 | 处理方式 |
|---|---|
| `401/403` | API Key、账号权限或地域错误，不自动无限重试 |
| `400` | 模型参数或文件 URL 不合法，记录 `request_id` 后失败 |
| `404` | 任务不存在或 URL 已失效 |
| `429` | 指数退避重试，降低并发 |
| `5xx` | 指数退避，最多重试有限次数 |
| 网络超时 | 重试查询；不要重复提交已成功创建的任务 |
| OSS URL 过期 | 重新签名后重新提交该片段 |
| 某个片段失败 | 只重试失败片段，不重跑完整 3 小时音频 |

必须保存以下状态，便于断点续传：

```text
chunk_index
chunk_start_sec
oss_object_key
dashscope_task_id
status
attempt_count
last_error
```

不要保存会过期的完整 OSS 签名 URL；保存 `oss_object_key`，需要时重新生成 URL。

## 12. 测试指导

### 12.1 单元测试

至少覆盖：

1. Qwen Filetrans 请求中不包含 `timestamp_alignment_enabled`。
2. Paraformer 请求仍包含 `timestamp_alignment_enabled`。
3. `language_hints` 为 `zh/en`。
4. 配置了 `vocabulary_id` 时才发送该字段。
5. 句级毫秒时间戳正确转换为秒。
6. 分段局部时间正确加上 `chunk.start_sec`。
7. `words` 的时间戳也正确加上分段偏移。
8. SRT 时间戳格式正确。
9. VTT 使用点号而不是逗号表示毫秒。
10. 某段失败时不会静默产出缺失章节的完整教材。

时间偏移测试示例：

```python
def test_parse_transcript_adds_chunk_offset():
    result_json = {
        "transcripts": [
            {
                "sentences": [
                    {
                        "begin_time": 1200,
                        "end_time": 4680,
                        "text": "测试字幕",
                        "words": [
                            {
                                "begin_time": 1200,
                                "end_time": 1800,
                                "text": "测试",
                            }
                        ],
                    }
                ]
            }
        ]
    }

    segments = backend._parse_transcript(
        result_json=result_json,
        chunk_start_sec=1800.0,
    )

    assert segments[0].start == 1801.2
    assert segments[0].end == 1804.68
    assert segments[0].words[0].start == 1801.2
    assert segments[0].words[0].end == 1801.8
```

### 12.2 集成测试

先准备一段 1～3 分钟的真实教学音频，包含：

- 中文讲解
- 英文框架名
- 函数名
- 数字和版本号
- 代码标识符

验证：

```text
任务提交成功
→ 任务状态变为 SUCCEEDED
→ transcription_url 可下载
→ sentences 不为空
→ begin_time < end_time
→ 时间戳不超出音频总长度
→ SRT 可被播放器加载
```

### 12.3 真实效果评测

使用同一段 10～20 分钟课程音频对比：

```text
qwen-audio-3.0-asr-flash-filetrans
paraformer-v2
```

记录：

- 中文字错误率
- 英文术语错误率
- 函数名正确率
- 时间戳偏差
- 字幕断句质量
- 处理耗时
- 每小时成本

## 13. 推荐实施顺序

1. 修复 `VideoPipeline` 中硬编码的 `provider="siliconflow"`。
2. 将默认模型改为 `qwen-audio-3.0-asr-flash-filetrans`。
3. 增加 `_build_parameters()`，区分 Qwen Audio 和 Paraformer 参数。
4. 保留现有 30 分钟切片、OSS 上传和异步任务流程。
5. 增强轮询重试和失败状态处理。
6. 扩展 `TranscriptSegment`，保留词级时间戳。
7. 输出 SRT、VTT 和 JSON。
8. 增加课程热词表。
9. 使用真实中英混合课程样本与 Paraformer 做 A/B 测试。
10. 确认效果后，再决定是否保留 Paraformer 作为低成本后端。

## 14. 完成标准

实现完成后，下面命令应能处理视频：

```bash
framelearn run /path/to/tutorial.mp4
```

输出至少包含：

```text
output/tutorial/
  index.md
  notes.md
  src/
    subtitle.txt
    subtitle.srt
    subtitle.vtt
    subtitle.json
    frame_001.jpg
    ...
```

验收条件：

- 中文与英文术语能同时识别。
- 字幕包含单调递增的全局时间戳。
- 30 分钟分段边界前后没有明显丢句。
- 某个分段失败时任务明确失败或进入重试，不能静默生成缺内容的教材。
- OSS 临时对象最终被清理。
- 日志中不出现 API Key、AccessKey Secret 或完整签名 URL。
- 3 小时音频可以断点重试，不需要从头重新执行。

## 15. 最终结论

对 FrameLearn 而言，正确的生产模型是：

```text
qwen-audio-3.0-asr-flash-filetrans
```

`qwen-audio-3.0-asr-flash` 可以保留为不超过 5 分钟的短音频同步后端，但不应作为 3 小时教学视频的默认实现。

两者关系可以写成：

```text
短音频快速识别（≤5分钟）
  → qwen-audio-3.0-asr-flash

长音频、时间戳、字幕（FrameLearn 默认）
  → qwen-audio-3.0-asr-flash-filetrans
```
