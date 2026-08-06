"""Privacy tracker for logging external service usage.

Logs what external services are being used in each task, including:
- OSS uploads (DashScope ASR)
- ASR API calls (DashScope, SiliconFlow)
- Vision API calls (various providers)
- Text API calls (various providers)
- Local session persistence

Usage:
    tracker = PrivacyTracker()
    tracker.add_service("oss_upload", "Aliyun OSS (临时音频切片)")
    tracker.add_service("asr", "DashScope ASR")
    tracker.show_summary()
"""

from typing import Optional


class PrivacyTracker:
    """Track external services used in a single task."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.services: dict[str, str] = {}

    def add_service(self, key: str, description: str):
        """Add a service to the tracking list.

        Args:
            key: Service identifier (e.g., "oss_upload", "asr", "vision_api")
            description: Human-readable description
        """
        if not self.enabled:
            return
        self.services[key] = description

    def show_summary(self):
        """Print a summary of services used in this task."""
        if not self.enabled or not self.services:
            return

        print("\n🔒 本次任务使用的外部服务：")
        for desc in self.services.values():
            print(f"   • {desc}")
        print()

    def get_services(self) -> dict[str, str]:
        """Return the services dict for programmatic access."""
        return self.services.copy()


# Global tracker instance (set by VideoPipeline or other entry points)
_current_tracker: Optional[PrivacyTracker] = None


def get_tracker() -> PrivacyTracker:
    """Get the current global tracker (or a no-op tracker if not set)."""
    global _current_tracker
    if _current_tracker is None:
        return PrivacyTracker(enabled=False)
    return _current_tracker


def set_tracker(tracker: Optional[PrivacyTracker]):
    """Set the global tracker for the current task."""
    global _current_tracker
    _current_tracker = tracker


def reset_tracker():
    """Clear the global tracker."""
    global _current_tracker
    _current_tracker = None
