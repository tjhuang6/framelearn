"""Aliyun DashScope ASR backend.

Supports:
  - qwen-audio-3.0-asr-flash-filetrans  (long audio, async, timestamps)
  - paraformer-v2                         (long audio, async, timestamps)

Flow: split audio → upload OSS → submit tasks → poll → merge → SRT → cleanup
"""

import concurrent.futures
import os
import random
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


# ── SRT / VTT helpers ────────────────────────────────────────────

def _seconds_to_srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(segments) -> str:
    blocks = []
    for i, seg in enumerate(segments, 1):
        if seg.start is None or seg.end is None:
            continue
        if not seg.text.strip():
            continue
        blocks.append(
            f"{i}\n"
            f"{_seconds_to_srt_time(seg.start)} --> {_seconds_to_srt_time(seg.end)}\n"
            f"{seg.text.strip()}"
        )
    return "\n\n".join(blocks)


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
        self.model = config_get("asr.model", "qwen-audio-3.0-asr-flash-filetrans")
        self.language_hints = config_get("asr.language_hints", ["zh", "en"])
        self.diarization_enabled = config_get("asr.diarization_enabled", False)
        self.disfluency_removal = config_get("asr.disfluency_removal", False)
        self.chunk_duration = config_get("asr.chunk_duration", 1800)
        self.max_workers = config_get("asr.max_workers", 6)
        self.poll_interval = config_get("asr.poll_interval", 5)
        self.poll_timeout = config_get("asr.poll_timeout", 3600)
        self.vocabulary_id = config_get("asr.vocabulary_id", "")
        self.oss_prefix = config_get("asr.oss.prefix", "framelearn-audio/")
        self.oss_url_ttl = config_get("asr.oss.url_ttl", 86400)

    def _build_parameters(self) -> dict:
        """Build model-specific parameters."""
        if self.model == "qwen-audio-3.0-asr-flash-filetrans":
            params: dict = {
                "channel_id": [0],
                "language_hints": self.language_hints,
                "diarization_enabled": self.diarization_enabled,
            }
            if self.vocabulary_id:
                params["vocabulary_id"] = self.vocabulary_id
            return params

        if self.model == "paraformer-v2":
            return {
                "channel_id": [0],
                "timestamp_alignment_enabled": True,
                "diarization_enabled": self.diarization_enabled,
                "disfluency_removal_enabled": self.disfluency_removal,
                "language_hints": self.language_hints,
            }

        raise ValueError(f"不支持的 DashScope ASR 模型：{self.model}")

    def transcribe(self, audio_path: Path, output_dir: Optional[Path] = None):
        """Full pipeline: split → OSS → submit → poll → merge → cleanup.

        Supports resuming from a previous run via asr_checkpoint.json.

        Args:
            audio_path: Path to the audio file to transcribe
            output_dir: If provided, temp files go to output_dir/temp instead of
                        a random system tmpdir. Kept when asr.keep_temp_files=true.
        """
        from framelearn.pipeline.asr_adapter import TranscriptResult, TranscriptSegment
        from framelearn.pipeline.asr_backends.oss_client import OssClient

        import json
        import shutil
        import tempfile

        keep_temp = config_get("asr.keep_temp_files", False)

        if output_dir is not None:
            temp_dir = Path(output_dir) / "temp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_path = temp_dir / "asr_checkpoint.json"
        else:
            temp_dir = Path(tempfile.mkdtemp(prefix="framelearn_asr_"))
            checkpoint_path = temp_dir / "asr_checkpoint.json"

        chunks: list[AudioChunk] = []

        try:
            # 1. Split (skip if chunks already exist)
            print(f"✂️  切分音频（每段 {self.chunk_duration // 60} 分钟）...")
            chunks = self._split_audio(audio_path, temp_dir)
            print(f"   共 {len(chunks)} 段")

            # 2. Load checkpoint
            checkpoint = self._load_checkpoint(checkpoint_path)
            if checkpoint:
                done = sum(1 for c in checkpoint.values() if c.get("status") == "done")
                print(f"♻️  发现断点记录，已完成 {done}/{len(chunks)} 段，继续上次进度...")

            # 3. Upload + submit (skip already submitted chunks)
            oss = OssClient()
            task_map: dict[str, AudioChunk] = {}

            # Restore completed tasks from checkpoint
            for chunk in chunks:
                key = str(chunk.index)
                entry = checkpoint.get(key, {})
                if entry.get("status") == "done" and entry.get("task_id"):
                    task_map[entry["task_id"]] = chunk
                    print(f"   ⏭️  段 {chunk.index + 1}/{len(chunks)} 已完成，跳过上传")

            # Submit remaining chunks
            pending = [c for c in chunks if str(c.index) not in checkpoint
                       or checkpoint[str(c.index)].get("status") != "done"]

            if pending:
                print(f"⬆️  上传并提交 {len(pending)} 段...")
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {
                        executor.submit(self._upload_and_submit, chunk, oss): chunk
                        for chunk in pending
                    }
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            chunk, task_id = future.result()
                            task_map[task_id] = chunk
                            checkpoint[str(chunk.index)] = {"status": "submitted", "task_id": task_id}
                            self._save_checkpoint(checkpoint_path, checkpoint)
                            print(f"   ✅ 段 {chunk.index + 1}/{len(chunks)} 已提交")
                        except Exception as e:
                            c = futures[future]
                            print(f"   ❌ 段 {c.index + 1} 提交失败：{e}")

            if not task_map:
                raise RuntimeError("所有分段均提交失败")

            # 4. Poll
            print(f"⏳ 等待识别完成（{len(task_map)} 个任务）...")
            results: list[tuple[AudioChunk, dict]] = []
            for task_id, chunk in task_map.items():
                key = str(chunk.index)
                # Already have result in checkpoint?
                if checkpoint.get(key, {}).get("status") == "done" and checkpoint[key].get("result"):
                    results.append((chunk, checkpoint[key]["result"]))
                    continue
                try:
                    raw = self._poll_task(task_id)
                    results.append((chunk, raw))
                    checkpoint[key] = {"status": "done", "task_id": task_id, "result": raw}
                    self._save_checkpoint(checkpoint_path, checkpoint)
                    print(f"   ✅ 段 {chunk.index + 1} 完成")
                except Exception as e:
                    print(f"   ❌ 段 {chunk.index + 1} 识别失败：{e}")

            if not results:
                raise RuntimeError("所有分段均识别失败")

            # 5. Merge
            results.sort(key=lambda x: x[0].index)
            all_segments = self._merge_results(results)

            full_text = " ".join(s.text for s in all_segments)
            srt = build_srt(all_segments)

            # All done — remove checkpoint
            if checkpoint_path.exists():
                checkpoint_path.unlink()

            return TranscriptResult(
                segments=all_segments,
                full_text=full_text,
                has_timestamps=True,
                srt=srt,
            )


        finally:
            self._cleanup(chunks, oss if 'oss' in dir() else None)
            if keep_temp:
                print(f"📁 临时切片文件保留在：{temp_dir}")
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)

    # ── Audio splitting ─────────────────────────────────────────

    def _split_audio(self, audio_path: Path, temp_dir: Path) -> list[AudioChunk]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, check=True,
        )
        total = float(result.stdout.strip())

        chunks = []
        start = 0.0
        index = 0
        while start < total:
            dur = min(self.chunk_duration, total - start)
            out = temp_dir / f"chunk_{index:03d}.m4a"
            subprocess.run(
                ["ffmpeg", "-i", str(audio_path),
                 "-ss", str(start), "-t", str(dur),
                 "-c", "copy", "-y", str(out)],
                check=True, capture_output=True,
            )
            chunks.append(AudioChunk(
                index=index,
                path=out,
                start_sec=start,
                duration_sec=dur,
            ))
            start += dur
            index += 1
        return chunks

    # ── Upload + submit ─────────────────────────────────────────

    def _upload_and_submit(self, chunk: AudioChunk, oss) -> tuple[AudioChunk, str]:
        run_id = uuid.uuid4().hex[:8]
        key = f"{self.oss_prefix}{run_id}_{chunk.path.name}"
        oss.upload(str(chunk.path), key)
        chunk.oss_key = key
        signed_url = oss.sign_url(key, self.oss_url_ttl)
        chunk.signed_url = "***"  # never log the real URL
        task_id = self._submit_task(signed_url)
        return chunk, task_id

    def _submit_task(self, signed_url: str) -> str:
        payload = {
            "model": self.model,
            "input": {"file_urls": [signed_url]},
            "parameters": self._build_parameters(),
        }
        resp = httpx.post(
            f"{self.API_BASE}/services/audio/asr/transcription",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            },
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        task_id = body.get("output", {}).get("task_id")
        if not task_id:
            raise RuntimeError(
                f"DashScope 响应中没有 task_id："
                f"request_id={body.get('request_id')}, status={resp.status_code}"
            )
        return task_id

    # ── Polling ─────────────────────────────────────────────────

    def _poll_task(self, task_id: str) -> dict:
        deadline = time.monotonic() + self.poll_timeout
        transient_failures = 0

        while time.monotonic() < deadline:
            try:
                resp = httpx.get(
                    f"{self.API_BASE}/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=15.0,
                )
                if resp.status_code in (429,) or resp.status_code >= 500:
                    transient_failures += 1
                    wait = min(30, self.poll_interval * (2 ** min(transient_failures, 3)))
                    time.sleep(wait + random.random())
                    continue

                resp.raise_for_status()
                body = resp.json()
                output = body.get("output", {})
                status = output.get("task_status", "")

                if status == "SUCCEEDED":
                    results = output.get("results") or []
                    if not results:
                        raise RuntimeError(f"任务 {task_id} 成功但 results 为空")
                    trans_url = results[0].get("transcription_url")
                    if not trans_url:
                        raise RuntimeError(f"任务 {task_id} 缺少 transcription_url")
                    return self._download_result(trans_url)

                if status == "FAILED":
                    raise RuntimeError(
                        f"任务 {task_id} 失败：{output.get('message', 'unknown')}"
                    )

            except (httpx.TimeoutException, httpx.NetworkError):
                pass

            time.sleep(self.poll_interval)

        raise TimeoutError(f"任务 {task_id} 超时（{self.poll_timeout}s）")

    def _download_result(self, url: str) -> dict:
        resp = httpx.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.json()

    # ── Merge ────────────────────────────────────────────────────

    def _merge_results(self, results: list[tuple[AudioChunk, dict]]):
        from framelearn.pipeline.asr_adapter import TranscriptSegment

        all_segments = []
        for chunk, raw in results:
            transcripts = raw.get("transcripts") or []
            if not transcripts:
                continue
            sentences = transcripts[0].get("sentences") or []
            for sent in sentences:
                text = sent.get("text", "").strip()
                if not text:
                    continue
                start = (sent.get("begin_time", 0) / 1000.0) + chunk.start_sec
                end = (sent.get("end_time", 0) / 1000.0) + chunk.start_sec
                all_segments.append(TranscriptSegment(
                    text=text,
                    start=start,
                    end=end,
                ))
        return all_segments

    # ── Cleanup ─────────────────────────────────────────────────

    def _cleanup(self, chunks: list[AudioChunk], oss):
        if not oss:
            return
        for chunk in chunks:
            if chunk.oss_key:
                try:
                    oss.delete(chunk.oss_key)
                except Exception:
                    pass
