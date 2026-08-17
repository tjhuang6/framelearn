"""CLI tool to inspect cache manifests."""

import json
import sys
from pathlib import Path
from typing import Optional

from framelearn.pipeline.cache_manifest import CacheManifest


def format_timestamp(iso_str: str) -> str:
    """Format ISO timestamp to readable string."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_str


def print_manifest(manifest_path: Path):
    """Print manifest information in a readable format."""
    manifest = CacheManifest.load(manifest_path)
    if not manifest:
        print(f"❌ Failed to load manifest: {manifest_path}")
        return
    
    print(f"\n{'='*60}")
    print(f"📋 Manifest: {manifest_path.name}")
    print(f"{'='*60}\n")
    
    # Basic info
    print(f"📅 Created: {format_timestamp(manifest.created_at)}")
    print(f"🔑 Cache Key: {manifest.cache_key}")
    if manifest.git_commit:
        print(f"💻 Git Commit: {manifest.git_commit}")
    
    # Input file
    if manifest.input_file:
        print(f"\n📹 Input File:")
        print(f"   Path: {manifest.input_file.path}")
        print(f"   Size: {manifest.input_file.size:,} bytes")
        print(f"   Hash: {manifest.input_file.sha256}")
    
    # Subtitle file
    if manifest.subtitle_file:
        print(f"\n📝 Subtitle File:")
        print(f"   Path: {manifest.subtitle_file.path}")
        print(f"   Size: {manifest.subtitle_file.size:,} bytes")
        print(f"   Hash: {manifest.subtitle_file.sha256}")
    
    # Config
    if manifest.config:
        print(f"\n⚙️  Configuration:")
        config_dict = {
            "Mode": manifest.config.mode,
            "ASR": f"{manifest.config.asr_provider} / {manifest.config.asr_model}",
            "Heuristic Scene Threshold": manifest.config.heuristic_scene_threshold,
            "Heuristic Similarity Threshold": manifest.config.heuristic_similarity_threshold,
            "Heuristic Max Frames": manifest.config.heuristic_max_frames,
        }
        for key, value in config_dict.items():
            print(f"   {key}: {value}")
    
    print(f"\n{'='*60}\n")


def find_manifests(directory: Path) -> list[Path]:
    """Find all manifest.json files in directory."""
    manifests = []
    
    # Check common locations
    src_dir = directory / "src"
    if src_dir.exists():
        for pattern in ["*_manifest.json", "manifest.json"]:
            manifests.extend(src_dir.glob(pattern))
    
    return sorted(manifests)


def main():
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python -m framelearn.tools.inspect_manifest <output_dir>")
        print("\nExamples:")
        print("  python -m framelearn.tools.inspect_manifest output/my_video")
        print("  python -m framelearn.tools.inspect_manifest output/my_video/src/subtitle_manifest.json")
        sys.exit(1)
    
    path = Path(sys.argv[1])
    
    if not path.exists():
        print(f"❌ Path not found: {path}")
        sys.exit(1)
    
    if path.is_file() and path.suffix == ".json":
        # Direct manifest file
        print_manifest(path)
    elif path.is_dir():
        # Directory - find all manifests
        manifests = find_manifests(path)
        if not manifests:
            print(f"⚠️  No manifests found in: {path}")
            print("\nExpected locations:")
            print("  - output/video_name/src/subtitle_manifest.json")
            print("  - output/video_name/src/keyframe_manifest.json")
            sys.exit(1)
        
        print(f"\n🔍 Found {len(manifests)} manifest(s) in {path}")
        for manifest_path in manifests:
            print_manifest(manifest_path)
    else:
        print(f"❌ Invalid path: {path}")
        print("Expected: directory or .json file")
        sys.exit(1)


if __name__ == "__main__":
    main()
