"""Aliyun OSS client for temporary audio storage."""

import os
from pathlib import Path

from framelearn.config import get as config_get


class OssClient:
    """Upload / sign / delete objects on Aliyun OSS."""

    def __init__(self):
        import oss2

        key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
        key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")
        bucket_name = config_get("asr.oss.bucket", "")
        region = config_get("asr.oss.region", "oss-cn-hangzhou")

        if not key_id or key_id.startswith("your_"):
            raise ValueError("OSS_ACCESS_KEY_ID not configured in .env")
        if not key_secret or key_secret.startswith("your_"):
            raise ValueError("OSS_ACCESS_KEY_SECRET not configured in .env")
        if not bucket_name:
            raise ValueError(
                "asr.oss.bucket not configured in settings.toml\n"
                "Set the bucket name of your Aliyun OSS bucket."
            )

        auth = oss2.Auth(key_id, key_secret)
        endpoint = f"https://{region}.aliyuncs.com"

        # Increase timeout for large audio files
        self.bucket = oss2.Bucket(
            auth,
            endpoint,
            bucket_name,
            connect_timeout=30,  # connection timeout
            timeout=600          # read/write timeout (10 minutes)
        )
        endpoint = f"https://{region}.aliyuncs.com"
        self.bucket = oss2.Bucket(auth, endpoint, bucket_name)
        self._region = region
        self._bucket_name = bucket_name

    def upload(self, local_path: str, object_key: str) -> str:
        """Upload a local file to OSS.

        Args:
            local_path: Path to local file
            object_key: Object key (path) in OSS

        Returns:
            The object_key (for use with sign_url / delete)
        """
        self.bucket.put_object_from_file(object_key, local_path)
        return object_key

    def sign_url(self, object_key: str, ttl_seconds: int) -> str:
        """Generate a temporary signed download URL.

        Args:
            object_key: Object key in OSS
            ttl_seconds: URL validity in seconds

        Returns:
            Signed HTTPS URL valid for ttl_seconds
        """
        return self.bucket.sign_url("GET", object_key, ttl_seconds)

    def delete(self, object_key: str):
        """Delete an object from OSS.

        Silently ignores errors (cleanup is best-effort).
        """
        try:
            self.bucket.delete_object(object_key)
        except Exception:
            pass
