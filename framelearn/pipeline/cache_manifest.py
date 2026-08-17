"""Cache manifest for traceability and validation.

Manifest tracks:
- Input file hash/mtime/size
- Configuration snapshot (relevant keys for subtitle / keyframe caches)
- Provider/model used
- Code version (git commit)
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass, field, asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class InputFileInfo:
    """Input file metadata for cache key validation."""
    path: str
    size: int
    mtime: float
    sha256: str  # First 16 hex chars of the FULL file SHA-256

    @classmethod
    def from_path(cls, path: Path) -> "InputFileInfo":
        """Compute file info from path."""
        stat = path.stat()

        # Hash the full file. Videos can be large, so stream in 1 MiB
        # chunks instead of reading everything into memory.
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)

        return cls(
            path=str(path.resolve()),
            size=stat.st_size,
            mtime=stat.st_mtime,
            sha256=hasher.hexdigest()[:16],
        )


@dataclass
class ConfigSnapshot:
    """Configuration snapshot for cache key validation.

    Only the two caches used by the anchored pipeline exist now:

    - ``subtitle``: keyed by the ASR provider/model
    - ``keyframe``: keyed by the ``[heuristic]`` extractor settings
    """

    mode: str = ""
    asr_provider: str = ""
    asr_model: str = ""
    heuristic_scene_threshold: float = 0.0
    heuristic_similarity_threshold: float = 0.0
    heuristic_max_frames: int = 0

    @classmethod
    def from_config(
        cls,
        config_get_fn,
        mode: str,
        asr_provider: str = "unknown",
        asr_model: str = "unknown",
    ) -> "ConfigSnapshot":
        """Extract the config keys that are relevant for ``mode``."""
        if mode == "subtitle":
            return cls(
                mode=mode,
                asr_provider=asr_provider,
                asr_model=asr_model,
            )

        if mode == "keyframe":
            return cls(
                mode=mode,
                heuristic_scene_threshold=float(
                    config_get_fn("heuristic.scene_threshold", 0.4)
                ),
                heuristic_similarity_threshold=float(
                    config_get_fn("heuristic.similarity_threshold", 0.95)
                ),
                heuristic_max_frames=int(
                    config_get_fn("heuristic.max_frames", 200)
                ),
            )

        raise ValueError(f"Unsupported manifest mode: {mode}")


@dataclass
class CacheManifest:
    """Complete cache manifest for traceability."""
    version: str = "1.0"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Input file
    input_file: Optional[InputFileInfo] = None
    subtitle_file: Optional[InputFileInfo] = None

    # Configuration
    config: Optional[ConfigSnapshot] = None

    # Code version
    git_commit: Optional[str] = None

    # Heuristic keyframes produced for this run (used to invalidate the
    # cache when the heuristic extractor output changes, e.g. a new ffmpeg
    # scene-detection pass picks up different shots).
    heuristic_frames_digest: str = ""

    # Cache key (computed from above fields)
    cache_key: str = ""

    def compute_cache_key(self) -> str:
        """Compute cache key from all relevant fields.

        The ``heuristic_frames_digest`` is only folded in when BOTH the
        cached and current sides know it — the keyframe-cache check in
        :class:`VideoPipeline` runs *before* extraction, so it has no
        digest yet. Treating "absent on either side" as "no constraint"
        keeps the cache usable.
        """
        parts = []

        # Input files
        if self.input_file:
            parts.append(f"input:{self.input_file.sha256}:{self.input_file.size}")
        if self.subtitle_file:
            parts.append(f"subtitle:{self.subtitle_file.sha256}:{self.subtitle_file.size}")

        # Config (serialize to stable JSON)
        if self.config:
            config_json = json.dumps(asdict(self.config), sort_keys=True)
            config_hash = hashlib.sha256(config_json.encode()).hexdigest()[:16]
            parts.append(f"config:{config_hash}")

        # Heuristic-frame digest: only contribute when this manifest has
        # one. The validator below enforces symmetry (caller passes the
        # current run's digest and compute_cache_key is re-run with it).
        if self.heuristic_frames_digest:
            parts.append(f"frames:{self.heuristic_frames_digest}")

        # Git commit excluded from cache key for development convenience
        # Rationale: code changes that affect output should change config/behavior,
        # which is already tracked. Including git hash invalidates cache on every
        # commit/edit, making caching useless during active development.
        # Git commit is still recorded in manifest for traceability/debugging.

        key = "|".join(parts)
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        data = asdict(self)
        # Recompute cache key before saving
        self.cache_key = self.compute_cache_key()
        data["cache_key"] = self.cache_key
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CacheManifest":
        """Load from dict."""
        # Reconstruct nested dataclasses
        if data.get("input_file"):
            data["input_file"] = InputFileInfo(**data["input_file"])
        if data.get("subtitle_file"):
            data["subtitle_file"] = InputFileInfo(**data["subtitle_file"])
        if data.get("config"):
            data["config"] = ConfigSnapshot(**data["config"])
        return cls(**data)

    def save(self, path: Path):
        """Save manifest to JSON file atomically."""
        from framelearn.file_utils import atomic_write_text

        atomic_write_text(
            path,
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
        )

    @classmethod
    def load(cls, path: Path) -> Optional["CacheManifest"]:
        """Load manifest from JSON file."""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception as e:
            print(f"⚠️  Failed to load manifest: {e}")
            return None

    def validate(
        self,
        video_path: Path,
        subtitle_path: Optional[Path],
        config_get_fn,
        mode: str,
        asr_provider: str,
        asr_model: str,
        heuristic_frames_digest: str = "",
    ) -> bool:
        """Validate if cached data matches current inputs/config.

        Returns True if cache is valid and can be reused.

        The heuristic-frames digest participates in the comparison only
        when BOTH sides know it: the stored manifest was saved with one
        AND the caller passes the current run's. The keyframe-cache
        check in :class:`VideoPipeline` runs *before* extraction and has
        no digest yet, so it compares on input/config alone — matching
        the symmetry rule documented on :meth:`compute_cache_key`.
        """
        # Recompute current cache key
        current_input = InputFileInfo.from_path(video_path)
        current_subtitle = (
            InputFileInfo.from_path(subtitle_path)
            if subtitle_path and subtitle_path.exists()
            else None
        )
        current_config = ConfigSnapshot.from_config(
            config_get_fn, mode, asr_provider, asr_model
        )
        current_git = get_git_commit()

        stored_digest = self.heuristic_frames_digest
        effective_digest = (
            heuristic_frames_digest
            if stored_digest and heuristic_frames_digest
            else ""
        )

        # Build temporary manifest for current state
        temp_manifest = CacheManifest(
            input_file=current_input,
            subtitle_file=current_subtitle,
            config=current_config,
            git_commit=current_git,
            heuristic_frames_digest=effective_digest,
        )
        current_key = temp_manifest.compute_cache_key()

        # Recompute the stored key under the SAME digest rule, so a
        # manifest saved WITH a digest can still validate against a
        # digest-less caller (pre-extraction cache check). Comparing
        # against the stored ``cache_key`` field directly would embed
        # the digest on one side only and never match.
        stored_manifest = replace(
            self, heuristic_frames_digest=effective_digest
        )
        stored_key = stored_manifest.compute_cache_key()

        # Compare cache keys
        return stored_key == current_key


def compute_heuristic_frames_digest(frames) -> str:
    """SHA256 over the ordered list of (path, timestamp_sec) tuples.

    Accepts either CandidateFrame objects, plain tuples, or Path strings.
    The digest is stable across runs as long as the extractor produced
    the same frames in the same order — which is exactly the signal we
    need for cache invalidation.
    """
    hasher = hashlib.sha256()
    for f in frames:
        if hasattr(f, "path") and hasattr(f, "timestamp_sec"):
            key = (str(f.path), float(f.timestamp_sec))
        elif isinstance(f, (list, tuple)) and len(f) >= 2:
            key = (str(f[0]), float(f[1]))
        elif isinstance(f, (list, tuple)) and len(f) == 1:
            key = (str(f[0]), 0.0)
        else:
            key = (str(f), 0.0)
        hasher.update(f"{key[0]}|{key[1]:.6f}\n".encode())
    return hasher.hexdigest()[:16]


def get_git_commit() -> Optional[str]:
    """Get current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=Path(__file__).parent.parent.parent,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def create_manifest(
    video_path: Path,
    subtitle_path: Optional[Path],
    config_get_fn,
    mode: str,
    asr_provider: str = "unknown",
    asr_model: str = "unknown",
    heuristic_frames=None,
) -> CacheManifest:
    """Create a new cache manifest.

    ``heuristic_frames`` is the ordered list produced by the heuristic
    extractor (CandidateFrame or tuple list). The list is folded into a
    short SHA256 digest that goes into the cache key, so a different
    frame set invalidates downstream caches.
    """
    digest = (
        compute_heuristic_frames_digest(heuristic_frames)
        if heuristic_frames
        else ""
    )
    manifest = CacheManifest(
        input_file=InputFileInfo.from_path(video_path),
        subtitle_file=(
            InputFileInfo.from_path(subtitle_path)
            if subtitle_path and subtitle_path.exists()
            else None
        ),
        config=ConfigSnapshot.from_config(
            config_get_fn, mode, asr_provider, asr_model
        ),
        git_commit=get_git_commit(),
        heuristic_frames_digest=digest,
    )
    manifest.cache_key = manifest.compute_cache_key()
    return manifest
