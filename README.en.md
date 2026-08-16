# FrameLearn

[中文](README.md) | English

FrameLearn converts local and online programming tutorial videos into Markdown learning material with timestamped keyframes. The current implementation covers online video downloading, audio extraction, ASR, the anchored blog pipeline (text LLM writes blog prose and frame anchors, FFmpeg supplies candidate frames, and the vision model validates frames before assembly), dual Markdown output, and general-purpose Q&A through the text API.

## Current capabilities

- Processes local `.mp4`, `.mkv`, `.avi`, `.mov`, `.flv`, `.wmv`, and `.webm` files.
- Downloads online videos from YouTube, Bilibili (including `b23.tv`), Douyin, and Kuaishou. Downloaders are yt-dlp based with BiliNote-adapted Bilibili dm_img patch, Douyin visitor-cookie, and Kuaishou GraphQL flows.
- Online subtitles are attached automatically when available: YouTube uses Supadata native English transcripts (`lang=en&mode=native`, same as youtube-digest), Bilibili uses the `x/player/v2` official subtitle API (same as Bilitato, Chinese preferred); then yt-dlp subtitles, then ASR.
- Uses the video's audio stream or finds a companion `.mp3`, `.m4a`, or `.aac` file for split Bilibili downloads.
- Supports two ASR backends:
  - Aliyun DashScope for chunked long-audio transcription, OSS upload, async polling, checkpoints, and SRT timestamps.
  - SiliconFlow SenseVoice for simpler transcription without timestamps.
- Accepts an existing `.txt`, `.srt`, or `.vtt` file through `--subtitle` to skip ASR.
- **Anchored blog pipeline**: SRT is split into configurable chunks (default 10 minutes), candidate frame markers are inserted into each chunk, the text LLM lightly polishes the transcript into lecture notes that keep the speaker's voice and `[[FRAME:id@timestamp]]` anchors, the program binds candidate frames or asks FFmpeg for precise captures, and the Qwen3-VL vision model only validates frames (retake / keep / caption / text_representation).
- Chunks are processed in parallel, bounded by `[chunking].concurrency`.
- Always produces two Markdown files: `srt_picture.md` (preserves SRT structure, timestamps + embedded images) and `blog.md` (complete illustrated transcript in the speaker's voice + the same images).
- Heuristic frame extraction (FFmpeg scene detection + pHash dedup) is summarized by SHA256 in the manifest, so a config change or a different frame set invalidates the cache automatically.
- Routes `ask` through a text LLM API.

## Online video notes

- Downloaded videos are cached in `[download].download_dir` (default `./downloads`) and reused by platform video ID.
- YouTube generally needs a proxy in mainland China: set `DOWNLOAD_PROXY`, `[download].proxy`, or standard `HTTP_PROXY` / `HTTPS_PROXY`.
- Optional cookies in `.env`: `BILIBILI_COOKIE`, `YOUTUBE_COOKIE`, `DOUYIN_COOKIE`, `KUAISHOU_COOKIE`; `SUPADATA_API_KEY` enables the native YouTube transcript path. Kuaishou often requires `KUAISHOU_COOKIE` when the GraphQL API returns a captcha challenge.

## Current limitations

- Douyin/Kuaishou have no stable public subtitle API, so those platforms still go through ASR.
- Kuaishou's anti-bot checks may block requests without `KUAISHOU_COOKIE`.
- `summarize` only prints instructions for an external `/summarize-learning` skill.
- `ask` is a general workspace conversation, not a tutorial-grounded RAG implementation.

- The legacy `agent_keyframe_selector.py` and the `notes` / `visual_script` modes of `doc_generator.py` are superseded by the chunked flow and kept only for back-compat.

## Install

Python 3.11+, [uv](https://docs.astral.sh/uv/), FFmpeg, and `ffprobe` are required.

```bash
brew install ffmpeg
uv sync
```

The source also imports Pillow, ImageHash, and `oss2`. If they are absent from your environment, add them before using the related features:

```bash
uv add pillow imagehash oss2
```

## Configure

```bash
cp .env.example .env
```

The repository's current `settings.toml` key sections:

```toml
[text]
text_mode = "api"
provider = "claude"
model = "MiniMax-M3"
base_url = "https://api.minimaxi.com/anthropic"

[vision]
vision_mode = "api"
vision_provider = "siliconflow"
vision_model = "Qwen/Qwen3-VL-8B-Instruct"

[asr]
provider = "dashscope"
model = "qwen-audio-3.0-asr-flash-filetrans"

[download]
download_dir = "./downloads"   # online video cache
proxy = ""                     # download proxy; falls back to DOWNLOAD_PROXY/HTTP(S)_PROXY

[chunking]
segment_minutes = 10          # video minutes per chunk; 5-10 recommended
max_images_per_chunk = 20     # max candidate frames shown to the LLM per chunk
concurrency = 5               # max chunks processed in parallel

[text_clean]
# legacy chunked pipeline; the current blog-anchor pipeline does not call TextCleaner
filler_words = ["那么", "就是说", "大家注意", "咱们", "啊", "嗯", "这个", "那个", "对吧"]

[heuristic]
scene_threshold = 0.4         # FFmpeg scene-detection threshold (lower = more sensitive)
similarity_threshold = 0.95   # pHash dedup threshold
max_frames = 200

[doc_gen]
srt_filename = "srt_picture.md"   # SRT structure + images
blog_filename = "blog.md"         # complete illustrated transcript in the speaker's voice

[blog_gen]
frame_match_tolerance = 2.0       # anchor-to-candidate timestamp tolerance (seconds)
max_retakes = 1                   # vision model retake budget
```

With those defaults, configure at least:

```bash
DASHSCOPE_API_KEY=...
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
SILICONFLOW_API_KEY=...
```

Text and vision providers can also be overridden with the `TEXT_*` / `VISION_*` environment variables documented in [`.env.example`](.env.example).

## Run

```bash
# Local video
framelearn run /absolute/path/tutorial.mp4

# Online video (downloaded to ./downloads, then runs the same pipeline)
framelearn run "https://www.bilibili.com/video/BV1xx..."
framelearn run "https://youtube.com/watch?v=xxx"
framelearn run "https://v.douyin.com/xxxx/"
framelearn run "https://v.kuaishou.com/xxxx"

# Reuse an existing subtitle and skip ASR
framelearn run /absolute/path/tutorial.mp4 --subtitle /absolute/path/tutorial.srt

# Natural-language entry point
framelearn "Process this video /absolute/path/tutorial.mp4"

# General-purpose workspace Q&A
framelearn ask "Explain this project architecture"

# Start the interactive REPL
framelearn
```

Traditional commands are `run`, `ask`, `summarize`, `session`, and `help`. Natural-language parsing uses `TEXT_PROVIDER` + `TEXT_API_KEY` when valid; otherwise it applies local rules and routes most non-video requests to `ask`.

## Actual output

```text
output/<video-stem>/
├── srt_picture.md                 # SRT structure + timestamps + images
├── blog.md                        # complete illustrated transcript in the speaker's voice
├── src/
│   ├── subtitle.txt               # cleaned plain text
│   ├── subtitle.srt               # when timestamped SRT is available
│   ├── frame_00h01m30s.jpg        # heuristic keyframes (timestamped name)
│   ├── extra_frame_xxx.jpg        # precise anchor-pipeline captures
│   ├── subtitle_manifest.json     # subtitle cache validation
│   └── keyframe_manifest.json     # heuristic-frames digest (short-circuits ffmpeg on rerun)
├── temp/                          # DashScope chunks + intermediate ffmpeg frames
└── run-report.json                # aggregated fallbacks / cache hits
```

Existing subtitle, frame, and manifest files act as caches. Subtitle caching follows `[asr]`; keyframe caching follows `[heuristic]`. Remove the cache only when you intentionally want a full rerun.

## Pipeline

```text
CLI / REPL
  → CommandParser
  → CommandRouter
  → VideoPipeline
      → existing subtitle, or FFmpeg audio extraction → ASRAdapter
      → SubtitleCleaner
      → FFmpegHelper
      → KeyframeDeduplicator
      → ChunkedDocGenerator
          → SRTChunker (10 min default) → per-chunk parallel pipeline
          → BlogGenerator → anchor validation / FFmpeg recapture
          → VisionFrameEvaluator → MDAssembler
```

## Anchored blog pipeline (0816 design)

Core flow (from `framelearn_docs/0816.md`):

```text
video
  │
  ├── ASR ──→ raw SRT ────────────────────────┐
  │                                            │
  └── FFmpeg 启发式截帧 ───────────────────────┤
                                               ▼
                                    程序生成 annotated SRT-MD
                                    （保留 raw SRT，不覆盖）
                                               │
                                               ▼
                                          SRTChunker
                                               │
                                               ▼
                                        BlogGenerator（文本模型）
                                        输入：annotated SRT chunk
                                        输出：
                                        ├─ blog_markdown
                                        │   （含 [[FRAME:id@timestamp]]）
                                        └─ frame_requests
                                               │
                                               ▼
                                       程序校验锚点
                                       ├─ 已有帧：绑定真实路径
                                       ├─ 无合适帧：FFmpeg 精准截帧
                                       └─ 非法锚点：删除并报告
                                               │
                                               ▼
                                   VisionFrameEvaluator（视频模型）
                                   对每张候选帧输出：
                                   ├─ anchor_id
                                   ├─ retake / retake_timestamp
                                   ├─ keep_image
                                   ├─ content_type
                                   ├─ caption
                                   └─ text_representation
                                               │
                                    retake=true → 循环补截
                                    retake=false → 进入拼装
                                               │
                                               ▼
                                          MDAssembler
                                               │
                        ┌──────────────────────┼──────────────────────────────────────────┐
                        ▼                      ▼                                          ▼
              结构图/公式/表格          text_slide/terminal                       face/blank/transition
              插图 + caption  插图 + caption（如有）+ text_representation（如有）         删除锚点
```

> The implementation uses the confirmed option A: chunk raw SRT first, then insert candidate frame markers into each chunk. The annotated SRT-MD above is what BlogGenerator sees; the raw SRT on disk is never overwritten.

### VisionFrameEvaluator fields

| Field | Meaning | Rule |
|------|------|----------|
| `anchor_id` | Anchor id referenced by `[[FRAME:id@timestamp]]` | Binds blog text to a frame |
| `retake` | Ask for a new capture | When true, FFmpeg captures `retake_timestamp` and the vision model re-evaluates |
| `retake_timestamp` | Precise retake time | Only used when `retake=true` |
| `keep_image` | Keep the image | `true` keeps the image; `false` removes the whole anchor |
| `content_type` | Visual content kind | `text_slide` / `terminal` / `code` / `diagram` / `formula` / `table` / `screenshot` / `face` / `blank` / `transition` / `other` |
| `caption` | Image caption | Inserted below a kept image when non-empty |
| `text_representation` | Text content of the image | Inserted below the caption when non-empty |

### BlogGenerator anchor example

```text
卷积层负责提取局部特征。
[[FRAME:a1@53.0]]

实际工程中更常用 3x3 卷积。
[[FRAME:a2@633.8]]
```

Anchor validation rules:

```text
Existing frame within frame_match_tolerance → reuse it
Existing frame too far away → FFmpeg captures the precise timestamp
No existing frame → FFmpeg captures the precise timestamp
Invalid anchor → remove it and record it in run-report.json
```


## Test

```bash
uv run pytest
```

## Documentation

- [Documentation status](docs/README.md)
- [Current architecture](docs/architecture.en.md)
- [Pipeline implementation](docs/pipeline-overview.md)
- [AntiVibe technical report (Chinese)](docs/antivibe-technical-report.md)
- Documentation links will be restored after the current pipeline refactor.

## License

MIT
