"""Cache manifest for traceability and validation.

Manifest tracks:
- Input file hash/mtime/size
- Configuration snapshot (relevant keys)
- Provider/model used
- Code version (git commit)
- Completion status of each segment
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class InputFileInfo:
    """Input file metadata for cache key validation."""
    path: str
    size: int
    mtime: float
    sha256: str  # First 16 chars for performance

    @classmethod
    def from_path(cls, path: Path) -> "InputFileInfo":
        """Compute file info from path."""
        stat = path.stat()
        
        # Compute partial hash (first 1MB) for performance
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            chunk = f.read(1024 * 1024)  # Read first 1MB
            hasher.update(chunk)
        
        return cls(
            path=str(path.resolve()),
            size=stat.st_size,
            mtime=stat.st_mtime,
            sha256=hasher.hexdigest()[:16],
        )


@dataclass
class ConfigSnapshot:
    """Configuration snapshot for cache key validation."""
    # Video processing
    scene_threshold: float
    fallback_interval: int
    max_keyframes: int
    
    # Document generation
    doc_mode: str
    segment_duration: int
    max_keyframes_per_segment: int
    
    # Provider/Model
    vision_provider: str
    vision_model: str
    asr_provider: str
    asr_model: str
    
    # Agent features
    keyframe_selection: bool
    quality_review: bool
    
    @classmethod
    def from_config(cls, config_get_fn, mode: str, asr_provider: str = "unknown", asr_model: str = "unknown") -> "ConfigSnapshot":
        """Extract relevant config keys."""
        return cls(
            scene_threshold=config_get_fn("video.scene_threshold", 0.3),
            fallback_interval=config_get_fn("video.fallback_interval", 30),
            max_keyframes=config_get_fn("video.max_keyframes", 100),
            doc_mode=mode,
            segment_duration=config_get_fn("doc_generation.segment_duration", 90),
            max_keyframes_per_segment=config_get_fn("doc_generation.max_keyframes_per_segment", 10),
            vision_provider=config_get_fn("vision.vision_provider", "siliconflow"),
            vision_model=config_get_fn("vision.vision_model", "Qwen/Qwen2.5-VL-72B-Instruct"),
            asr_provider=asr_provider,
            asr_model=asr_model,
            keyframe_selection=config_get_fn("agent.keyframe_selection", False),
            quality_review=config_get_fn("agent.quality_review", False),
        )


@dataclass
class SegmentStatus:
    """Completion status of each segment."""
    index: int
    completed: bool
    timestamp: str
    error: Optional[str] = None


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
    
    # Completion tracking
    segments_total: int = 0
    segments_completed: list[SegmentStatus] = field(default_factory=list)
    
    # Cache key (computed from above fields)
    cache_key: str = ""
    
    def compute_cache_key(self) -> str:
        """Compute cache key from all relevant fields."""
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
        if data.get("segments_completed"):
            data["segments_completed"] = [
                SegmentStatus(**seg) for seg in data["segments_completed"]
            ]
        return cls(**data)
    
    def save(self, path: Path):
        """Save manifest to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
    
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
    
    def validate(self, video_path: Path, subtitle_path: Optional[Path], config_get_fn, mode: str, asr_provider: str, asr_model: str) -> bool:
        """Validate if cached data matches current inputs/config.
        
        Returns True if cache is valid and can be reused.
        """
        # Recompute current cache key
        current_input = InputFileInfo.from_path(video_path)
        current_subtitle = InputFileInfo.from_path(subtitle_path) if subtitle_path and subtitle_path.exists() else None
        current_config = ConfigSnapshot.from_config(config_get_fn, mode, asr_provider, asr_model)
        current_git = get_git_commit()
        
        # Build temporary manifest for current state
        temp_manifest = CacheManifest(
            input_file=current_input,
            subtitle_file=current_subtitle,
            config=current_config,
            git_commit=current_git,
        )
        current_key = temp_manifest.compute_cache_key()
        
        # Compare cache keys
        return self.cache_key == current_key


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
    segments_total: int = 0,
) -> CacheManifest:
    """Create a new cache manifest."""
    manifest = CacheManifest(
        input_file=InputFileInfo.from_path(video_path),
        subtitle_file=InputFileInfo.from_path(subtitle_path) if subtitle_path and subtitle_path.exists() else None,
        config=ConfigSnapshot.from_config(config_get_fn, mode, asr_provider, asr_model),
        git_commit=get_git_commit(),
        segments_total=segments_total,
    )
    manifest.cache_key = manifest.compute_cache_key()
    return manifest


def mark_segment_completed(manifest_path: Path, segment_index: int, error: Optional[str] = None):
    """Mark a segment as completed in the manifest."""
    manifest = CacheManifest.load(manifest_path)
    if not manifest:
        return
    
    # Update or add segment status
    for seg in manifest.segments_completed:
        if seg.index == segment_index:
            seg.completed = error is None
            seg.timestamp = datetime.now().isoformat()
            seg.error = error
            manifest.save(manifest_path)
            return
    
    # Add new segment status
    manifest.segments_completed.append(
        SegmentStatus(
            index=segment_index,
            completed=error is None,
            timestamp=datetime.now().isoformat(),
            error=error,
        )
    )
    manifest.save(manifest_path)


def get_completed_segments(manifest_path: Path) -> set[int]:
    """Get set of completed segment indices."""
    manifest = CacheManifest.load(manifest_path)
    if not manifest:
        return set()
    return {seg.index for seg in manifest.segments_completed if seg.completed}
