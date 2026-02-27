"""Lyceum API client for biolyceum job orchestration.

Handles: auth, storage upload/download, job submission (Python & Docker),
status polling via SSE streaming, and a high-level run() method.

Uses the same API patterns as the lyceum-cli package.

Copied from projects/biolyceum/src/utils/client.py for Streamlit Cloud deployment.
"""

import json
import os
import time
from pathlib import Path

import boto3
import httpx


def _load_lyceum_config():
    """Load auth config from ~/.lyceum/config.json (written by `lyceum auth login`)."""
    config_path = Path.home() / ".lyceum" / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


class LyceumClient:
    def __init__(self, api_key=None, base_url=None):
        config = _load_lyceum_config()
        self.api_key = api_key or os.environ.get("LYCEUM_API_KEY") or config.get("api_key")
        self.base_url = base_url or config.get("base_url", "https://api.lyceum.technology")
        if not self.api_key:
            raise ValueError(
                "No API key found. Set LYCEUM_API_KEY env var or run `lyceum auth login`."
            )
        self._headers = {"Authorization": f"Bearer {self.api_key}"}
        self._s3_client = None
        self._s3_bucket = None

    # ── Storage ──────────────────────────────────────────────────────────

    def _ensure_s3(self):
        """Get S3 credentials and create boto3 client (cached)."""
        if self._s3_client is not None:
            return
        resp = httpx.post(
            f"{self.base_url}/api/v2/external/storage/credentials",
            headers=self._headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        creds = resp.json()
        endpoint = creds["endpoint"]
        if not endpoint.startswith("http"):
            endpoint = f"https://{endpoint}"
        self._s3_bucket = creds["bucket_name"]
        self._s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            aws_session_token=creds.get("session_token"),
            region_name=creds.get("region", "us-east-1"),
            config=boto3.session.Config(signature_version="s3v4"),
        )

    def upload_file(self, local_path, storage_key):
        """Upload a local file to Lyceum storage."""
        self._ensure_s3()
        self._s3_client.upload_file(str(local_path), self._s3_bucket, storage_key)

    def upload_bytes(self, data: bytes, storage_key: str):
        """Upload raw bytes to Lyceum storage."""
        self._ensure_s3()
        self._s3_client.put_object(Bucket=self._s3_bucket, Key=storage_key, Body=data)

    def download_file(self, storage_key, local_path):
        """Download a file from Lyceum storage."""
        self._ensure_s3()
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._s3_client.download_file(self._s3_bucket, storage_key, str(local_path))

    def download_bytes(self, storage_key: str) -> bytes:
        """Download a file from Lyceum storage as bytes."""
        self._ensure_s3()
        resp = self._s3_client.get_object(Bucket=self._s3_bucket, Key=storage_key)
        return resp["Body"].read()

    def list_files(self, prefix=""):
        """List files in Lyceum storage under a prefix."""
        self._ensure_s3()
        paginator = self._s3_client.get_paginator("list_objects_v2")
        files = []
        for page in paginator.paginate(Bucket=self._s3_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                files.append(obj["Key"])
        return files

    def download_prefix(self, prefix, local_dir):
        """Download all files under a storage prefix to a local directory."""
        files = self.list_files(prefix)
        if not files:
            return []
        downloaded = []
        for key in files:
            rel = key[len(prefix):].lstrip("/")
            if not rel:
                continue
            local_path = Path(local_dir) / rel
            self.download_file(key, str(local_path))
            downloaded.append(str(local_path))
        return downloaded

    # ── Job Submission ───────────────────────────────────────────────────

    def submit_docker_job(self, docker_image, command, execution_type="gpu.a100",
                          env=None, timeout=300, enable_s3_mount=True):
        """Submit a Docker execution on Lyceum."""
        if isinstance(command, str):
            command = command.split()
        env_str = "\n".join(f"{k}={v}" for k, v in (env or {}).items())
        payload = {
            "docker_image_ref": docker_image,
            "docker_run_cmd": command,
            "execution_type": execution_type,
            "timeout": timeout,
            "docker_run_env": env_str,
            "enable_s3_mount": enable_s3_mount,
        }
        resp = httpx.post(
            f"{self.base_url}/api/v2/external/execution/image/start",
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("execution_id"), data.get("streaming_url")

    # ── Status ───────────────────────────────────────────────────────────

    def get_status(self, execution_id):
        """Get execution status."""
        resp = httpx.get(
            f"{self.base_url}/api/v2/external/execution/streaming/{execution_id}/status",
            headers=self._headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    def wait_for_completion(self, execution_id, poll_interval=5, timeout=3600):
        """Poll execution status until done."""
        start = time.time()
        terminal_states = {"completed", "failed", "failed_user", "failed_system", "timeout", "cancelled"}

        while time.time() - start < timeout:
            result = self.get_status(execution_id)
            status = result.get("status", "unknown")
            if status in terminal_states:
                return status == "completed", status
            time.sleep(poll_interval)

        return False, "timeout"
