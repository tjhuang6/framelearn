"""ASR adapter — routes to backend based on settings.toml asr.provider."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from framelearn.config import get as config_get

load_dotenv()


@dataclass
class TranscriptSegment:
    text: str
    start: Optional[float] = None   # seconds (None for siliconflow)
    end: Optional[float] = None


@dataclass
class TranscriptResult:
    segments: list[TranscriptSegment]
    full_text: str
    has_timestamps: bool
    srt: Optional[str] = None       # SRT content (dashscope only)


class ASRAdapter:
    """Routes transcription to configured backend."""

    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or config_get("asr.provider", "siliconflow")

    def transcribe(self, audio_path: str, max_retries: int = 3) -> TranscriptResult:
        """Transcribe audio file using configured provider.

        Raises:
            FileNotFoundError: Audio file not found
            ValueError: API key missing or invalid
            RuntimeError: Transcription failed
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"音频文件不存在：{audio_path}")

        if self.provider == "dashscope":
            return self._transcribe_dashscope(path, max_retries)
        else:
            return self._transcribe_siliconflow(path, max_retries)

    def _transcribe_siliconflow(self, path: Path, max_retries: int) -> TranscriptResult:
        from framelearn.pipeline.asr_backends.siliconflow import SiliconflowBackend
        api_key = os.getenv("SILICONFLOW_API_KEY", "")
        model = config_get("asr.model", "FunAudioLLM/SenseVoiceSmall")
        backend = SiliconflowBackend(api_key=api_key, model=model)
        return backend.transcribe(path, max_retries=max_retries)

    def _transcribe_dashscope(self, path: Path, max_retries: int) -> TranscriptResult:
        from framelearn.pipeline.asr_backends.dashscope import DashscopeBackend
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        if not api_key or api_key.startswith("your_"):
            raise ValueError(
                "DASHSCOPE_API_KEY not configured in .env\n"
                "Get your key at: https://dashscope.aliyun.com/"
            )
        backend = DashscopeBackend(api_key=api_key)
        return backend.transcribe(path)
