# Current Technical Architecture

This document describes the code that is currently executable in the repository. See [Documentation Status](README.md) for the status of historical plans and proposals.

## Scope

FrameLearn is currently a local CLI application. It implements local video processing, optional subtitle reuse, DashScope/SiliconFlow ASR, subtitle cleaning, FFmpeg frame extraction, perceptual-hash deduplication, segmented Markdown generation, and general Codex/API Q&A.

It does not currently implement online video downloading, a Planner Agent, OCR-based Content Analyzer, Chroma RAG, an internal learning-summary workflow, or structured image inputs to Codex app-server.

## Entry and routing

```text
framelearn command / REPL
  → framelearn.__main__
  → CommandParser
  → CommandRouter
      ├── run       → VideoPipeline
      ├── ask       → RuntimeAdapter or text provider API
      ├── summarize → external-skill instruction only
      └── help
```

`framelearn.__main__` supports one CLI flag, `--subtitle <path>`. Traditional commands (`run`, `ask`, `summarize`, `help`) pass through unchanged. Natural-language classification uses `TEXT_PROVIDER` and `TEXT_API_KEY` when valid; otherwise local rules route video sources to `run`, summary keywords to `summarize`, and other input to `ask`.

The router validates YouTube/Bilibili URLs but reports that downloading is not implemented. Local videos are validated before `VideoPipeline` is created.

## Video pipeline

```text
local video + optional subtitle
  → existing/cached subtitle, or FFmpeg audio extraction → ASRAdapter
  → SubtitleCleaner → subtitle.txt (+ subtitle.srt when available)
  → cached frames, or scene detection + fixed-interval extraction
  → KeyframeDeduplicator
  → optional AgentKeyframeSelector
  → DocumentGenerator → notes.md + index.md
```

`PipelineResult` contains the output directory, main Markdown path, final frame paths, cleaned subtitle text, and an optional error.

## ASR

`ASRAdapter` reads `asr.provider`:

| Provider | Timestamps | Current flow |
|---|---:|---|
| `dashscope` | Yes | chunk → OSS upload → async tasks → poll → merge → SRT |
| `siliconflow` | No | upload the complete audio file → plain text |

DashScope stores chunks and `asr_checkpoint.json` under `output/<video>/temp`, restores completed tasks, adds each chunk's starting offset to timestamps, and attempts to delete uploaded OSS objects. It needs `DASHSCOPE_API_KEY`, `OSS_ACCESS_KEY_ID`, and `OSS_ACCESS_KEY_SECRET`.

SiliconFlow retries rate limits and failures but produces no SRT, so downstream alignment falls back to a character-rate estimate.

## Frame extraction

`FFmpegHelper.extract_keyframes()` always performs both scene detection and fixed-interval extraction; the latter is not conditional on scene detection failure. Frames are merged and renamed with whole-second timestamps.

`KeyframeDeduplicator` compares a 64-bit pHash against every retained frame and discards frames whose normalized similarity is greater than 0.9. This is a greedy, approximately O(n²) pass.

`AgentKeyframeSelector` is disabled by default. It augments the existing frame set rather than replacing it. Its direct-API image path currently references a missing `ProviderAdapter` class, while the app-server path sends text only, so it should be treated as experimental.

## Document generation

`DocumentGenerator` supports `visual_script`, `notes`, and `textbook`. The current `notes` prompt asks for connected technical-blog prose rather than bullet points.

Segmentation is enabled only when the cleaned subtitle is longer than 8,000 characters or there are more than 20 frames. With SRT, chunks are grouped by timestamps; otherwise the splitter estimates time at four characters per second. Each segment is cached under `segments_<mode>/`, retried up to three times for generation errors, and merged into the final document.

When `agent.quality_review = true`, local heuristics check minimum length, filler-word frequency, and missing image references. A failed draft is regenerated up to three times and then falls back to the original subtitle. There is no separate LLM reviewer in the current implementation.

| Vision mode | Current behavior |
|---|---|
| `api` | Sends prompt and base64-encoded local images through `provider_adapter.call_llm()` |
| `appserver` | Starts a Codex app-server session but sends only text; frame names appear in the prompt |

## App-server subsystem

```text
RuntimeAdapter
  → AppServerSession
      → JsonRpcStdioClient → codex app-server
      → EventProjector
  → SessionDB
```

- `JsonRpcStdioClient` owns newline-delimited JSON-RPC and subprocess I/O.
- `AppServerSession` owns initialize/thread/turn lifecycle, approvals, watchdogs, interruption, and retirement.
- `EventProjector` maps completed Codex events to persistent message objects.
- `RuntimeAdapter` persists the user message before a turn and projected messages afterward, and retries once with a new session after retirement.
- The default approval policy declines requests unless the host supplies a callback.

## Configuration sources

| Configuration | Source |
|---|---|
| runtime, video, ASR, document, and agent settings | `settings.toml` via cached `framelearn.config` |
| API and OSS credentials | `.env` / process environment |
| text API provider/model/base URL | `TEXT_*` environment variables |
| document vision provider/model | `runtime.vision_*` in `settings.toml` |
| Codex model/authentication | local Codex CLI configuration |

`load_config()` loads `.env` into the process environment but does not merge those keys into the returned TOML dictionary.

## Output and cache

```text
output/<video-stem>/
├── index.md
├── notes.md
├── src/
│   ├── subtitle.txt
│   ├── subtitle.srt
│   └── frame_HHhMMmSSs.jpg
├── segments_<mode>/
└── temp/
```

Caches are existence-based and do not include hashes of the source video, configuration, prompt, or model. Changing those inputs may still reuse old subtitle, frame, or segment files unless the relevant cache is removed manually.

## Verification boundary

The test suite covers routing, cleaning, splitter behavior, provider mocks, prompt selection, quality retries, keyframe-selection logic, and app-server protocol/persistence. It does not provide online integration tests for real videos, DashScope/OSS, the Vision API, or a complete end-to-end run.
