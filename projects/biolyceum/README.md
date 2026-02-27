# Biolyceum

Port of [biomodals](../resources/biomodals/) protein design tools from Modal to [Lyceum](https://lyceum.technology), a European sovereign GPU cloud.

## Lyceum Usage

### Authentication

```bash
pip install lyceum-cli
lyceum auth login
```

Auth config is stored at `~/.lyceum/config.json` (JWT token + refresh token).

### Execution Modes

**Python execution** (simple tools with pip-only dependencies):
```bash
lyceum python run script.py -r requirements.txt -m gpu.a100
```

**Docker execution** (tools needing system packages, compiled binaries, conda):
```bash
lyceum docker run pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime \
  -m gpu.a100 -t 600 -c "bash /mnt/s3/scripts/run.sh"
```

### Machine Types

| Type | GPU | Notes |
|------|-----|-------|
| `cpu` | None | Default |
| `gpu` | T4 | Unreliable (hangs), avoid |
| `gpu.a100` | A100 | Recommended for most tools |
| `gpu.a100.40gb` | A100 40GB | |
| `gpu.a100.80gb` | A100 80GB | |
| `gpu.l40s` | L40S | |
| `gpu.h100` | H100 | |

Max timeout: 600 seconds per execution.

### Persistent Storage

Lyceum provides an **S3-backed persistent storage bucket** per user account. Files persist across executions.

- **Python execution mode**: mounted at `/job/work/`
- **Docker execution mode**: mounted at `/mnt/s3/`
- **CLI access**: `lyceum storage load`, `lyceum storage download`, `lyceum storage ls`

```bash
# Upload a file
lyceum storage load ./input.faa --key input/my_input.faa

# List files
lyceum storage ls
lyceum storage ls input/

# Download a file
lyceum storage download output/results.tsv --output ./results.tsv

# Delete
lyceum storage rm test/file.txt
lyceum storage rmdir test/       # Delete all files under prefix
```

Inside a **Python execution**, read/write via `/job/work/`:
```python
# Read uploaded input
data = open("/job/work/input/my_input.faa").read()

# Write output (persists after execution ends)
with open("/job/work/output/results.tsv", "w") as f:
    f.write(results)
```

Inside a **Docker execution**, read/write via `/mnt/s3/`:
```python
data = open("/mnt/s3/input/my_input.faa").read()
```

Use persistent storage to **cache model weights** so they don't re-download every run:
```bash
# Models cached at /mnt/s3/models/ or /job/work/models/ persist across runs
```

### Script Arguments

Pass arguments to scripts after `--`:
```bash
lyceum python run script.py -r requirements.txt -m gpu.a100 \
  -- --input /job/work/input/data.faa --output-dir /job/work/output/run1
```

### Environment Variables in Docker

```bash
lyceum docker run myimage:latest -e "KEY=value" -e "OTHER=val2" -c "python run.py"
```

## Project Structure

```
src/
├── utils/
│   ├── client.py              # Lyceum API client (auth, upload, submit, poll, download)
│   ├── extract_ligands.py     # PDB ligand extraction (ported from biomodals)
│   └── extract_chain_as_mol2.py
├── lyceum_esm2.py             # ESM2 masked prediction
├── lyceum_boltzgen.py         # BoltzGen all-atom design
├── requirements/
│   ├── esm2.txt
│   └── ...
└── run_boltzgen.sh            # Docker setup script for BoltzGen
tasks/                         # Task files for porting progress
```

## Ported Tools

| Tool | Status | Mode | Machine |
|------|--------|------|---------|
| ESM2 predict masked | Done | Python | gpu.a100 |
| BoltzGen | In progress | Docker | gpu.a100 |
| Minimap2 | Pending | Docker | cpu |
| ANARCI | Pending | Docker | cpu |
| Chai-1 | Pending | Python | gpu.a100 |
| LigandMPNN | Pending | Docker | gpu.a100 |
| Boltz | Pending | Docker | gpu.a100 |
| AlphaFold | Pending | Docker | gpu.a100 |
| RFdiffusion | Pending | Docker | gpu.a100 |
| BindCraft | Pending | Docker | gpu.a100 |

## Usage Examples

### ESM2 (Python mode)
```bash
# Upload input
lyceum storage load input.faa --key input/test.faa

# Run
lyceum python run src/lyceum_esm2.py \
  -r src/requirements/esm2.txt -m gpu.a100 \
  -- --input /job/work/input/test.faa --output-dir /job/work/output/esm2

# Download results
lyceum storage download output/esm2/test.results.tsv --output ./results.tsv
```

### Via Python client
```python
from src.utils.client import LyceumClient

client = LyceumClient()
success, files = client.run(
    script_path="src/lyceum_esm2.py",
    requirements="src/requirements/esm2.txt",
    input_files={"input.faa": "input/test.faa"},
    output_prefix="output/esm2/",
    output_dir="./output",
    machine="gpu.a100",
)
```
