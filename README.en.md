# FrameLearn

[中文](README.md) | English

FrameLearn converts local programming tutorial videos into Markdown learning material with timestamped keyframes. The current implementation covers audio extraction, ASR, subtitle cleaning, frame extraction/deduplication, time-based segmented generation, and general-purpose Q&A through Codex app-server.

## Current capabilities

- Processes local `.mp4`, `.mkv`, `.avi`, `.mov`, `.flv`, `.wmv`, and `.webm` files.
- Uses the video's audio stream or finds a companion `.mp3`, `.m4a`, or `.aac` file for split Bilibili downloads.
- Supports two ASR backends:
  - Aliyun DashScope for chunked long-audio transcription, OSS upload, async polling, checkpoints, and SRT timestamps.
  - SiliconFlow SenseVoice for simpler transcription without timestamps.
- Accepts an existing `.txt`, `.srt`, or `.vtt` file through `--subtitle` to skip ASR.
- Combines FFmpeg scene detection and fixed-interval frames, then deduplicates with perceptual hashing.
- Automatically segments and caches generation for long subtitles or large frame sets.
- Always produces `notes.md` and also produces `index.md` in the configured `visual_script`, `notes`, or `textbook` mode.
- Routes `ask` through Codex app-server or a compatible text API.

## Current limitations

- YouTube and Bilibili URLs are validated, but downloading is not implemented. Download the video first.
- `summarize` only prints instructions for an external `/summarize-learning` skill.
- `ask` is a general workspace conversation, not a tutorial-grounded RAG implementation.
- FrameLearn's current app-server turn sends text only. Use `runtime.vision_mode = "api"` when document generation must inspect image pixels.
- Agent keyframe selection is experimental. Its API image-evaluation branch references a `ProviderAdapter` class that does not currently exist; keep it disabled.

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

The repository's current `settings.toml` defaults are:

```toml
[runtime]
text_mode = "appserver"
vision_mode = "api"
vision_provider = "siliconflow"
vision_model = "Qwen/Qwen3.6-35B-A3B"

[asr]
provider = "dashscope"
model = "qwen-audio-3.0-asr-flash-filetrans"

[doc_generation]
mode = "visual_script"
segment_duration = 90
max_keyframes_per_segment = 10

[agent]
keyframe_selection = false
quality_review = false
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
├── index.md
├── notes.md
├── src/
│   ├── subtitle.txt
│   ├── subtitle.srt               # when timestamped SRT is available
│   └── frame_00h01m30s.jpg
├── segments_<mode>/               # generated for segmented runs
└── temp/                          # DashScope chunks when configured to keep them
```

Existing subtitle, frame, and segment files act as caches. Remove the relevant cache only when you intentionally want a full rerun.

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
