"""SiliconFlow SenseVoice ASR backend (no timestamps)."""

import time
from pathlib import Path

import httpx


class SiliconflowBackend:
    BASE_URL = "https://api.siliconflow.cn/v1"

    def __init__(self, api_key: str, model: str = "FunAudioLLM/SenseVoiceSmall"):
        if not api_key or api_key.startswith("your_"):
            raise ValueError(
                "SILICONFLOW_API_KEY not configured in .env\n"
                "Get your key at: https://siliconflow.cn/"
            )
        self.api_key = api_key
        self.model = model

    def transcribe(self, audio_path: Path, max_retries: int = 3):
        """Transcribe audio file. Returns ``TranscriptResult`` without timestamps."""
        from framelearn.pipeline.asr_adapter import TranscriptResult

        last_error = None
        for attempt in range(max_retries):
            try:
                text = self._call_api(audio_path)
                return TranscriptResult(
                    segments=[],
                    full_text=text,
                    has_timestamps=False,
                    srt=None,
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise ValueError("Invalid SILICONFLOW_API_KEY") from e
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    print(f"⏳ 限流，{wait}s 后重试...")
                    time.sleep(wait)
                    continue
                last_error = e
            except Exception as e:
                last_error = e

            if attempt < max_retries - 1:
                print(f"⚠️  第 {attempt + 1} 次失败，重试...")
                time.sleep(5)

        raise RuntimeError(f"转录失败（重试 {max_retries} 次）：{last_error}")

    def _call_api(self, audio_path: Path) -> str:
        with open(audio_path, "rb") as f:
            response = httpx.post(
                f"{self.BASE_URL}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (audio_path.name, f, "audio/m4a")},
                data={"model": self.model},
                timeout=300.0,
            )
            response.raise_for_status()
        return response.json().get("text", "")
