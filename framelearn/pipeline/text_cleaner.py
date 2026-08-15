"""Clean SRT chunks via text LLM in parallel.

Each chunk is one LLM call. The model is asked to strip a configured set
of filler words while preserving segment ids and timestamps. On failure
the original chunk is returned so the rest of the pipeline can proceed.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Iterable

from framelearn.config import get as config_get
from framelearn.pipeline.run_report import get_reporter
from framelearn.pipeline.srt_chunker import SRTChunk
from framelearn.provider_adapter import (
    ProviderConfig,
    call_llm_async,
    load_text_config,
)


CLEAN_PROMPT_TEMPLATE = """你是字幕清洗助手，只删口水词，不重组句序，不删内容词。

口水词清单（务必删除）：
{filler_words}

约束：
- 保持每个 segment 的 id 和时间戳不变，ONLY 修改 text
- 不要拆分/合并 segment
- 不要删除内容词（专业术语、名词等）
- 如果一段全是口水词，返回 text 为空字符串

输入 SRT（id\\tstart_sec\\tend_sec\\ttext 一行一段）：
<subtitle>
{chunk_text}
</subtitle>

输出 JSON（严格格式，不要解释）：
{{"segments": [{{"id": <int>, "text": "<cleaned text>"}}, ...]}}
"""


def _format_chunk(segments: Iterable) -> str:
    """Render SRT segments as TSV — one line per segment.

    Uses the ``id`` field when available (TranscriptSegment does not have
    one — the index in the list is used instead).
    """
    lines = []
    for i, seg in enumerate(segments, start=1):
        start = getattr(seg, "start", None) or 0.0
        end = getattr(seg, "end", None) or 0.0
        text = getattr(seg, "text", "")
        lines.append(f"{i}\t{start:.3f}\t{end:.3f}\t{text}")
    return "\n".join(lines)


def _filler_regex(filler_words: list[str]) -> re.Pattern:
    """Build a regex that matches any filler word as a whole token."""
    # Sort longest first so multi-char fillers are matched before their
    # substrings (e.g. "就是说" before "就是说啊").
    sorted_words = sorted(set(filler_words), key=len, reverse=True)
    escaped = [re.escape(w) for w in sorted_words]
    # \b boundary doesn't help for Chinese — use lookbehind/lookahead for
    # non-CJK or start/end of string. Keep it simple: match filler words
    # surrounded by Chinese chars or string edges.
    return re.compile("|".join(escaped))


def _strip_fillers_locally(text: str, pattern: re.Pattern) -> str:
    """Last-resort local strip — used as a fallback when the LLM is down."""
    cleaned = pattern.sub("", text)
    # Tidy double spaces / leading commas left behind by the removal.
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"^[,\s]+|[,\s]+$", "", cleaned)
    return cleaned


def _parse_llm_response(raw: str, expected_count: int) -> list[dict] | None:
    """Try to extract a list of {id, text} dicts from the LLM response.

    Returns None when parsing fails — caller should retry / fall back.
    """
    if not raw:
        return None
    # Strip ```json fences if the model added them.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    candidate = fenced.group(1) if fenced else raw
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Try to find the JSON object inside the response.
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    segments = data.get("segments")
    if not isinstance(segments, list):
        return None
    if len(segments) != expected_count:
        return None
    for item in segments:
        if not isinstance(item, dict):
            return None
        if "id" not in item or "text" not in item:
            return None
    return segments


@dataclass
class TextCleaner:
    """Strip filler words from SRT chunks via text LLM calls.

    Uses settings.toml ``[text_clean] filler_words`` and ``[chunking]
    concurrency``. The text LLM is loaded via ``load_text_config()`` which
    reads ``[text] provider`` and ``[text] model``.
    """

    config: ProviderConfig
    filler_words: list[str]
    concurrency: int
    max_retries: int

    def __init__(
        self,
        config: ProviderConfig | None = None,
        filler_words: list[str] | None = None,
        concurrency: int | None = None,
        max_retries: int = 2,
    ):
        self.config = config or load_text_config()
        self.filler_words = (
            filler_words
            if filler_words is not None
            else config_get("text_clean.filler_words", [])
        )
        self.concurrency = (
            concurrency
            if concurrency is not None
            else int(config_get("chunking.concurrency", 5))
        )
        self.max_retries = max_retries

    async def clean_chunk(self, chunk: SRTChunk) -> SRTChunk:
        """Clean a single chunk. Returns original segments on final failure.

        The returned SRTChunk shares ``index``/``start_sec``/``end_sec``
        with the input. Its ``segments`` are the input segments with
        ``text`` replaced by the cleaned version (or the locally
        regex-stripped version if the LLM fails AND we have a fallback).
        """
        if not chunk.segments:
            return chunk

        prompt = CLEAN_PROMPT_TEMPLATE.format(
            filler_words="、".join(self.filler_words),
            chunk_text=_format_chunk(chunk.segments),
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await call_llm_async(
                    prompt, self.config, max_tokens=4096, timeout=120
                )
                parsed = _parse_llm_response(response, len(chunk.segments))
                if parsed is None:
                    raise ValueError("LLM response did not match expected schema")
                # Build id → cleaned text map, preserving order.
                cleaned_texts = [item["text"] for item in parsed]
                new_segments = []
                for seg, cleaned_text in zip(chunk.segments, cleaned_texts):
                    # dataclasses.replace would be cleaner but the segment
                    # may be a plain object — setattr keeps it generic.
                    setattr(seg, "text", cleaned_text)
                    new_segments.append(seg)
                return SRTChunk(
                    index=chunk.index,
                    start_sec=chunk.start_sec,
                    end_sec=chunk.end_sec,
                    segments=new_segments,
                )
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    # Exponential backoff: 1s, 2s, 4s …
                    await asyncio.sleep(2 ** attempt)

        # All retries failed — fall back to local regex stripping.
        pattern = _filler_regex(self.filler_words)
        for seg in chunk.segments:
            setattr(seg, "text", _strip_fillers_locally(seg.text or "", pattern))
        get_reporter().record_fallback(
            "text_cleaner.chunk_fallback",
            f"chunk {chunk.index} 文本 LLM 失败（{last_error}），已用本地正则降级",
        )
        return SRTChunk(
            index=chunk.index,
            start_sec=chunk.start_sec,
            end_sec=chunk.end_sec,
            segments=chunk.segments,
        )

    async def clean_all(self, chunks: list[SRTChunk]) -> list[SRTChunk]:
        """Clean all chunks concurrently, bounded by ``self.concurrency``.

        A single chunk's failure (after retries) MUST NOT abort the others;
        we use ``return_exceptions=True`` so we can substitute fallbacks.
        """
        if not chunks:
            return []

        sem = asyncio.Semaphore(self.concurrency)

        async def _run(chunk: SRTChunk) -> SRTChunk:
            async with sem:
                return await self.clean_chunk(chunk)

        results = await asyncio.gather(
            *(_run(c) for c in chunks), return_exceptions=False
        )
        return results