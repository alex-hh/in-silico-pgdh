"""Lyceum API client for biolyceum job orchestration.

Handles: auth, storage upload/download, job submission (Python & Docker),
status polling via SSE streaming, and a high-level run() method.

Uses the same API patterns as the lyceum-cli package.
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
            timeout=300.0,
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
        print(f"  uploaded {local_path} → {storage_key}")

    def upload_bytes(self, data: bytes, storage_key: str):
        """Upload raw bytes to Lyceum storage."""
        self._ensure_s3()
        self._s3_client.put_object(Bucket=self._s3_bucket, Key=storage_key, Body=data)

    def download_file(self, storage_key, local_path):
        """Download a file from Lyceum storage."""
        self._ensure_s3()
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._s3_client.download_file(self._s3_bucket, storage_key, str(local_path))
        print(f"  downloaded {storage_key} → {local_path}")

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
            print(f"  no files found under {prefix}")
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

    def submit_python_job(self, script_path, requirements=None, machine="gpu.a100",
                          timeout=60, args=None, import_files=None):
        """Submit a Python script for execution on Lyceum.

        Args:
            script_path: Path to the .py file to execute.
            requirements: Path to requirements.txt or a requirements string.
            machine: Machine type (cpu, a100, h100).
            timeout: Timeout in seconds.
            args: List of script arguments.
            import_files: Dict of {remote_path: file_contents} for local imports.

        Returns:
            (execution_id, streaming_url) tuple.
        """
        code = Path(script_path).read_text()

        # Inject sys.argv if args provided
        if args:
            argv_str = json.dumps([str(script_path)] + list(args))
            code = f"import sys; sys.argv = {argv_str}\n" + code

        # Read requirements
        req_content = ""
        if requirements:
            req_path = Path(requirements)
            if req_path.exists():
                req_content = req_path.read_text()
            else:
                req_content = requirements

        payload = {
            "code": code,
            "nbcode": 0,
            "execution_type": machine,
            "timeout": timeout,
            "file_name": Path(script_path).name,
            "requirements_content": req_content,
            "prior_imports": [],
        }
        if import_files:
            payload["import_files"] = json.dumps(import_files)

        resp = httpx.post(
            f"{self.base_url}/api/v2/external/execution/streaming/start",
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
            timeout=300.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("execution_id"), data.get("streaming_url")

    def submit_docker_job(self, docker_image, command, execution_type="gpu.a100",
                          env=None, timeout=300, enable_s3_mount=True):
        """Submit a Docker execution on Lyceum.

        Args:
            docker_image: Docker image reference (e.g. pytorch/pytorch:2.6.0-...).
            command: List of command arguments.
            execution_type: Machine type.
            env: Dict of environment variables.
            timeout: Timeout in seconds.
            enable_s3_mount: Mount S3 storage at /mnt/s3.

        Returns:
            (execution_id, streaming_url) tuple.
        """
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
            timeout=300.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("execution_id"), data.get("streaming_url")

    # ── Status & Streaming ───────────────────────────────────────────────

    def get_status(self, execution_id):
        """Get execution status.

        Returns:
            dict with 'status' and optionally 'errors' keys.
            Status values: pending, queued, running, completed, failed,
                          failed_user, failed_system, timeout, cancelled.
        """
        resp = httpx.get(
            f"{self.base_url}/api/v2/external/execution/streaming/{execution_id}/status",
            headers=self._headers,
            timeout=300.0,
        )
        resp.raise_for_status()
        return resp.json()

    def stream_output(self, execution_id, streaming_url=None):
        """Stream execution output via SSE. Blocks until completion.

        Returns:
            (success: bool, output: str)
        """
        url = streaming_url or f"{self.base_url}/api/v1/stream/{execution_id}"
        output_lines = []
        success = False

        try:
            with httpx.stream("POST", url, headers=self._headers, timeout=1200.0) as resp:
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    # New format
                    if "output" in data:
                        content = data["output"].get("content", "")
                        if content:
                            print(content, end="")
                            output_lines.append(content)
                    elif "jobFinished" in data:
                        result = data["jobFinished"].get("job", {}).get("result", {})
                        success = result.get("returnCode", 1) == 0
                        break
                    # Legacy format
                    elif data.get("type") == "output":
                        content = data.get("content", "")
                        if content:
                            print(content, end="")
                            output_lines.append(content)
                    elif data.get("type") == "completed":
                        success = True
                        break
                    elif data.get("type") == "error":
                        print(f"ERROR: {data.get('message', '')}")
                        break
        except httpx.ReadTimeout:
            print("Stream timed out, falling back to status polling...")
            return self._poll_until_done(execution_id)

        # If stream ended without explicit completion signal, check status
        if not success:
            result = self.get_status(execution_id)
            status = result.get("status", "unknown")
            if status == "completed":
                success = True

        return success, "".join(output_lines)

    def wait_for_completion(self, execution_id, poll_interval=5, timeout=3600):
        """Poll execution status until done.

        Returns:
            (success: bool, status: str)
        """
        start = time.time()
        terminal_states = {"completed", "failed", "failed_user", "failed_system", "timeout", "cancelled"}

        while time.time() - start < timeout:
            result = self.get_status(execution_id)
            status = result.get("status", "unknown")
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] status: {status}")

            if status in terminal_states:
                success = status == "completed"
                if not success:
                    errors = result.get("errors", "")
                    if errors:
                        print(f"  error: {errors}")
                return success, status

            time.sleep(poll_interval)

        print(f"  timed out after {timeout}s")
        return False, "timeout"

    def _poll_until_done(self, execution_id):
        """Fallback polling when streaming fails."""
        success, status = self.wait_for_completion(execution_id)
        return success, status

    # ── BoltzGen ────────────────────────────────────────────────────────

    def run_boltzgen(self, yaml_path, structure_files=None, output_dir="./output/boltzgen",
                     protocol="protein-anything", num_designs=10, machine="gpu.a100",
                     timeout=600):
        """Run BoltzGen protein design on Lyceum via Docker execution.

        Args:
            yaml_path: Local path to YAML design spec.
            structure_files: List of local paths to structure files (CIF/PDB)
                referenced in the YAML.
            output_dir: Local directory to download results to.
            protocol: Design protocol.
            num_designs: Number of designs to generate.
            machine: Machine type.
            timeout: Timeout in seconds (max 600).

        Returns:
            (success: bool, downloaded_files: list[str])
        """
        # Upload YAML and structure files
        print("Uploading BoltzGen inputs...")
        self.upload_file(yaml_path, f"input/boltzgen/{Path(yaml_path).name}")
        for f in (structure_files or []):
            self.upload_file(f, f"input/boltzgen/{Path(f).name}")

        # Build command
        yaml_name = Path(yaml_path).name
        cmd = (
            f"bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh"
            f" --input-yaml /root/boltzgen_work/{yaml_name}"
            f" --output-dir /mnt/s3/output/boltzgen"
            f" --protocol {protocol}"
            f" --num-designs {num_designs}"
            f" --cache /mnt/s3/models/boltzgen"
        )

        print(f"Submitting BoltzGen Docker job on {machine}...")
        exec_id, stream_url = self.submit_docker_job(
            docker_image="pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime",
            command=cmd,
            execution_type=machine,
            timeout=timeout,
        )
        print(f"  execution_id: {exec_id}")

        # Stream output
        success, output = self.stream_output(exec_id, stream_url)
        if not success:
            print("BoltzGen execution failed.")
            return False, []

        print("BoltzGen execution completed.")

        # Download results
        print(f"Downloading results to {output_dir}...")
        downloaded = self.download_prefix("output/boltzgen/final_ranked_designs/", output_dir)
        print(f"  downloaded {len(downloaded)} files")
        return True, downloaded

    # ── Boltz-2 ──────────────────────────────────────────────────────────

    def run_boltz2(self, yaml_path, output_dir="./output/boltz2",
                   recycling_steps=10, diffusion_samples=5,
                   use_msa_server=True, machine="gpu.a100", timeout=600):
        """Run Boltz-2 structure prediction on Lyceum via Docker execution.

        Args:
            yaml_path: Local path to YAML input file (or directory for batch).
            output_dir: Local directory to download results to.
            recycling_steps: Number of recycling steps.
            diffusion_samples: Number of diffusion samples.
            use_msa_server: Whether to use ColabFold MSA server.
            machine: Machine type.
            timeout: Timeout in seconds (max 600).

        Returns:
            (success: bool, downloaded_files: list[str])
        """
        yaml_path = Path(yaml_path)

        # Upload YAML(s) to input/boltz2/
        print("Uploading Boltz-2 inputs...")
        if yaml_path.is_dir():
            for f in sorted(yaml_path.glob("*.yaml")) + sorted(yaml_path.glob("*.yml")):
                self.upload_file(str(f), f"input/boltz2/{f.name}")
            input_flag = "--input-dir /root/boltz2_work"
        else:
            self.upload_file(str(yaml_path), f"input/boltz2/{yaml_path.name}")
            input_flag = f"--input-yaml /root/boltz2_work/{yaml_path.name}"

        # Build command
        cmd = (
            f"bash /mnt/s3/scripts/boltz2/run_boltz2.sh"
            f" {input_flag}"
            f" --output-dir /mnt/s3/output/boltz2"
            f" --recycling-steps {recycling_steps}"
            f" --diffusion-samples {diffusion_samples}"
            f" --cache /mnt/s3/models/boltz2"
        )
        if use_msa_server:
            cmd += " --use-msa-server"
        else:
            cmd += " --no-msa-server"

        print(f"Submitting Boltz-2 Docker job on {machine}...")
        exec_id, stream_url = self.submit_docker_job(
            docker_image="pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime",
            command=cmd,
            execution_type=machine,
            timeout=timeout,
        )
        print(f"  execution_id: {exec_id}")

        # Stream output
        success, output = self.stream_output(exec_id, stream_url)
        if not success:
            print("Boltz-2 execution failed.")
            return False, []

        print("Boltz-2 execution completed.")

        # Download results
        print(f"Downloading results to {output_dir}...")
        downloaded = self.download_prefix("output/boltz2/", output_dir)
        print(f"  downloaded {len(downloaded)} files")
        return True, downloaded

    # ── PyRosetta Scoring ──────────────────────────────────────────────

    def run_pyrosetta_scoring(self, structure_files, output_dir="./output/pyrosetta",
                              binder_chain=None, relax=True, timeout=600):
        """Run PyRosetta interface scoring on Lyceum via Docker execution (CPU).

        Args:
            structure_files: Local path(s) to CIF/PDB complex files.
                Can be a single path (str/Path), a list of paths, or a directory.
            output_dir: Local directory to download results to.
            binder_chain: Binder chain ID (default: auto-detect shorter chain).
            relax: Whether to FastRelax before scoring (default: True).
            timeout: Timeout in seconds (default: 1800 = 30 min).

        Returns:
            (success: bool, downloaded_files: list[str])
        """
        structure_files = Path(structure_files) if isinstance(structure_files, str) else structure_files

        # Upload structure files to input/pyrosetta/
        print("Uploading structures for PyRosetta scoring...")
        if isinstance(structure_files, Path) and structure_files.is_dir():
            files = sorted(structure_files.glob("*.cif")) + sorted(structure_files.glob("*.pdb"))
            for f in files:
                self.upload_file(str(f), f"input/pyrosetta/{f.name}")
            input_flag = f"--input-dir /root/pyrosetta_work"
        elif isinstance(structure_files, (list, tuple)):
            for f in structure_files:
                f = Path(f)
                self.upload_file(str(f), f"input/pyrosetta/{f.name}")
            if len(structure_files) == 1:
                name = Path(structure_files[0]).name
                input_flag = f"--input /root/pyrosetta_work/{name}"
            else:
                input_flag = f"--input-dir /root/pyrosetta_work"
        else:
            # Single file
            name = structure_files.name
            self.upload_file(str(structure_files), f"input/pyrosetta/{name}")
            input_flag = f"--input /root/pyrosetta_work/{name}"

        # Build command
        cmd = (
            f"bash /mnt/s3/scripts/pyrosetta/run_pyrosetta.sh"
            f" {input_flag}"
            f" --output-dir /mnt/s3/output/pyrosetta"
        )
        if binder_chain:
            cmd += f" --binder-chain {binder_chain}"
        if not relax:
            cmd += " --no-relax"

        print(f"Submitting PyRosetta scoring job (CPU)...")
        exec_id, stream_url = self.submit_docker_job(
            docker_image="python:3.11-slim",
            command=cmd,
            execution_type="cpu",
            timeout=timeout,
        )
        print(f"  execution_id: {exec_id}")

        # Stream output
        success, output = self.stream_output(exec_id, stream_url)
        if not success:
            print("PyRosetta scoring failed.")
            return False, []

        print("PyRosetta scoring completed.")

        # Download results
        print(f"Downloading results to {output_dir}...")
        downloaded = self.download_prefix("output/pyrosetta/", output_dir)
        print(f"  downloaded {len(downloaded)} files")
        return True, downloaded

    # ── High-Level Run ───────────────────────────────────────────────────

    def run(self, script_path, requirements=None, input_files=None,
            output_prefix=None, output_dir=None, machine="gpu.a100", timeout=60,
            args=None, stream=True):
        """High-level: upload inputs → submit → stream/poll → download outputs.

        Args:
            script_path: Path to the .py file.
            requirements: Path to requirements.txt.
            input_files: Dict of {local_path: storage_key} to upload before execution.
            output_prefix: Storage prefix to download results from after completion.
            output_dir: Local directory to download results to.
            machine: Machine type (cpu, a100, h100).
            timeout: Timeout in seconds.
            args: List of script arguments.
            stream: If True, stream output in real-time.

        Returns:
            (success: bool, downloaded_files: list[str])
        """
        # Upload input files
        if input_files:
            print("Uploading inputs...")
            for local_path, storage_key in input_files.items():
                self.upload_file(local_path, storage_key)

        # Submit job
        print(f"Submitting {Path(script_path).name} on {machine}...")
        exec_id, stream_url = self.submit_python_job(
            script_path, requirements=requirements, machine=machine,
            timeout=timeout, args=args,
        )
        print(f"  execution_id: {exec_id}")

        # Wait for completion
        if stream:
            success, output = self.stream_output(exec_id, stream_url)
        else:
            success, status = self.wait_for_completion(exec_id)

        if not success:
            print("Execution failed.")
            return False, []

        print("Execution completed.")

        # Download outputs
        downloaded = []
        if output_prefix and output_dir:
            print(f"Downloading outputs from {output_prefix}...")
            downloaded = self.download_prefix(output_prefix, output_dir)
            print(f"  downloaded {len(downloaded)} files to {output_dir}")

        return True, downloaded
