# FrameLearn

[中文](README.md) | English

FrameLearn converts local programming tutorial videos into Markdown learning material with timestamped keyframes. The current implementation covers audio extraction, ASR, subtitle cleaning, chunked (30 min) LLM calls, heuristic + vision two-stage keyframe selection, dual Markdown output, and general-purpose Q&A through Codex app-server.

## Current capabilities

- Processes local `.mp4`, `.mkv`, `.avi`, `.mov`, `.flv`, `.wmv`, and `.webm` files.
- Uses the video's audio stream or finds a companion `.mp3`, `.m4a`, or `.aac` file for split Bilibili downloads.
- Supports two ASR backends:
  - Aliyun DashScope for chunked long-audio transcription, OSS upload, async polling, checkpoints, and SRT timestamps.
  - SiliconFlow SenseVoice for simpler transcription without timestamps.
- Accepts an existing `.txt`, `.srt`, or `.vtt` file through `--subtitle` to skip ASR.
- **Chunked LLM document generation**: SRT is split into 30-minute chunks (`[chunking] segment_minutes`); each chunk is sent once to the text LLM for filler removal, then a Qwen3-VL vision model runs two stages (text+images to pick timestamps, then image-only to drop redundant/noisy frames). A 30-min video uses ≤ 3 LLM calls regardless of length.
- Always produces two Markdown files: `srt_picture.md` (preserves SRT structure, timestamps + embedded images) and `blog.md` (blog-style narrative + the same images).
- Heuristic frame extraction (FFmpeg scene detection + pHash dedup) is summarized by SHA256 in the manifest, so a config change or a different frame set invalidates the cache automatically.
- Routes `ask` through Codex app-server or a compatible text API.

## Current limitations

- YouTube and Bilibili URLs are validated, but downloading is not implemented. Download the video first.
- `summarize` only prints instructions for an external `/summarize-learning` skill.
- `ask` is a general workspace conversation, not a tutorial-grounded RAG implementation.
- FrameLearn's current app-server turn sends text only. Use `runtime.vision_mode = "api"` when document generation must inspect image pixels.
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
[runtime]
text_mode = "appserver"
vision_mode = "api"
vision_provider = "siliconflow"
vision_model = "Qwen/Qwen3.6-35B-A3B"

[asr]
provider = "dashscope"
model = "qwen-audio-3.0-asr-flash-filetrans"

[chunking]
segment_minutes = 30          # max video duration per chunk
max_images_per_chunk = 50     # max frames kept per chunk
concurrency = 5               # max in-flight LLM calls per stage

[text_clean]
filler_words = ["那么", "就是说", "大家注意", "咱们", "啊", "嗯", "这个", "那个", "对吧"]

[heuristic]
scene_threshold = 0.4         # FFmpeg scene-detection threshold (lower = more sensitive)
similarity_threshold = 0.95   # pHash dedup threshold
max_frames = 200

[doc_gen]
srt_filename = "srt_picture.md"   # SRT structure + images
blog_filename = "blog.md"         # blog-style narrative + same images
```

With those defaults, configure at least:

```bash
DASHSCOPE_API_KEY=...
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
SILICONFLOW_API_KEY=...
```

`text_mode = "appserver"` also requires an installed and configured `codex` CLI. See [`settings.toml`](settings.toml) and [`.env.example`](.env.example) for all fields.

## Run

```bash
# Local video
framelearn run /absolute/path/tutorial.mp4

# Reuse an existing subtitle and skip ASR
framelearn run /absolute/path/tutorial.mp4 --subtitle /absolute/path/tutorial.srt

# Natural-language entry point
framelearn "Process this video /absolute/path/tutorial.mp4"

# General-purpose workspace Q&A
framelearn ask "Explain this project architecture"

# Start the interactive REPL
framelearn
```

Traditional commands are `run`, `ask`, `summarize`, and `help`. Natural-language parsing uses `TEXT_PROVIDER` + `TEXT_API_KEY` when valid; otherwise it applies local rules and routes most non-video requests to `ask`.

## Actual output

```text
output/<video-stem>/
├── srt_picture.md                 # SRT structure + timestamps + images
├── blog.md                        # blog-style narrative + same images
├── src/
│   ├── subtitle.txt               # cleaned plain text
│   ├── subtitle.srt               # when timestamped SRT is available
│   ├── frame_00h01m30s.jpg        # heuristic keyframes (timestamped name)
│   ├── extra_frame_xxx.jpg        # Stage1-captured extra frames
│   ├── subtitle_manifest.json     # subtitle cache validation
│   └── keyframe_manifest.json     # heuristic-frames digest (short-circuits ffmpeg on rerun)
├── temp/                          # DashScope chunks + intermediate ffmpeg frames
└── run-report.json                # aggregated fallbacks / cache hits
```

Existing subtitle, frame, and manifest files act as caches. Config changes (`[chunking]`, `[text_clean]`, `[doc_gen]`, `[heuristic]`) or a different frame set automatically invalidate the relevant cache. Remove the cache only when you intentionally want a full rerun.

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
      → optional AgentKeyframeSelector
      → DocumentGenerator
          → one-shot generation for small inputs
          → SegmentSplitter + cache + retry + merge for large inputs
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
- [Codex app-server guide (Chinese)](docs/codex-app-server-guide.md)

## License

MIT
