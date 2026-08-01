"""ASR adapter for speech-to-text transcription."""

import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()


@dataclass
class TranscriptSegment:
    """Single segment of transcript."""
    text: str
    start: float | None = None  # seconds
    end: float | None = None


@dataclass
class TranscriptResult:
    """Complete transcription result."""
    segments: list[TranscriptSegment]
    full_text: str
    has_timestamps: bool


class ASRAdapter:
    """Adapter for various ASR providers."""

    def __init__(self, provider: str = "siliconflow"):
        self.provider = provider
        self._api_key = os.getenv("SILICONFLOW_API_KEY", "")
        self._base_url = "https://api.siliconflow.cn/v1"
        self._model = "FunAudioLLM/SenseVoiceSmall"

        if not self._api_key or self._api_key.startswith("your_"):
            raise ValueError("SILICONFLOW_API_KEY not configured in .env")

    def transcribe(self, audio_path: str, max_retries: int = 3) -> TranscriptResult:
        """Transcribe audio file.

        Args:
            audio_path: Path to audio file (.m4a, .mp3, .wav, etc.)
            max_retries: Maximum number of retry attempts

        Returns:
            TranscriptResult with full text and segments

        Raises:
            ValueError: If API key is invalid
            RuntimeError: If transcription fails after retries
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        last_error = None
        for attempt in range(max_retries):
            try:
                return self._transcribe_siliconflow(audio_path)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise ValueError("Invalid SILICONFLOW_API_KEY") from e
                if e.response.status_code == 429:
                    # Rate limit, retry with backoff
                    if attempt < max_retries - 1:
                        wait_time = 5 * (attempt + 1)
                        print(f"⏳ Rate limited, retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                last_error = e
            except Exception as e:
                last_error = e

            if attempt < max_retries - 1:
                print(f"⚠️  Attempt {attempt + 1} failed, retrying...")
                time.sleep(5)

        raise RuntimeError(f"Transcription failed after {max_retries} attempts: {last_error}")

    def _transcribe_siliconflow(self, audio_path: Path) -> TranscriptResult:
        """Call SiliconFlow ASR API."""
        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/m4a")}
            data = {"model": self._model}

            response = httpx.post(
                f"{self._base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files=files,
                data=data,
                timeout=300.0,
            )
            response.raise_for_status()

        result = response.json()
        text = result.get("text", "")

        # SiliconFlow SenseVoice doesn't return timestamps
        segment = TranscriptSegment(text=text, start=None, end=None)

        return TranscriptResult(
            segments=[segment],
            full_text=text,
            has_timestamps=False,
        )
