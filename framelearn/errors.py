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


class PipelineExecutionError(FrameLearnError):
    """Raised when VideoPipeline.run() reports a business failure.

    (e.g. missing FFmpeg, ASR failure, document generation failure).
    """


class FeatureNotAvailableError(FrameLearnError):
    """Raised when a requested run path is not yet implemented.

    (e.g. downloading and processing an online video URL).
    """
