#!/usr/bin/env python3
"""Test script for session management and privacy features."""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_session_commands():
    """Test session management commands."""
    print("=" * 60)
    print("Testing Session Management")
    print("=" * 60)
    
    from framelearn.session_manager import show_info, list_sessions
    
    print("\n1. Session database info:")
    show_info()
    
    print("\n2. List all sessions:")
    list_sessions()
    
    print("\n✅ Session management test completed")


def test_privacy_tracker():
    """Test privacy tracker functionality."""
    print("\n" + "=" * 60)
    print("Testing Privacy Tracker")
    print("=" * 60)
    
    from framelearn.privacy_tracker import PrivacyTracker
    
    print("\n1. Create tracker with hints enabled:")
    tracker = PrivacyTracker(enabled=True)
    tracker.add_service("oss_upload", "阿里云 OSS (临时音频切片)")
    tracker.add_service("asr", "DashScope ASR")
    tracker.add_service("vision_api", "SiliconFlow Vision API")
    tracker.show_summary()
    
    print("\n2. Create tracker with hints disabled:")
    tracker_disabled = PrivacyTracker(enabled=False)
    tracker_disabled.add_service("test", "This should not appear")
    tracker_disabled.show_summary()
    
    print("\n✅ Privacy tracker test completed")


def test_config_defaults():
    """Test that new config options have defaults."""
    print("\n" + "=" * 60)
    print("Testing Configuration Defaults")
    print("=" * 60)
    
    from framelearn.config import get as config_get, reload
    
    # Force reload to pick up defaults
    reload()
    
    persist_sessions = config_get("runtime.persist_sessions", None)
    privacy_hints = config_get("runtime.privacy_hints", None)
    
    print(f"\n1. persist_sessions: {persist_sessions}")
    print(f"2. privacy_hints: {privacy_hints}")
    
    assert persist_sessions is not None, "persist_sessions should have a default"
    assert privacy_hints is not None, "privacy_hints should have a default"
    
    print("\n✅ Configuration defaults test completed")


def test_persistence_disabled():
    """Test that SessionDB can be disabled."""
    print("\n" + "=" * 60)
    print("Testing Session Persistence Disabled Mode")
    print("=" * 60)
    
    from framelearn.app_server.persistence import SessionDB
    
    # Create disabled DB
    db = SessionDB(enabled=False)
    
    # These should all be no-ops
    db.create_session("test_session", "Test Title")
    db.append_message("test_session", "user", "Hello")
    messages = db.get_messages("test_session")
    
    print(f"\n1. Created disabled SessionDB")
    print(f"2. Messages from disabled DB: {len(messages)} (should be 0)")
    
    assert len(messages) == 0, "Disabled DB should return empty list"
    assert db.conn is None, "Disabled DB should have no connection"
    
    print("\n✅ Session persistence disabled test completed")


def main():
    """Run all tests."""
    print("\n🧪 FrameLearn Privacy & Session Management Tests\n")
    
    try:
        test_privacy_tracker()
        test_config_defaults()
        test_persistence_disabled()
        test_session_commands()
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
