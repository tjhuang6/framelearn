"""Aliyun Dashscope paraformer-v2 ASR backend with OSS audio staging."""

import concurrent.futures
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from framelearn.config import get as config_get


# ── Data structures ──────────────────────────────────────────────

@dataclass
class AudioChunk:
    index: int
    path: Path
    start_sec: float
    duration_sec: float
    oss_key: Optional[str] = None
    signed_url: Optional[str] = None


# ── SRT helpers ──────────────────────────────────────────────────

def _seconds_to_srt_time(seconds: float) -> str:
    ms = int(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        if seg.start is None or seg.end is None:
            continue
        start = _seconds_to_srt_time(seg.start)
        end = _seconds_to_srt_time(seg.end)
        lines.append(f"{i}\n{start} --> {end}\n{seg.text}\n")
    return "\n".join(lines)


# ── Main backend ─────────────────────────────────────────────────

class DashscopeBackend:
    API_BASE = "https://dashscope.aliyuncs.com/api/v1"

    def __init__(self, api_key: str):
        if not api_key or api_key.startswith("your_"):
            raise ValueError(
                "DASHSCOPE_API_KEY not configured in .env\n"
                "Get your key at: https://dashscope.aliyun.com/"
            )
        self.api_key = api_key
        self.model = config_get("asr.model", "paraformer-v2")
        self.language_hints = config_get("asr.language_hints", ["zh", "en"])
        self.disfluency_removal = config_get("asr.disfluency_removal", False)
        self.chunk_duration = config_get("asr.chunk_duration", 1800)
        self.poll_interval = config_get("asr.poll_interval", 5)
        self.poll_timeout = config_get("asr.poll_timeout", 1800)
        self.oss_prefix = config_get("asr.oss.prefix", "framelearn-audio/")
        self.oss_url_ttl = config_get("asr.oss.url_ttl", 86400)

    def transcribe(self, audio_path: Path):
        """Full pipeline: split → upload → submit → poll → merge → cleanup."""
        from framelearn.pipeline.asr_adapter import TranscriptResult, TranscriptSegment
        from framelearn.pipeline.asr_backends.oss_client import OssClient

        import tempfile
        temp_dir = Path(tempfile.mkdtemp(prefix="framelearn_asr_"))

        try:
            # 1. Split audio into chunks
            print(f"✂️  切分音频（每段 {self.chunk_duration // 60} 分钟）...")
            chunks = self._split_audio(audio_path, temp_dir)
            print(f"   共 {len(chunks)} 段")

            # 2. Upload all chunks to OSS and submit tasks in parallel
            oss = OssClient()
            print("⬆️  并行上传 OSS + 提交识别任务...")
            task_map: dict[str, AudioChunk] = {}

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                futures = {
                    executor.submit(self._upload_and_submit, chunk, oss): chunk
                    for chunk in chunks
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        chunk, task_id = future.result()
                        task_map[task_id] = chunk
                        print(f"   ✅ 段 {chunk.index + 1}/{len(chunks)} 已提交（task: {task_id[:8]}...）")
                    except Exception as e:
                        chunk = futures[future]
                        print(f"   ❌ 段 {chunk.index + 1} 提交失败：{e}")

            if not task_map:
                raise RuntimeError("所有分段均提交失败")

            # 3. Poll all tasks
            print(f"⏳ 等待识别完成（共 {len(task_map)} 个任务）...")
            results: list[tuple[AudioChunk, dict]] = []
            for task_id, chunk in task_map.items():
                try:
                    result = self._poll_task(task_id)
                    results.append((chunk, result))
                    print(f"   ✅ 段 {chunk.index + 1} 识别完成")
                except Exception as e:
                    print(f"   ❌ 段 {chunk.index + 1} 识别失败：{e}")

            if not results:
                raise RuntimeError("所有分段均识别失败")

            # 4. Sort by chunk index, merge
            results.sort(key=lambda x: x[0].index)
            all_segments = self._merge_results(results)

            full_text = " ".join(s.text for s in all_segments)
            srt = build_srt(all_segments)

            return TranscriptResult(
                segments=all_segments,
                full_text=full_text,
                has_timestamps=True,
                srt=srt,
            )

        finally:
            # 5. Cleanup OSS + temp files
            self._cleanup(chunks if 'chunks' in dir() else [], oss if 'oss' in dir() else None)
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    # ── Audio splitting ─────────────────────────────────────────

    def _split_audio(self, audio_path: Path, temp_dir: Path) -> list[AudioChunk]:
        """Split audio into chunks using ffprobe + ffmpeg."""
        # Get total duration
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, check=True,
        )
        total_duration = float(result.stdout.strip())

        chunks = []
        start = 0.0
        index = 0

        while start < total_duration:
            duration = min(self.chunk_duration, total_duration - start)
            out_path = temp_dir / f"chunk_{index:03d}.m4a"

            subprocess.run(
                ["ffmpeg", "-i", str(audio_path),
                 "-ss", str(start), "-t", str(duration),
                 "-c", "copy", "-y", str(out_path)],
                check=True, capture_output=True,
            )

            chunks.append(AudioChunk(
                index=index,
                path=out_path,
                start_sec=start,
                duration_sec=duration,
            ))

            start += self.chunk_duration
            index += 1

        return chunks

    # ── OSS + task submission ────────────────────────────────────

    def _upload_and_submit(self, chunk: AudioChunk, oss) -> tuple[AudioChunk, str]:
        """Upload one chunk to OSS and submit recognition task."""
        run_id = uuid.uuid4().hex[:8]
        object_key = f"{self.oss_prefix}{run_id}/chunk_{chunk.index:03d}.m4a"

        # Upload
        oss.upload(str(chunk.path), object_key)
        chunk.oss_key = object_key

        # Sign URL
        signed_url = oss.sign_url(object_key, self.oss_url_ttl)
        chunk.signed_url = signed_url

        # Submit recognition task
        task_id = self._submit_task(signed_url)
        return chunk, task_id

    def _submit_task(self, signed_url: str) -> str:
        """Submit async recognition task, return task_id."""
        response = httpx.post(
            f"{self.API_BASE}/services/audio/asr/transcription",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-DashScope-Async": "enable",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "input": {"file_urls": [signed_url]},
                "parameters": {
                    "timestamp_alignment_enabled": True,
                    "diarization_enabled": False,
                    "disfluency_removal_enabled": self.disfluency_removal,
                    "language_hints": self.language_hints,
                },
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        task_id = data.get("output", {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in response: {data}")
        return task_id

    # ── Polling ──────────────────────────────────────────────────

    def _poll_task(self, task_id: str) -> dict:
        """Poll until task SUCCEEDED or FAILED, return result dict."""
        deadline = time.monotonic() + self.poll_timeout

        while time.monotonic() < deadline:
            response = httpx.get(
                f"{self.API_BASE}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()
            output = data.get("output", {})
            status = output.get("task_status", "")

            if status == "SUCCEEDED":
                # Download result JSON
                result_url = output.get("results", [{}])[0].get("transcription_url")
                if not result_url:
                    raise RuntimeError(f"No transcription_url in task result: {output}")
                result_resp = httpx.get(result_url, timeout=30.0)
                result_resp.raise_for_status()
                return result_resp.json()

            if status == "FAILED":
                raise RuntimeError(
                    f"Task {task_id} failed: {output.get('message', 'unknown error')}"
                )

            time.sleep(self.poll_interval)

        raise TimeoutError(
            f"Task {task_id} timed out after {self.poll_timeout}s"
        )

    # ── Merging ──────────────────────────────────────────────────

    def _merge_results(self, results: list[tuple]) -> list:
        """Merge per-chunk results into a flat segment list with time offsets."""
        from framelearn.pipeline.asr_adapter import TranscriptSegment

        all_segments = []

        for chunk, result_json in results:
            transcripts = result_json.get("transcripts", [{}])
            sentences = transcripts[0].get("sentences", []) if transcripts else []

            for sentence in sentences:
                start = (sentence.get("begin_time", 0) / 1000) + chunk.start_sec
                end = (sentence.get("end_time", 0) / 1000) + chunk.start_sec
                all_segments.append(TranscriptSegment(
                    text=sentence.get("text", ""),
                    start=round(start, 3),
                    end=round(end, 3),
                ))

        return all_segments

    # ── Cleanup ──────────────────────────────────────────────────

    def _cleanup(self, chunks: list[AudioChunk], oss):
        """Delete OSS objects. Best-effort, no exceptions raised."""
        if oss is None:
            return
        for chunk in chunks:
            if chunk.oss_key:
                oss.delete(chunk.oss_key)
