# 通过 Codex App-Server 向 GPT‑5.6 发送视频关键帧与音轨转写

## 1. 结论

FrameLearn 可以通过 Codex app-server 将视频关键帧或定时截图发送给 GPT‑5.6，但需要区分三层能力：

1. **Codex app-server 协议能力**：支持文字、图片和音频类型的输入。
2. **GPT‑5.6 模型能力**：当前 `gpt-5.6-sol` 明确声明支持 `text` 和 `image`，没有声明支持 `audio`。
3. **Hermes 当前实现**：当前 app-server 适配器只向 `turn/start` 发送文字；图片会被替换成 `[image attached]`，不会把真实像素交给模型。

因此推荐使用以下处理链路：

```text
视频
  │
  ├── FFmpeg 场景检测 / 定时抽帧
  │      ↓
  │   带时间戳的关键帧 JPEG
  │
  └── FFmpeg 提取音轨
         ↓
      Whisper / faster-whisper
         ↓
      带时间戳的文字转写

关键帧 + 时间戳转写 + 分析指令
         ↓
Codex app-server turn/start
         ↓
GPT‑5.6 联合分析画面与语义
```

最终建议：

```text
关键帧/定时截图 → 作为 image/localImage 输入直接发送
音轨             → 先通过语音识别转成带时间戳文字
GPT‑5.6 输入      → text + image
```

---

## 2. 当前模型能力的实测结果

基于本机 `codex-cli 0.145.0` 生成的 app-server JSON Schema，`turn/start.input` 支持以下 `UserInput` 类型：

```text
text
image
localImage
audio
localAudio
skill
mention
```

但是协议支持某种输入，并不代表当前模型支持该模态。

通过 app-server 的 `model/list` 查询当前模型，`gpt-5.6-sol` 返回：

```json
{
  "id": "gpt-5.6-sol",
  "model": "gpt-5.6-sol",
  "inputModalities": ["text", "image"],
  "hidden": false
}
```

因此当前可靠的模型输入组合是：

```text
文字 + 图片
```

不应假设 `gpt-5.6-sol` 能直接可靠地理解原始音频。即使 app-server 接受 `audio` 或 `localAudio` 对象，上游模型也可能拒绝、忽略或无法正确处理。

---

## 3. App-server 多模态输入格式

### 3.1 远程图片

```json
{
  "type": "image",
  "url": "https://example.com/frame-001.jpg"
}
```

### 3.2 本地图片

```json
{
  "type": "localImage",
  "path": "/absolute/path/frames/frame-001.jpg"
}
```

本地路径应使用绝对路径，并确保 `codex app-server` 子进程有权读取该文件。

### 3.3 远程音频

协议支持：

```json
{
  "type": "audio",
  "url": "https://example.com/audio.wav"
}
```

### 3.4 本地音频

协议支持：

```json
{
  "type": "localAudio",
  "path": "/absolute/path/audio.wav"
}
```

但对于当前 `gpt-5.6-sol`，FrameLearn 不应默认使用这两种音频输入。音轨应优先经过专用 STT 模型转写。

---

## 4. 发送关键帧和转写文本的 `turn/start` 示例

建议在每张图片前加入对应的视频时间戳，使模型能够把图片和字幕放在同一时间轴上：

```json
{
  "id": 3,
  "method": "turn/start",
  "params": {
    "threadId": "thread-123",
    "model": "gpt-5.6-sol",
    "input": [
      {
        "type": "text",
        "text": "下面是视频关键帧和音轨转写。请结合时间戳分析视频，并指出画面与台词不一致的地方。"
      },
      {
        "type": "text",
        "text": "[00:00:02.000]"
      },
      {
        "type": "localImage",
        "path": "/absolute/path/frames/frame-0002.jpg"
      },
      {
        "type": "text",
        "text": "[00:00:05.000]"
      },
      {
        "type": "localImage",
        "path": "/absolute/path/frames/frame-0005.jpg"
      },
      {
        "type": "text",
        "text": "音轨转写：\n[00:00:01.200-00:00:03.800] 主持人：欢迎来到本期节目。\n[00:00:04.100-00:00:07.500] 嘉宾：今天我们演示新的产品。"
      }
    ]
  }
}
```

也可以把完整时间轴放在图片之后，但每张图片都应具有明确的时间戳或稳定的帧 ID。

推荐的内部数据结构：

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoFrame:
    timestamp_seconds: float
    path: Path
    source: str  # "interval" | "scene_change" | "manual"


@dataclass
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
```

---

## 5. 为什么音轨应该先转成文字

专用语音识别模型比不明确支持音频的视觉语言模型更适合处理：

- 长音频；
- 精确时间戳；
- 分段；
- 多语言；
- 说话人信息；
- 置信度；
- 批量处理和失败重试。

可选转写方案：

- 本地 `faster-whisper`；
- OpenAI Whisper API；
- Groq Whisper；
- Mistral Voxtral；
- ElevenLabs STT；
- macOS 本地语音识别。

推荐保留以下字段：

```json
[
  {
    "start": 1.2,
    "end": 3.8,
    "speaker": "主持人",
    "text": "欢迎来到本期节目。",
    "confidence": 0.97
  },
  {
    "start": 4.1,
    "end": 7.5,
    "speaker": "嘉宾",
    "text": "今天我们演示新的产品。",
    "confidence": 0.94
  }
]
```

建议同时生成：

```text
transcript.json  # 程序处理和时间轴对齐
transcript.srt   # 字幕预览和兼容导出
transcript.txt   # 发送给模型或人工阅读
```

---

## 6. 视频关键帧提取策略

### 6.1 固定间隔抽帧

例如每 5 秒抽取一帧：

```bash
ffmpeg -i input.mp4 \
  -vf "fps=1/5,scale=1280:-2" \
  frames/frame-%06d.jpg
```

优点：

- 时间覆盖均匀；
- 实现简单；
- 适合作为保底采样。

缺点：

- 可能产生大量重复图片；
- 可能错过很短的镜头变化。

### 6.2 场景变化抽帧

```bash
ffmpeg -i input.mp4 \
  -vf "select='gt(scene,0.30)',scale=1280:-2" \
  -vsync vfr \
  frames/scene-%06d.jpg
```

优点：

- 更接近语义关键帧；
- 图片数量通常更少；
- 更适合镜头变化明显的视频。

缺点：

- 可能漏掉长时间静态画面中的内容变化；
- 必须额外保存每一帧的准确时间戳。

### 6.3 推荐的混合策略

```text
每 10 秒保底一帧
+
场景变化阈值 0.25～0.35 的额外关键帧
+
感知哈希去除近似重复帧
+
按视频章节或时间窗口分组
```

建议控制单次请求中的图片数量：

| 视频长度 | 建议图片数量 | 处理方式 |
|---|---:|---|
| 1 分钟以内 | 10～30 张 | 一次请求通常足够 |
| 1～5 分钟 | 20～60 张 | 可按内容密度调整 |
| 5～15 分钟 | 30～100 张 | 建议分段处理 |
| 15 分钟以上 | 每段 20～50 张 | 先分段分析，再汇总 |

不要把视频每秒几十帧全部发送给模型。这样会造成：

- 上下文迅速膨胀；
- 大量重复视觉信息；
- 推理成本上升；
- 模型更难建立稳定时间线；
- 请求可能超过图片数量或上下文限制。

---

## 7. 音轨提取

将视频音轨转为适合语音识别的 16 kHz、单声道 PCM WAV：

```bash
ffmpeg -i input.mp4 \
  -vn \
  -ac 1 \
  -ar 16000 \
  -c:a pcm_s16le \
  audio.wav
```

建议先通过 `ffprobe` 获取：

- 视频总时长；
- 视频帧率；
- 视频时间基；
- 音频采样率；
- 音频声道；
- 音视频起始时间偏移。

音视频起始时间可能并非都从 0 开始。时间轴模块需要对 FFmpeg PTS、关键帧时间和 STT 时间进行统一归一化。

---

## 8. 当前 Hermes 为什么不能直接转发图片

当前实现文件：

```text
/Users/iwill/.hermes/hermes-agent/agent/transports/codex_app_server_session.py
```

其中 `_coerce_turn_input_text()` 会把富媒体输入压缩成纯文字，并把图片替换为：

```text
[image attached]
```

随后 `run_turn()` 只发送：

```python
{
    "threadId": self._thread_id,
    "input": [
        {
            "type": "text",
            "text": user_input_text,
        }
    ],
}
```

因此，在当前 Hermes app-server 模式中，即使上层提供了图片，GPT‑5.6 实际也看不到图片像素。

FrameLearn 如果参考 Hermes 架构，不应复制这一文本降级逻辑；应该直接保留 app-server 的结构化 `UserInput`。

---

## 9. FrameLearn 的多模态输入构造器

建议把 `_coerce_turn_input_text()` 改造成结构化输入构造器：

```python
from pathlib import Path
from typing import Any


def build_turn_inputs(items: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(items, str):
        return [{"type": "text", "text": items}]

    result: list[dict[str, Any]] = []

    for item in items:
        item_type = item.get("type")

        if item_type in {"text", "input_text"}:
            text = str(item.get("text") or item.get("content") or "").strip()
            if text:
                result.append({"type": "text", "text": text})

        elif item_type in {"localImage", "local_image"}:
            path = Path(str(item["path"])).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(path)
            result.append({"type": "localImage", "path": str(path)})

        elif item_type in {"image", "image_url"}:
            url = str(item.get("url") or "").strip()
            if not url:
                raise ValueError("image URL is empty")
            result.append({"type": "image", "url": url})

        else:
            raise ValueError(f"unsupported turn input type: {item_type!r}")

    if not result:
        raise ValueError("turn input cannot be empty")

    return result
```

然后将 `turn/start` 改为：

```python
turn_inputs = build_turn_inputs(user_input)

response = rpc.request(
    "turn/start",
    {
        "threadId": thread_id,
        "model": "gpt-5.6-sol",
        "input": turn_inputs,
    },
    timeout=10,
)
```

### 9.1 按时间轴构建请求

```python
def format_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def build_video_analysis_inputs(
    frames: list[VideoFrame],
    transcript: list[TranscriptSegment],
    instruction: str,
) -> list[dict]:
    inputs: list[dict] = [
        {
            "type": "text",
            "text": instruction,
        }
    ]

    for frame in sorted(frames, key=lambda value: value.timestamp_seconds):
        timestamp = format_timestamp(frame.timestamp_seconds)
        inputs.append({"type": "text", "text": f"关键帧 [{timestamp}]"})
        inputs.append({
            "type": "localImage",
            "path": str(frame.path.expanduser().resolve()),
        })

    transcript_lines = []
    for segment in transcript:
        start = format_timestamp(segment.start_seconds)
        end = format_timestamp(segment.end_seconds)
        speaker = f"{segment.speaker}：" if segment.speaker else ""
        transcript_lines.append(f"[{start} - {end}] {speaker}{segment.text}")

    inputs.append({
        "type": "text",
        "text": "音轨转写：\n" + "\n".join(transcript_lines),
    })
    return inputs
```

---

## 10. 推荐的 FrameLearn 模块结构

```text
framelearn/
  media/
    probe.py                 # ffprobe 获取媒体元数据
    frame_extractor.py       # 固定间隔 + 场景变化抽帧
    frame_deduplicator.py    # 感知哈希去重
    audio_extractor.py       # 提取 16 kHz 单声道音轨
    transcriber.py           # Whisper / faster-whisper 转写
    timeline.py              # 图片与字幕时间轴对齐

  codex/
    jsonrpc_client.py        # app-server JSON-RPC stdio
    app_server_session.py    # thread/turn 生命周期
    multimodal_input.py      # text/localImage 输入构建
    event_projector.py       # item/* -> 本地消息

  pipeline/
    video_analysis.py        # 视频分析总流程
    segment_analysis.py      # 分段分析
    summary.py               # 全局汇总
```

核心数据流：

```text
VideoAnalysisPipeline
  → MediaProbe
  → FrameExtractor
  → FrameDeduplicator
  → AudioExtractor
  → SpeechTranscriber
  → TimelineAligner
  → MultimodalInputBuilder
  → CodexAppServerSession.run_turn()
  → GPT‑5.6
  → SegmentResult
  → GlobalSummary
```

---

## 11. 长视频分段策略

对长视频，不应把所有关键帧和完整字幕放进一个 turn。推荐先分段：

```text
视频
  ↓
按章节 / 场景 / 固定时长切成多个 segment
  ↓
每个 segment：关键帧 + 对应字幕 → GPT‑5.6 分段结果
  ↓
所有分段结果 → GPT‑5.6 全局汇总
```

每段可采用：

- 30～120 秒；
- 5～20 张关键帧；
- 仅包含该时间窗口对应的字幕；
- 保留前后 2～5 秒重叠，避免语义在边界处被截断。

分段结果建议使用结构化输出：

```json
{
  "segment_start": 0.0,
  "segment_end": 60.0,
  "summary": "本段内容摘要",
  "visual_events": [
    {
      "timestamp": 12.5,
      "description": "讲者展示设置页面"
    }
  ],
  "spoken_topics": ["产品配置", "权限设置"],
  "inconsistencies": [],
  "important_frames": [12.5, 37.0]
}
```

---

## 12. 时间轴对齐

FrameLearn 应以秒或毫秒为统一内部时间单位，不要使用帧编号作为唯一时间标识。

推荐：

```text
内部计算：整数毫秒
显示输出：HH:MM:SS.mmm
FFmpeg 输入：PTS × time_base
STT 输入：start/end 秒数
```

图片和字幕对齐时，可为每张关键帧查找：

- 当前时刻正在发生的字幕；
- 前后最近的字幕；
- 所属 segment；
- 与上一个关键帧的时间距离。

示例：

```python
def transcript_near_frame(
    frame_time: float,
    segments: list[TranscriptSegment],
    margin: float = 2.0,
) -> list[TranscriptSegment]:
    return [
        segment
        for segment in segments
        if segment.start_seconds <= frame_time + margin
        and segment.end_seconds >= frame_time - margin
    ]
```

---

## 13. 文件与请求安全

发送本地图片前应检查：

- 路径为绝对路径；
- 文件存在且可读；
- 格式属于允许集合，如 JPEG/PNG/WebP；
- 文件大小不超过应用限制；
- 文件位于允许的工作目录；
- 不允许通过 `../` 越界读取；
- 临时文件在 turn 结束后按策略清理。

如果 app-server 运行在沙箱中，还必须确保关键帧目录属于可读取范围。

不要在 prompt 或日志中写入：

- API key；
- OAuth token；
- 用户隐私路径中不必要的目录信息；
- 未脱敏的个人音视频元数据。

---

## 14. 实施顺序

### 第一阶段：图片输入验证

- 从一个短视频每 5 秒抽一帧；
- 直接构造 `localImage` 输入；
- 调用 `turn/start`；
- 验证 GPT‑5.6 能描述图片内容。

### 第二阶段：音轨转写

- 提取 16 kHz 单声道 WAV；
- 使用 faster-whisper 转写；
- 保留 segment 时间戳；
- 把字幕作为 text 输入发送。

### 第三阶段：时间轴联合分析

- 每张图片前加入时间戳；
- 将字幕按时间排序；
- 要求模型输出带时间戳的结构化结果；
- 检查模型是否正确关联画面与台词。

### 第四阶段：长视频分段

- 按 30～120 秒分段；
- 分段抽帧和转写；
- 保存每段分析；
- 二次请求完成全局总结。

### 第五阶段：生产可靠性

- 图片去重；
- 转写缓存；
- 请求失败重试；
- app-server watchdog；
- 进程异常 retire/restart；
- segment checkpoint；
- 成本和 token 统计；
- 临时文件清理。

---

## 15. 验收测试

至少覆盖以下测试：

```text
[ ] app-server model/list 显示目标模型支持 image
[ ] localImage 使用绝对路径发送成功
[ ] 多张图片能在同一 turn 中发送
[ ] 每张图片具有准确时间戳
[ ] 音轨能提取为 16 kHz 单声道 WAV
[ ] STT 结果包含 start/end
[ ] 字幕与关键帧按统一时间轴排序
[ ] 当前 Hermes 的 [image attached] 降级路径没有被照搬
[ ] 长视频能够分段处理
[ ] 分段之间保留适当重叠
[ ] 最终结果可以引用原视频时间戳
[ ] 临时图片和音频能够安全清理
[ ] 单个 segment 失败后可以重试而不重跑整个视频
```

---

## 16. 最终建议

FrameLearn 的最佳实现不是把完整视频或原始音轨直接塞给 GPT‑5.6，而是构建一条可控、可缓存、可恢复的预处理管线：

```text
视频
  ↓
场景抽帧 + 定时保底抽帧 + 图片去重
  ↓
音轨提取 + 专用 STT + 时间戳字幕
  ↓
关键帧与字幕时间轴对齐
  ↓
text + localImage 发送给 gpt-5.6-sol
  ↓
分段结构化分析
  ↓
全局总结
```

这条方案符合当前实测能力：

```text
Codex app-server 0.145.0：协议支持 text/image/audio
当前 gpt-5.6-sol：声明支持 text/image
当前 Hermes 适配器：仅发送 text，需要修改才能转发真实图片
FrameLearn 推荐路径：图片直传，音轨先转写
```
