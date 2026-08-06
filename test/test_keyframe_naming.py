"""Test keyframe file naming collision avoidance."""

import tempfile
from pathlib import Path

import pytest


def test_timestamp_formatting():
    """Test millisecond precision formatting."""
    # Scene frame at 30.250 seconds
    ts = 30.250
    h = int(ts // 3600)
    m = int((ts % 3600) // 60)
    s = int(ts % 60)
    ms = int((ts % 1) * 1000)
    
    scene_name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_scene_001.jpg"
    assert scene_name == "frame_00h00m30s250ms_scene_001.jpg"
    
    # Interval frame at exactly 30.000 seconds
    ts2 = 30.000
    h2 = int(ts2 // 3600)
    m2 = int((ts2 % 3600) // 60)
    s2 = int(ts2 % 60)
    ms2 = int((ts2 % 1) * 1000)
    
    interval_name = f"frame_{h2:02d}h{m2:02d}m{s2:02d}s{ms2:03d}ms_interval_001.jpg"
    assert interval_name == "frame_00h00m30s000ms_interval_001.jpg"
    
    # Verify they don't collide
    assert scene_name != interval_name


def test_no_collision_same_second():
    """Test that scene and interval frames at the same second don't collide."""
    timestamps = [30.123, 30.456, 30.789]
    
    filenames = []
    for i, ts in enumerate(timestamps, 1):
        h = int(ts // 3600)
        m = int((ts % 3600) // 60)
        s = int(ts % 60)
        ms = round((ts % 1) * 1000)
        name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_scene_{i:03d}.jpg"
        filenames.append(name)
    
    # All filenames should be unique
    assert len(filenames) == len(set(filenames))
    assert filenames[0] == "frame_00h00m30s123ms_scene_001.jpg"
    assert filenames[1] == "frame_00h00m30s456ms_scene_002.jpg"
    assert filenames[2] == "frame_00h00m30s789ms_scene_003.jpg"


def test_timestamp_parsing():
    """Test parsing timestamp from new filename format."""
    filename = "frame_00h01m30s250ms_scene_003.jpg"
    
    # Parse logic from video_pipeline.py
    name = Path(filename).stem  # "frame_00h01m30s250ms_scene_003"
    parts = name.split("_", 1)
    assert len(parts) == 2
    
    time_part = parts[1].split("_")[0]  # "00h01m30s250ms"
    time_part = time_part.replace("ms", "")
    
    h_part, rest = time_part.split("h")
    m_part, rest = rest.split("m")
    s_part = rest.split("s")[0]
    ms_part = rest.split("s")[1] if "s" in rest and rest.split("s")[1] else "0"
    
    h, m, s, ms = int(h_part), int(m_part), int(s_part), int(ms_part)
    timestamp = h * 3600 + m * 60 + s + ms / 1000.0
    
    assert timestamp == pytest.approx(90.250)
    assert h == 0
    assert m == 1
    assert s == 30
    assert ms == 250


def test_agent_frame_naming():
    """Test agent keyframe selector naming."""
    ts = 125.678
    selected_count = 5
    
    h = int(ts // 3600)
    m = int((ts % 3600) // 60)
    s = int(ts % 60)
    ms = round((ts % 1) * 1000)
    
    frame_name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_agent_{selected_count+1:03d}.jpg"
    
    assert frame_name == "frame_00h02m05s678ms_agent_006.jpg"


def test_filename_uniqueness_with_files(tmp_path: Path):
    """Test actual file creation without collisions."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    
    # Simulate scene and interval frames at similar timestamps
    scene_timestamps = [30.123, 30.456]
    interval_timestamps = [30.000, 60.000]
    
    created_files = []
    
    # Create scene frames
    for i, ts in enumerate(scene_timestamps, 1):
        h = int(ts // 3600)
        m = int((ts % 3600) // 60)
        s = int(ts % 60)
        ms = round((ts % 1) * 1000)
        name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_scene_{i:03d}.jpg"
        filepath = frames_dir / name
        filepath.write_text("scene frame")
        created_files.append(filepath)
    
    # Create interval frames
    for i, ts in enumerate(interval_timestamps, 1):
        h = int(ts // 3600)
        m = int((ts % 3600) // 60)
        s = int(ts % 60)
        ms = round((ts % 1) * 1000)
        name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_interval_{i:03d}.jpg"
        filepath = frames_dir / name
        filepath.write_text("interval frame")
        created_files.append(filepath)
    
    # Verify all files exist and are unique
    assert len(created_files) == 4
    assert all(f.exists() for f in created_files)
    assert len(set(f.name for f in created_files)) == 4
    
    # Verify no filename conflicts
    filenames = [f.name for f in created_files]
    assert "frame_00h00m30s123ms_scene_001.jpg" in filenames
    assert "frame_00h00m30s456ms_scene_002.jpg" in filenames
    assert "frame_00h00m30s000ms_interval_001.jpg" in filenames
    assert "frame_00h01m00s000ms_interval_002.jpg" in filenames


def test_edge_case_zero_milliseconds():
    """Test frames with exactly 0 milliseconds."""
    ts = 60.0
    h = int(ts // 3600)
    m = int((ts % 3600) // 60)
    s = int(ts % 60)
    ms = round((ts % 1) * 1000)
    
    name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_interval_001.jpg"
    assert name == "frame_00h01m00s000ms_interval_001.jpg"
    assert ms == 0


def test_edge_case_999_milliseconds():
    """Test frames with maximum milliseconds."""
    ts = 59.999
    h = int(ts // 3600)
    m = int((ts % 3600) // 60)
    s = int(ts % 60)
    ms = round((ts % 1) * 1000)
    
    name = f"frame_{h:02d}h{m:02d}m{s:02d}s{ms:03d}ms_scene_001.jpg"
    assert name == "frame_00h00m59s999ms_scene_001.jpg"
    assert ms == 999


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
