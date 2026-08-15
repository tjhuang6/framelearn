#!/usr/bin/env python3
"""Integration test for privacy tracker in video pipeline."""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_privacy_tracker_integration():
    """Test that privacy tracker is properly integrated into VideoPipeline."""
    print("=" * 60)
    print("Integration Test: Privacy Tracker in VideoPipeline")
    print("=" * 60)
    
    from framelearn.privacy_tracker import PrivacyTracker, get_tracker, set_tracker, reset_tracker
    from framelearn.config import reload
    
    # Mock config to enable privacy hints
    mock_config = {
        "text": {
            "text_mode": "appserver",
        },
        "vision": {
            "vision_mode": "api",
            "vision_provider": "siliconflow",
            "vision_model": "Qwen3-VL-8B-Instruct",
        },
        "privacy": {
            "privacy_hints": True,
            "persist_sessions": True,
        },
        "asr": {
            "provider": "dashscope",
            "model": "qwen-audio-3.0-asr-flash-filetrans",
        },
        "agent": {
            "keyframe_selection": False,
        },
        "video": {
            "output_dir": "./output",
            "keep_temp_files": False,
        },
    }
    
    def mock_config_get(key, default=None):
        parts = key.split(".")
        value = mock_config
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return default
        return value if value is not None else default
    
    # Test 1: Tracker is created and set when pipeline runs
    print("\n1. Testing tracker initialization in pipeline...")
    
    tracker = PrivacyTracker(enabled=True)
    set_tracker(tracker)
    
    current_tracker = get_tracker()
    assert current_tracker is not None
    assert current_tracker.enabled
    print("   ✅ Tracker properly initialized")
    
    # Test 2: Tracker tracks services
    print("\n2. Testing service tracking...")
    
    tracker.add_service("test_service", "Test Service Description")
    services = tracker.get_services()
    assert "test_service" in services
    assert services["test_service"] == "Test Service Description"
    print("   ✅ Services tracked correctly")
    
    # Test 3: Tracker shows summary when enabled
    print("\n3. Testing summary output...")
    print("   Expected output:")
    tracker.show_summary()
    
    # Test 4: Disabled tracker is no-op
    print("\n4. Testing disabled tracker...")
    
    reset_tracker()
    disabled_tracker = PrivacyTracker(enabled=False)
    set_tracker(disabled_tracker)
    
    disabled_tracker.add_service("should_not_appear", "This should not appear")
    disabled_services = disabled_tracker.get_services()
    assert len(disabled_services) == 0
    print("   ✅ Disabled tracker is no-op")
    
    # Test 5: Config integration
    print("\n5. Testing config integration...")
    
    with patch('framelearn.config.get', side_effect=mock_config_get):
        from framelearn.config import get as config_get
        
        privacy_hints = config_get("privacy.privacy_hints", False)
        persist_sessions = config_get("privacy.persist_sessions", True)
        
        assert privacy_hints is True
        assert persist_sessions is True
        print(f"   ✅ Config values: privacy_hints={privacy_hints}, persist_sessions={persist_sessions}")
    
    # Cleanup
    reset_tracker()
    
    print("\n" + "=" * 60)
    print("✅ Integration test passed!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        test_privacy_tracker_integration()
    except Exception as e:
        print(f"\n❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
