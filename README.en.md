# FrameLearn

[中文](README.md) | English

An AI agent that converts programming tutorial videos into structured Markdown tutorials — so you can learn at your own pace and ask AI questions when you get stuck.

## What It Does

1. **Transcribes**: Extracts audio and transcribes via SiliconFlow SenseVoice
2. **Extracts frames**: FFmpeg scene detection + fallback timing, deduplicated with perceptual hashing
3. **Generates**: Keyframes + subtitles → structured Markdown tutorial via GPT / Claude
4. **Q&A**: Ask questions about the video content at any time

## Output Format

```
output/video-name/
  index.md          # Tutorial (chapters, key points, code examples, frame references)
  src/
    frame_001.jpg   # Keyframe screenshots
    frame_002.jpg
    ...
    subtitle.txt    # Cleaned subtitle text
```

## Quickstart

### 1. Install dependencies

```bash
# Install FFmpeg (required)
brew install ffmpeg          # macOS
# apt install ffmpeg         # Ubuntu

# Install Python dependencies
uv sync
```

### 2. Configure

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

Minimum required:

```bash
# Text / vision model (via Codex app-server, no extra key needed)
# Or configure a direct API key
TEXT_PROVIDER=deepseek
TEXT_API_KEY=your_deepseek_key_here

# ASR speech-to-text (SiliconFlow, accessible from mainland China)
SILICONFLOW_API_KEY=your_siliconflow_key_here
```

Runtime mode and video parameters are configured in `settings.toml`:

```toml
[runtime]
text_mode = "appserver"   # appserver (via Codex) or api (direct call)
vision_mode = "appserver"

[video]
output_dir = "./output"
scene_threshold = 0.3     # Scene change sensitivity
max_keyframes = 100
```

### 3. Run

```bash
# Natural language (recommended)
framelearn "Process this video /path/to/tutorial.mp4"
framelearn "What does chapter 3 cover?"

# Traditional command format
framelearn run /path/to/tutorial.mp4
framelearn ask "Why use a virtual environment?"
framelearn summarize
framelearn help
```

## Bilibili Videos

Bilibili downloads often split video and audio into separate files:

```
tutorial-30080.mp4   # Video stream (no audio)
tutorial-30280.mp3   # Audio stream
```

FrameLearn automatically detects and pairs companion audio files in the same directory — no manual merging needed.

## Architecture

```
User input
  ↓
CommandParser (natural language intent recognition)
  ↓
CommandRouter (command dispatch)
  ↓
VideoPipeline
  ├── FFmpegHelper      Audio extraction + keyframe extraction
  ├── ASRAdapter        Speech-to-text (SiliconFlow SenseVoice)
  ├── SubtitleCleaner   Subtitle cleaning (ported from Bilitato)
  ├── KeyframeDedup     Perceptual hash deduplication
  └── DocumentGenerator Markdown tutorial generation
```

## Tech Stack

| Purpose | Tool |
|---------|------|
| Audio/video processing | FFmpeg |
| Speech recognition | SiliconFlow FunAudioLLM/SenseVoiceSmall |
| Vision / text analysis | Codex app-server (GPT-5.6) or direct API |
| Keyframe deduplication | imagehash (perceptual hashing) |
| Configuration | settings.toml + .env |
| Runtime | Python 3.11+, uv |

## Docs

- [Technical Architecture](docs/architecture.md)
- [Video Pipeline Design](openspec/changes/video-pipeline/design.md)
- [Bilitato Migration Decisions](docs/decisions/bilitato-to-framelearn.md)
- [App-Server Multimodal Pipeline](docs/app-server-video-multimodal-pipeline.md)

## License

MIT
