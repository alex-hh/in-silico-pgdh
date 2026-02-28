---
name: pgdh_boltz2
description: >
  Run Boltz-2 cross-validation of PGDH binder designs on Lyceum.
  Use this skill when: (1) Cross-validating BoltzGen/RFdiffusion3 binder designs with an independent predictor,
  (2) Running Boltz-2 structure prediction for PGDH binder-target complexes,
  (3) Generating confidence metrics (ipTM, pTM, pLDDT) for designed binders.

  For BoltzGen design, use boltzgen-pgdh.
  For RFdiffusion3 design, use pgdh_rfdiffusion3.
  For ipSAE scoring after Boltz-2, use pgdh_ipsae.
  For the full campaign workflow, see pgdh_campaign/CAMPAIGN_PLAN.md.
license: MIT
category: validation
tags: [pgdh, boltz2, cross-validation, structure-prediction, lyceum]
lyceum_script: lyceum_boltz2.py
---

# Boltz-2 PGDH Cross-Validation (Lyceum)

Cross-validate 15-PGDH (PDB: 2GDZ) binder designs using Boltz-2 structure prediction on Lyceum.

Boltz-2 uses MSAs (via ColabFold server) and is architecturally independent from BoltzGen's diffusion model, making it a good orthogonal validator.

## Target

- **Protein**: 15-PGDH (15-hydroxyprostaglandin dehydrogenase)
- **PDB**: 2GDZ (1.65 A, homodimer, NAD+ bound)
- **Chain A sequence**: `AHMVNGKVALVTGAAQGIGRAFAEALLLKGAKVALVDWNLEAGVQCKAALHEQFEPQKTLFIQCDVADQQQLRDTFRKVVDHFGRLDILVNNAGVNNEKNWEKTLQINLVSVISGTYLGLDYMSKQNGGEGGIIINMSSLAGLMPVAQQPVYCASKHGIVGFTRSAALAANLMNSGVRLNAICPGFVNTAILESIEKEENMGQYIEYKDHIKDMIKYYGILDPPLIANGLITLIEDDALNGAIMKITTSKGIHFQDYGSKENLYFQ`

## YAML Input Format

Boltz-2 takes YAML files specifying target + binder sequences:

```yaml
sequences:
  - protein:
      id: A
      sequence: AHMVNGKVALVTGAAQGIGRAFAEALLLKGAKVALVDWNLEAGVQCKAALHEQFEPQKTLFIQCDVADQQQLRDTFRKVVDHFGRLDILVNNAGVNNEKNWEKTLQINLVSVISGTYLGLDYMSKQNGGEGGIIINMSSLAGLMPVAQQPVYCASKHGIVGFTRSAALAANLMNSGVRLNAICPGFVNTAILESIEKEENMGQYIEYKDHIKDMIKYYGILDPPLIANGLITLIEDDALNGAIMKITTSKGIHFQDYGSKENLYFQ
  - protein:
      id: B
      sequence: <BINDER_SEQUENCE_HERE>
```

Chain A = 15-PGDH target, Chain B = designed binder.

## Prerequisites

1. Lyceum auth: `lyceum auth login` (or check `~/.lyceum/config.json`)
2. Activate venv: `source .venv/bin/activate`
3. Scripts uploaded to Lyceum storage:
   - `scripts/boltz2/lyceum_boltz2.py`
   - `scripts/boltz2/run_boltz2.sh`
4. Boltz-2 models cached at `/mnt/s3/models/boltz2/` (auto-downloaded on first run)

## How to run

### Step 1: Create YAML input from design sequence

Extract the binder sequence from a BoltzGen/RFdiffusion3 design and create a YAML:

```python
# Example: extract binder sequence from a design CIF
from Bio.PDB import MMCIFParser
from Bio.PDB.Polypeptide import protein_letters_3to1

PGDH_SEQ = "AHMVNGKVALVTGAAQGIGRAFAEALLLKGAKVALVDWNLEAGVQCKAALHEQFEPQKTLFIQCDVADQQQLRDTFRKVVDHFGRLDILVNNAGVNNEKNWEKTLQINLVSVISGTYLGLDYMSKQNGGEGGIIINMSSLAGLMPVAQQPVYCASKHGIVGFTRSAALAANLMNSGVRLNAICPGFVNTAILESIEKEENMGQYIEYKDHIKDMIKYYGILDPPLIANGLITLIEDDALNGAIMKITTSKGIHFQDYGSKENLYFQ"

parser = MMCIFParser(QUIET=True)
structure = parser.get_structure("design", "design.cif")
# Get binder chain (usually chain B)
binder_chain = structure[0]["B"]
binder_seq = "".join(
    protein_letters_3to1.get(res.get_resname(), "X")
    for res in binder_chain.get_residues()
    if res.id[0] == " "
)

yaml_content = f"""sequences:
  - protein:
      id: A
      sequence: {PGDH_SEQ}
  - protein:
      id: B
      sequence: {binder_seq}
"""

with open(f"/tmp/boltz2_{design_name}.yaml", "w") as f:
    f.write(yaml_content)
```

### Step 2: Upload and run on Lyceum

```bash
source .venv/bin/activate

# Upload YAML
lyceum storage load /tmp/boltz2_candidate.yaml --key input/boltz2/boltz2_candidate.yaml

# Run Boltz-2
lyceum docker run pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime \
  -m gpu.a100 \
  -t 600 \
  -f "pgdh_boltz2_candidate" \
  -c "bash /mnt/s3/scripts/boltz2/run_boltz2.sh \
      --input-yaml /root/boltz2_work/boltz2_candidate.yaml \
      --output-dir /mnt/s3/output/boltz2 \
      --recycling-steps 10 \
      --diffusion-samples 5 \
      --cache /mnt/s3/models/boltz2 \
      --use-msa-server"
```

### Step 3: Download results immediately

**IMPORTANT**: Download results right after the job completes. Lyceum storage is NOT guaranteed to persist.

```bash
# List outputs
lyceum storage ls output/boltz2/

# Download structure and confidence
mkdir -p pgdh_campaign/out/boltz2/candidate/
lyceum storage download "output/boltz2/predictions/boltz2_candidate/model_0.cif" \
  --output pgdh_campaign/out/boltz2/candidate/model_0.cif
lyceum storage download "output/boltz2/predictions/boltz2_candidate/confidence_model_0.json" \
  --output pgdh_campaign/out/boltz2/candidate/confidence_model_0.json
```

### Via Python client

```python
from projects.biolyceum.src.utils.client import LyceumClient

client = LyceumClient()
success, files = client.run_boltz2(
    yaml_path="/tmp/boltz2_candidate.yaml",
    output_dir="pgdh_campaign/out/boltz2/candidate",
    recycling_steps=10,
    diffusion_samples=5,
    use_msa_server=True,
    machine="gpu.a100",
    timeout=600,
)
```

### Batch mode (multiple candidates)

Create a directory of YAMLs and run in batch:

```bash
# Create YAMLs for each candidate
mkdir -p /tmp/boltz2_batch/
# ... create /tmp/boltz2_batch/design_1.yaml, design_2.yaml, etc.

# Upload all
for f in /tmp/boltz2_batch/*.yaml; do
  lyceum storage load "$f" --key "input/boltz2/$(basename $f)"
done

# Run batch
lyceum docker run pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime \
  -m gpu.a100 \
  -t 600 \
  -f "pgdh_boltz2_batch" \
  -c "bash /mnt/s3/scripts/boltz2/run_boltz2.sh \
      --input-dir /root/boltz2_work \
      --output-dir /mnt/s3/output/boltz2 \
      --recycling-steps 10 \
      --diffusion-samples 5 \
      --cache /mnt/s3/models/boltz2 \
      --use-msa-server"
```

**Note**: Each complex takes ~3-5 min on A100. Batch 2-3 designs per 600s job.

**API Stability (Feb 2026)**: Lyceum API has high latency. **Schedule at most 1 job
at a time.** The Python client uses 120s API timeouts. Alternatively, use
`server/run_boltz2.py` on a standalone A100 VM — see `server/README.md`.

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--recycling-steps` | 10 | More = higher accuracy, slower |
| `--diffusion-samples` | 5 | Number of structure samples |
| `--use-msa-server` | enabled | ColabFold MSA server for coevolutionary signal |
| `--no-msa-server` | - | Disable MSA (faster but less accurate) |
| `--seed` | 42 | Random seed for reproducibility |

## Output structure

```
output/boltz2/
└── predictions/
    └── <yaml_stem>/
        ├── model_0.cif              # Predicted complex structure
        └── confidence_model_0.json  # Confidence scores
```

## Key metrics from Boltz-2

| Metric | Good | Strong | Description |
|--------|------|--------|-------------|
| ipTM | > 0.5 | > 0.7 | Interface confidence (primary metric) |
| pTM | > 0.6 | > 0.8 | Global fold confidence |
| pLDDT | > 0.7 | > 0.85 | Per-residue confidence |

## Cross-validation interpretation

Compare Boltz-2 ipTM with BoltzGen's original ipTM:

| BoltzGen ipTM | Boltz-2 ipTM | Interpretation |
|---------------|--------------|----------------|
| High | High | Strong candidate - both methods agree |
| High | Low | Possible false positive from BoltzGen |
| Low | High | Unlikely but worth investigating |
| Low | Low | Weak candidate - skip |

Designs where both BoltzGen and Boltz-2 show ipTM > 0.5 are the strongest candidates for experimental testing.

## Pipeline timing (PGDH + binder, A100)

| Phase | Time |
|-------|------|
| First run (install + download models) | ~5-8 min |
| Subsequent runs (per complex) | ~3-5 min |
| MSA generation (per complex) | ~1-2 min |
| Structure prediction (per complex) | ~2-3 min |

First run on a fresh cache will spend time installing boltz and downloading models (~3 GB). Subsequent runs are much faster since everything is cached on S3.

## Campaign integration

This is Step 4 of the PGDH campaign (see `pgdh_campaign/CAMPAIGN_PLAN.md`):

```
Step 1-2: BoltzGen/RFdiffusion3 → generate designs
Step 3:   protein-qc filter → ~30 candidates
Step 4:   >>> Boltz-2 cross-validation <<< (this skill)
Step 5:   pgdh_ipsae scoring → rank by ipSAE
Step 6:   Select top 10 for submission
```

## Next steps after Boltz-2

1. **Score**: Use `/pgdh_ipsae` to compute ipSAE from Boltz-2 outputs (needs PAE + structure)
2. **Rank**: Compare ipSAE scores across all candidates
3. **Select**: Pick top 10 for experimental testing

## Files

- Lyceum scripts: `projects/biolyceum/src/lyceum_boltz2.py`, `run_boltz2.sh`
- Client: `projects/biolyceum/src/utils/client.py` (`run_boltz2()` method)
- Target structure: `pgdh_campaign/structures/2GDZ.cif`
- Campaign plan: `pgdh_campaign/CAMPAIGN_PLAN.md`
- Docker image: `pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime`
- Machine: `gpu.a100`
