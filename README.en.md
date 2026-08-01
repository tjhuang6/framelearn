# FrameLearn

中文 | [English](README.en.md)

An AI agent that converts programming tutorial videos (Bilibili / YouTube) into structured, step-by-step text-and-image documents — so you can learn at your own pace and ask AI questions when you get stuck.

## What It Does

1. **Downloads** a video from Bilibili or YouTube via URL
2. **Analyzes** the video structure autonomously (intro, setup, core code, testing, summary)
3. **Extracts** key frames at meaningful moments (code changes, errors, test results)
4. **Generates** a Markdown tutorial with screenshots and step-by-step explanations
5. **Answers questions** about the video content interactively

## Example

```bash
framelearn run "https://www.bilibili.com/video/BV1xx411c7mD"
```

Output: `output/tutorial.md` — a complete image-and-text tutorial with code blocks, screenshots, and section headings.

## Architecture

```
FrameLearn
├── Planner Agent       # Analyzes video structure, creates a conversion plan
├── Tool Executor       # Calls yt-dlp, ffmpeg, OCR as needed
├── Content Analyzer    # Detects key frames, identifies code, segments chapters
├── Document Generator  # Produces structured Markdown output
└── QA Module           # Answers user questions based on video content
```

## Tech Stack

- **Claude API** (Anthropic) — agent orchestration, content analysis, QA
- **yt-dlp** — video downloading (YouTube & Bilibili)
- **ffmpeg** — frame extraction and video processing
- **Tesseract / pytesseract** — OCR for code recognition in frames
- **LangChain** — tool calling framework and agent loop
- **Python 3.11+**

## Quickstart

```bash
# 1. Clone the repo
git clone https://github.com/yourname/framelearn.git
cd framelearn

# 2. Install dependencies
uv sync

# 3. Set your API key
export ANTHROPIC_API_KEY=your_key_here

# 4. Run on a video
python -m framelearn run "https://www.youtube.com/watch?v=example"
```

## Output Format

Each generated tutorial includes:

- Chapter headings matching the video structure
- Key frame screenshots at critical moments
- Code blocks extracted from the video
- Step-by-step explanations for each segment
- Timestamps linking back to the source video

## Interactive QA

After the tutorial is generated, you can ask questions about it:

```bash
python -m framelearn ask "Why does the author use a virtual environment in step 3?"
```

The agent references the original video content and generated notes to answer accurately.

## Docs

- [Technical Architecture](docs/architecture.en.md)
- [Module Interface Design](docs/modules/)
- [Hello-Agents Study Notes](docs/hello-agents/)
- [Technical Decisions](docs/decisions/)

## License

MIT
