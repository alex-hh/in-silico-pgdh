# Task 3: Write Biolyceum Utils

## Status: Pending

## Objective
Create the shared utility modules that all biolyceum entrypoints depend on.

## Files to Create

### `src/utils/client.py` — Lyceum API Client

A Python class wrapping the Lyceum REST API and CLI for job orchestration.

**Class: `LyceumClient`**

```python
class LyceumClient:
    def __init__(self, api_key=None, base_url=None):
        # api_key from param or LYCEUM_API_KEY env var
        # base_url defaults to https://api.lyceum.technology/api/v2/external

    def upload_file(self, local_path, storage_key):
        # POST /storage/upload — multipart form with file + key

    def download_file(self, storage_key, local_path):
        # GET /storage/download/{file_key}

    def list_files(self, prefix=""):
        # GET /storage/list-files?prefix=...

    def submit_python_job(self, script_path, requirements=None, machine="gpu", timeout=3600, args=None):
        # Wraps: lyceum python run script.py -r requirements.txt -m gpu.a100
        # Or POST to execution API

    def submit_docker_job(self, docker_image, command, execution_type="gpu", env=None, timeout=3600):
        # POST /execution/image/start

    def get_status(self, execution_id):
        # GET /execution/streaming/{id}/status

    def wait_for_completion(self, execution_id, poll_interval=10, timeout=3600):
        # Poll get_status until done or timeout

    def run(self, script_path, requirements=None, input_files=None, output_prefix=None, machine="gpu", timeout=3600):
        # High-level: upload inputs → submit → poll → download outputs
        # input_files: dict of {local_path: storage_key}
        # output_prefix: prefix to download from after completion
```

**Key design decisions:**
- Use `httpx` for HTTP (async-capable, good timeout handling)
- Fall back to `requests` if httpx not available
- Support both CLI and REST API paths
- Print progress during polling (status, elapsed time)

### `src/utils/extract_ligands.py` — Port from biomodals

Port from `resources/biomodals/utils/extract_ligands.py`:
- Remove any Modal imports
- Keep all business logic identical
- Dependencies: prody, rdkit, pypdb, pandas

### `src/utils/extract_chain_as_mol2.py` — Port from biomodals

Port from `resources/biomodals/utils/extract_chain_as_mol2.py`:
- Remove any Modal imports
- Keep all business logic identical
- Dependencies: prody, Open Babel (obabel binary)

### `src/utils/__init__.py`
Empty or minimal imports.

## Acceptance Criteria
- [ ] `client.py` handles auth, upload, download, submit, poll, and high-level `run()`
- [ ] `extract_ligands.py` works without Modal imports
- [ ] `extract_chain_as_mol2.py` works without Modal imports
- [ ] Client tested with a basic upload/download round-trip (depends on task 2)

## Dependencies on Other Tasks
- Task 2 must be done first (need working Lyceum access to test client)
