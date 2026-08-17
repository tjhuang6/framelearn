"""Domain-level exceptions for FrameLearn.

These are distinct from ``ValueError``, which signals a CLI *usage* error
(bad arguments, missing input). A ``FrameLearnError`` signals that the
command was well-formed but could not be completed — e.g. a business
failure inside the video pipeline, or a feature that is not yet
implemented. Both kinds of errors must surface as a non-zero process
exit code so that shell scripts, batch jobs, and CI can detect failure.
"""


class FrameLearnError(Exception):
    """Base class for all FrameLearn domain errors."""


class ConfigurationError(FrameLearnError):
    """Raised when required configuration is missing, invalid, or rejected.

    These failures are fatal: the CLI should exit immediately instead of
    falling back per chunk or making the user wait through a long run.
    """


class DownloadError(FrameLearnError):
    """Raised when an online video cannot be downloaded.

    This covers unsupported URLs, platform API failures, cookie / captcha
    failures and missing output files.
    """


class PipelineExecutionError(FrameLearnError):
    """Raised when VideoPipeline.run() reports a business failure.

    (e.g. missing FFmpeg, ASR failure, document generation failure).
    """


class GenerationError(FrameLearnError):
    """Raised when an LLM stage cannot produce valid output after retries.

    The pipeline deliberately does not degrade to raw subtitles or keep
    unvalidated frames: a generation failure aborts the whole run so the
    user never receives silently-lower-quality output.
    """


class FeatureNotAvailableError(FrameLearnError):
    """Raised when a requested run path is not yet implemented."""
