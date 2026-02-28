---
name: lyceum_ipsae
description: >
  Run ipSAE scoring on Lyceum. Use this skill when you need to score
  protein-protein interactions from AF2/AF3/Boltz predictions on Lyceum
  (CPU, no GPU needed). Computes ipSAE, pDockQ, pDockQ2, LIS scores.
license: MIT
category: evaluation
tags: [ranking, scoring, binding, lyceum]
lyceum_script: lyceum_ipsae.py
---

# ipSAE on Lyceum

## What ipSAE needs

ipSAE scores the **predicted confidence** of a protein-protein interaction. Both input
files must come from a structure predictor (AF2, AF3, or Boltz) that predicted your
binder + target as a 2-chain complex:

- **PAE file** — the NxN predicted aligned error matrix from the predictor
- **Structure file** — the predicted complex structure (2+ chains with coordinates)

You cannot use a raw crystal structure (e.g. `2GDZ.pdb`) — it has no PAE matrix and
is typically single-chain. The workflow is: design binder → predict complex → score.

## How to run

### Via Lyceum CLI

Upload both prediction output files, then run:

```bash
lyceum storage upload boltz2_output/pae.json input/pae.json
lyceum storage upload boltz2_output/model_0.cif input/model.cif

lyceum python run lyceum_ipsae.py -r requirements/ipsae.txt -m cpu \
    -- --pae-file /job/work/input/pae.json \
       --structure-file /job/work/input/model.cif \
       --pae-cutoff 10 --dist-cutoff 10
```

### Via Python client

```python
from projects.biolyceum.src.utils.client import LyceumClient

client = LyceumClient()
success, files = client.run(
    script_path="projects/biolyceum/src/lyceum_ipsae.py",
    requirements="projects/biolyceum/src/requirements/ipsae.txt",
    machine="cpu",
    timeout=120,
    args=[
        "--pae-file", "/job/work/input/scores.json",
        "--structure-file", "/job/work/input/design.pdb",
        "--pae-cutoff", "10",
        "--dist-cutoff", "10",
        "--output-dir", "/job/work/output/ipsae",
    ],
    input_files={
        "local/scores.json": "input/scores.json",
        "local/design.pdb": "input/design.pdb",
    },
    output_prefix="output/ipsae/",
    output_dir="./out/ipsae/",
)
```

### Local (no Lyceum)

```bash
python projects/biolyceum/src/lyceum_ipsae.py \
    --pae-file scores.json \
    --structure-file design.pdb \
    --output-dir ./out/ipsae
```

## Input formats

| Predictor | PAE file | Structure file |
|-----------|----------|----------------|
| AlphaFold2 | `.json` | `.pdb` |
| AlphaFold3 | `.json` | `.cif` |
| Boltz1 | `.npz` | `.cif` |

Format is auto-detected from file extensions.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--pae-file` | required | PAE file (JSON or NPZ) |
| `--structure-file` | required | Structure file (PDB or CIF) |
| `--pae-cutoff` | 10.0 | PAE threshold for contacts (A) |
| `--dist-cutoff` | 10.0 | Max CB-CB distance (A) |
| `--output-dir` | `/job/work/output/ipsae` | Output directory |

## Output

Three files per run (e.g. with cutoffs 10/10):

| File | Contents |
|------|----------|
| `*_10_10.txt` | Chain-pair summary (ipSAE, pDockQ, pDockQ2, LIS) |
| `*_10_10_byres.txt` | Per-residue ipSAE scores |
| `*_10_10.pml` | PyMOL visualization script |

## Thresholds

| Metric | Pass | Stringent |
|--------|------|-----------|
| ipSAE | > 0.61 | > 0.70 |
| pDockQ | > 0.5 | > 0.6 |
| LIS | > 0.35 | > 0.45 |

## Files

- Script: `projects/biolyceum/src/lyceum_ipsae.py`
- Requirements: `projects/biolyceum/src/requirements/ipsae.txt`
- Machine: CPU (no GPU needed)
