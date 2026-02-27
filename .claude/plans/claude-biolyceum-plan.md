# Biolyceum: Port Biomodals to Lyceum

## Context

Biomodals is a collection of 22 Modal-based protein design tools (RFdiffusion, BindCraft, Chai-1, etc.). Modal uses a decorator-based Python SDK where `@app.function()` transforms functions into cloud workloads with programmatic image building. Lyceum is a European sovereign GPU cloud that uses a CLI/REST API model — you submit Python scripts with requirements or Docker containers, with auto-mounted S3 storage at `/lyceum/storage/`. The goal is to port all biomodals entrypoints to work with Lyceum instead of Modal.

## Key Architectural Differences

| Modal | Lyceum |
|-------|--------|
| `modal.Image.debian_slim().pip_install(...)` | `requirements.txt` or inline `--import` flags |
| `@app.function(gpu="A100")` | `-m gpu.a100` CLI flag / `execution_type: "gpu"` in API |
| `@app.local_entrypoint()` | Local Python CLI that calls Lyceum CLI or REST API |
| `modal.Volume` | `/lyceum/storage/` auto-mounted S3 storage |
| `modal.Secret` | `docker_run_env` environment variables |
| `.remote()` call | `lyceum python run script.py -m gpu.a100 -r requirements.txt` |
| `run_function(download_models, gpu=...)` | Setup script or first-run download to `/lyceum/storage/` |

## Execution Strategy

**No Docker images to build/push.** Like biomodals (which never builds Docker images — Modal does it on-the-fly), biolyceum will use Lyceum's Python execution mode for simple cases and Docker execution with public base images for complex cases.

**Two modes:**
1. **Python execution** (simple tools): `lyceum python run lyceum_X.py -r requirements.txt -m gpu.a100` — Lyceum installs deps and runs the script. Good for tools with pip-only dependencies.
2. **Docker execution** (complex tools): `POST /execution/image/start` with a public base image (e.g. `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`) + setup commands. Good for tools needing conda, system packages, or compiled software.

**File I/O:** Upload inputs to Lyceum storage before execution, script reads from `/lyceum/storage/`, writes outputs there, then download locally after completion.

## Directory Structure

```
projects/biolyceum/
├── src/
│   ├── utils/
│   │   ├── client.py              # Lyceum API client (auth, submit, poll, upload/download)
│   │   ├── extract_ligands.py     # Ported from biomodals utils
│   │   └── extract_chain_as_mol2.py
│   ├── lyceum_esm2.py             # Standalone scripts (run on Lyceum)
│   ├── lyceum_chai1.py
│   ├── lyceum_minimap2.py
│   ├── requirements/              # Per-tool requirements files
│   │   ├── esm2.txt
│   │   ├── chai1.txt
│   │   └── ...
│   └── ...
├── tasks/
│   ├── 1-lyceum-docs-and-plan.md
│   ├── 2-check-lyceum-access.md
│   ├── 3-rewrite-utils.md
│   ├── 4-port-first-entrypoint.md
│   ├── 5-port-remaining-entrypoints.md
│   └── 6-rewrite-skills.md
└── README.md
```

No separate `cli/` or `dockerfiles/` directories. Each `lyceum_X.py` is a self-contained script that:
- Reads inputs from `/lyceum/storage/input/` (uploaded before execution)
- Runs the tool logic (same business logic as biomodals)
- Writes outputs to `/lyceum/storage/output/`

The `client.py` utility handles the local orchestration: upload → submit → poll → download.

## Per-Entrypoint Port Pattern

Each biomodals `modal_X.py` becomes:

1. **`src/lyceum_X.py`** — Business logic extracted from the `@app.function()` body. No Modal imports. Reads/writes from `/lyceum/storage/`. Includes a `if __name__ == "__main__"` block with argparse for parameters.
2. **`src/requirements/X.txt`** — Dependencies extracted from the `Image.pip_install(...)` chain.
3. **Entry in `client.py`** or a thin CLI wrapper — Calls `lyceum python run lyceum_X.py -r requirements/X.txt -m gpu.a100` (or uses the REST API for Docker-mode tools).

For tools needing conda/system packages (micromamba-based images in biomodals), use Docker execution mode with an appropriate public base image.

## Tasks

### Task 1: Lyceum docs & port plan (this task — done)

### Task 2: Check Lyceum access
- Install `lyceum-cli` (`pip install lyceum-cli`)
- Run `lyceum auth login`
- Verify with a minimal test: `lyceum python run "print('hello')"`
- Test with GPU: `lyceum python run "import torch; print(torch.cuda.is_available())" -m gpu`
- Check storage: upload a test file and verify it appears at `/lyceum/storage/`
- Check available credits via API

### Task 3: Write biolyceum utils
- **`client.py`**: Lyceum API client class wrapping:
  - Auth (API key from env var `LYCEUM_API_KEY`)
  - `upload_file(local_path, storage_key)` → POST `/storage/upload`
  - `download_file(storage_key, local_path)` → GET `/storage/download/{key}`
  - `list_files(prefix)` → GET `/storage/list-files`
  - `submit_job(script_path, requirements, machine, timeout, args)` — wraps `lyceum python run` or REST API
  - `wait_for_completion(execution_id, poll_interval)` → GET `/execution/{id}/status`
  - `run(script, requirements, input_files, output_prefix, machine, timeout)` — high-level: upload inputs → submit → poll → download outputs
- Port `extract_ligands.py` and `extract_chain_as_mol2.py` (minimal changes — remove any Modal dependencies)

### Task 4: Port first simple entrypoint — `esm2_predict_masked`
- Good first candidate: single function, optional GPU, pip-only deps (torch, fair-esm, pandas, matplotlib)
- Write `src/lyceum_esm2.py` — extract business logic from `modal_esm2_predict_masked.py`
- Write `src/requirements/esm2.txt`
- Test interactively: `lyceum python run lyceum_esm2.py -r requirements/esm2.txt -m gpu`
- Verify outputs match biomodals

### Task 5: Port remaining entrypoints (one at a time)
Priority order (simple → complex):
1. minimap2 (no GPU, simplest — but needs apt/compiled binary, may need Docker mode)
2. anarci (no GPU, subprocess)
3. chai1 (GPU, medium complexity, pip-only deps)
4. ligandmpnn (GPU, subprocess-based)
5. boltz (GPU, uses Volume → `/lyceum/storage/`)
6. alphafold (GPU, complex scoring)
7. proteinmpnn (via ligandmpnn pattern)
8. rfdiffusion, bindcraft, germinal, etc. (complex)

### Task 6: Rewrite skills for biolyceum
- Update `.claude/skills/setup/SKILL.md` to include Lyceum setup path
- Update `_shared/compute-setup.md` to include Lyceum option
- For each tool skill (chai, boltz, rfdiffusion, etc.), add Lyceum command examples alongside Modal ones

## `/bioltask` Skill

A skill that loads a task file and guides the user through it:

```
/bioltask <task_number>
```

Reads `projects/biolyceum/tasks/<task_number>-*.md` and displays the task description, acceptance criteria, and suggested steps. Acts as a guide — shows what to do, lets the user decide when to proceed.

## Critical Files

- **Biomodals source**: `/Users/alex/projects/biohack/resources/biomodals/modal_*.py`
- **Biomodals utils**: `/Users/alex/projects/biohack/resources/biomodals/utils/`
- **Skills**: `/Users/alex/projects/biohack/.claude/skills/`
- **Shared compute setup**: `/Users/alex/projects/biohack/.claude/skills/_shared/compute-setup.md`
- **Plan file**: `/Users/alex/projects/biohack/.claude/plans/biolyceum.md`

## Lyceum API Reference

- Base URL: `https://api.lyceum.technology/api/v2/external`
- Python exec CLI: `lyceum python run script.py -r requirements.txt -m gpu.a100`
- Docker exec: `POST /execution/image/start` — `{docker_image_ref, docker_run_cmd, execution_type, docker_run_env, timeout}`
- Storage upload: `POST /storage/upload` — multipart form with `file` and `key`
- Storage download: `GET /storage/download/{file_key}`
- Storage list: `GET /storage/list-files?prefix=...`
- Status: `GET /execution/streaming/{id}/status`
- Auth: Bearer token via API key or JWT
- Auto-mount: `/lyceum/storage/` available in all executions
- GPU types: `gpu` (T4), `gpu.a100` (A100 40GB), `gpu.h100` (H100 80GB), `gpu.b200` (B200 180GB)

## Verification

For each ported entrypoint:
1. Submit via `lyceum python run` or Docker API with test input
2. Verify outputs match biomodals outputs (same tool, same input → same results)
3. Test interactively first, then via `client.py` wrapper

For the `/bioltask` skill:
- Run `/bioltask 4` and verify it loads the correct task file and guides execution
