# Technical Architecture

## Overview

FrameLearn is built as a multi-step AI agent system. Rather than a fixed pipeline, each component makes autonomous decisions about what to do next based on the content it observes.

The overall flow:

```
Video URL
   ↓
Planner Agent        ← analyzes structure, creates a plan
   ↓
Tool Executor        ← downloads video, extracts frames, runs OCR
   ↓
Content Analyzer     ← identifies key moments, segments chapters
   ↓
Document Generator   ← produces Markdown tutorial
   ↓
QA Module            ← answers user questions interactively
```

---

## Components

### 1. Planner Agent

Responsible for understanding the video before any processing begins.

- Samples a small number of frames from the video at regular intervals
- Sends them to Claude with the prompt: "Analyze the structure of this programming tutorial. Identify the main sections and what each one covers."
- Outputs a **conversion plan**: a list of chapters, estimated time ranges, and what to focus on in each
- The rest of the system executes against this plan

Why this matters: without a plan, the agent would process every frame blindly. The planner reduces noise and focuses effort on meaningful segments.

### 2. Tool Executor

A thin wrapper that lets the agent call external tools by name. Tools available:

| Tool | Purpose |
|---|---|
| `download_video` | Calls yt-dlp to download from YouTube or Bilibili |
| `extract_frames` | Calls ffmpeg to extract frames at specified timestamps |
| `run_ocr` | Calls Tesseract to extract text from a frame image |
| `detect_scene_changes` | Uses ffmpeg scene detection to find visual transitions |

The agent decides which tools to call and in what order. It does not follow a hardcoded sequence.

### 3. Content Analyzer

Processes the extracted frames and decides which ones are worth including in the tutorial.

Decision criteria:
- **Code change detected**: the code on screen differs from the previous frame
- **Error appeared**: the frame contains a visible error message or traceback
- **Terminal output**: a command was run and output is visible
- **New section**: the video title, slide, or heading changed

Frames that don't meet any criteria are discarded. This keeps the output tight and relevant.

### 4. Document Generator

Takes the selected frames and the conversion plan, and produces the final Markdown document.

For each chapter:
1. Writes a `##` heading
2. Inserts the key frame screenshots as images
3. Generates a step-by-step explanation of what happens in that segment
4. Formats any detected code into fenced code blocks with the correct language tag
5. Adds a timestamp link back to the source video

Output is a single `tutorial.md` file under the `output/` directory.

### 5. QA Module

Allows the user to ask questions after the tutorial is generated.

- Loads the generated tutorial and a summary of the video structure into context
- Accepts a natural language question from the user
- If the question refers to a specific step, the agent re-examines the corresponding frames
- Returns a precise answer grounded in the actual video content

---

## Agent Loop Design

The core agent loop follows a **Plan → Act → Observe → Reflect** cycle:

```
Plan:     Planner Agent produces a chapter-by-chapter conversion plan
Act:      Tool Executor calls yt-dlp, ffmpeg, OCR as directed
Observe:  Content Analyzer reviews the results and flags issues
Reflect:  If a frame is blurry or OCR failed, the agent retries with different parameters
```

This loop runs until the agent determines the output meets quality standards — or it surfaces a failure to the user with a clear explanation.

---

## Self-Critique Mechanism

After the Document Generator produces a draft, a separate critique pass runs:

- Are all chapters from the plan covered?
- Are there any sections with no screenshots?
- Are any code blocks incomplete or cut off?

If issues are found, the agent goes back and fills the gaps before returning the final output.

---

## Technology Choices

**Claude API (Anthropic)**
Used for planning, content analysis, document generation, and QA. Claude's vision capability handles frame analysis; its tool use capability drives the agent loop.

**yt-dlp**
Supports both YouTube and Bilibili with a unified interface. Handles auth, rate limiting, and format selection automatically.

**ffmpeg**
Industry-standard video processing. Used for frame extraction, scene change detection, and thumbnail generation.

**Tesseract / pytesseract**
Extracts text from frames where OCR is needed — particularly for code that Claude's vision might misread due to font rendering.

**LangChain**
Provides the tool-calling scaffolding and agent loop infrastructure so the focus stays on FrameLearn's logic rather than boilerplate.

**uv**
Dependency management. Faster than pip, deterministic installs via `uv.lock`.

---

## Data Flow Diagram

```
┌─────────────┐
│  Video URL  │
└──────┬──────┘
       │
       ▼
┌─────────────────┐     ┌──────────────┐
│  Planner Agent  │────▶│  Claude API  │
│  (sample frames)│     └──────────────┘
└──────┬──────────┘
       │ conversion plan
       ▼
┌─────────────────┐     ┌──────────────┐
│  Tool Executor  │────▶│  yt-dlp      │
│                 │────▶│  ffmpeg      │
│                 │────▶│  Tesseract   │
└──────┬──────────┘     └──────────────┘
       │ frames + OCR text
       ▼
┌──────────────────┐    ┌──────────────┐
│ Content Analyzer │───▶│  Claude API  │
│ (key frame select│    └──────────────┘
└──────┬───────────┘
       │ selected frames
       ▼
┌──────────────────┐    ┌──────────────┐
│ Doc Generator    │───▶│  Claude API  │
└──────┬───────────┘    └──────────────┘
       │
       ▼
┌──────────────────┐
│  tutorial.md     │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐    ┌──────────────┐
│   QA Module      │◀──▶│  Claude API  │
│ (user questions) │    └──────────────┘
└──────────────────┘
```

---

## Directory Structure

```
framelearn/
├── README.md
├── pyproject.toml
├── uv.lock
├── docs/
│   └── architecture.md        # this file
├── framelearn/
│   ├── __init__.py
│   ├── planner.py             # Planner Agent
│   ├── executor.py            # Tool Executor
│   ├── analyzer.py            # Content Analyzer
│   ├── generator.py           # Document Generator
│   ├── qa.py                  # QA Module
│   └── tools/
│       ├── downloader.py      # yt-dlp wrapper
│       ├── extractor.py       # ffmpeg wrapper
│       └── ocr.py             # Tesseract wrapper
└── output/                    # generated tutorials go here
```
