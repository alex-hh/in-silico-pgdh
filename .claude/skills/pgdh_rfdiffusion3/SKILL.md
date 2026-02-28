---
name: pgdh_rfdiffusion3
description: >
  Run RFdiffusion3 binder design against 15-PGDH on Lyceum.
  Use this skill when: (1) Generating PGDH binder candidates with RFdiffusion3,
  (2) Running atomic-level binder design with hotspot specification,
  (3) Using the rosettacommons/foundry Docker image on Lyceum.

  For backbone-only RFdiffusion (v1), use rfdiffusion.
  For BoltzGen design, use boltzgen-pgdh.
  For scoring designs, use pgdh_ipsae.
  For the full campaign workflow, see pgdh_campaign/CAMPAIGN_PLAN.md.
license: MIT
category: design-tools
tags: [pgdh, rfdiffusion3, binder-design, lyceum, foundry]
lyceum_script: lyceum_rfdiffusion3.py
---

# RFdiffusion3 PGDH Binder Design (Lyceum)

Generate atomic-level protein binders targeting 15-PGDH (PDB: 2GDZ) using RFdiffusion3 on Lyceum.

RFdiffusion3 (RFD3) is the latest Baker lab protein design tool from the `RosettaCommons/foundry` framework. Unlike RFdiffusion v1 (backbone-only), RFD3 designs at the atomic level — full sidechain placement included.

## Target

- **Protein**: 15-PGDH (15-hydroxyprostaglandin dehydrogenase)
- **PDB**: 2GDZ (1.65 Å, chain A, 266 residues, NAD+ bound)
- **Active site**: Ser138(OG), Gln148(OE1/NE2), Tyr151(OH), Lys155(NZ), Phe185(CZ), Tyr217(OH)
- **Dimer interface**: Val150(CG1), Phe161(CZ), Leu167(CD2), Met172(SD), Tyr206(OH)

**Important**: Residue 150 is **Val** (not Leu) in the PDB. It has CG1/CG2, not CD1/CD2.

## JSON Config Format

RFD3 uses JSON configs (not YAML like BoltzGen). Each key is a design task name.

```json
{
    "task_name": {
        "dialect": 2,
        "infer_ori_strategy": "hotspots",
        "input": "/mnt/s3/input/rfdiffusion3/2GDZ.pdb",
        "contig": "60-120,/0,A0-265",
        "select_hotspots": {
            "A138": "OG",
            "A151": "OH"
        },
        "is_non_loopy": true
    }
}
```

### Contig syntax (RFD3)
- `60-120` = design 60-120 new residues (binder)
- `/0` = chain break
- `A0-265` = fix target residues A0-265 from input PDB
- Comma-separated (not space-separated like RFdiffusion v1)

### Hotspot specification
- Keys: `"A138"` = chain + residue number
- Values: atom names to target, comma-separated (e.g., `"OG"`, `"OH"`, `"CZ"`)
- **Must match actual atoms in the PDB** — validate before submitting

## 2 Binding Strategies

| # | Strategy | Config Key | Hotspot Residues | Binder Size |
|---|----------|------------|------------------|-------------|
| 1 | Active site blocker | `pgdh_active_site` | Ser138, Gln148, Tyr151, Lys155, Phe185, Tyr217 | 60-120 AA |
| 2 | Dimer disruptor | `pgdh_dimer_interface` | Val150, Phe161, Leu167, Met172, Tyr206 | 60-140 AA |

Config file: `pgdh_campaign/configs/rfd3_pgdh_binder.json`

### Validated hotspot atoms

**Active site** (all verified in 2GDZ.pdb):
```json
"select_hotspots": {
    "A138": "OG",
    "A148": "OE1,NE2",
    "A151": "OH",
    "A155": "NZ",
    "A185": "CZ",
    "A217": "OH"
}
```

**Dimer interface** (all verified in 2GDZ.pdb):
```json
"select_hotspots": {
    "A150": "CG1",
    "A161": "CZ",
    "A167": "CD2",
    "A172": "SD",
    "A206": "OH"
}
```

## Prerequisites

1. Lyceum auth: `lyceum auth login` (or check `~/.lyceum/config.json`)
2. Activate venv: `source .venv/bin/activate`
3. Target PDB: `pgdh_campaign/structures/2GDZ.pdb`
4. Lyceum script: `projects/biolyceum/src/lyceum_rfdiffusion3.py`
5. Docker image: `rosettacommons/foundry` (includes model weights)

## How to run

### Via Python client (recommended)

```python
import sys
sys.path.insert(0, "projects/biolyceum/src")
from utils.client import LyceumClient

client = LyceumClient()

# Upload inputs
client.upload_file("pgdh_campaign/configs/rfd3_pgdh_binder.json",
                   "input/rfdiffusion3/rfd3_pgdh_binder.json")
client.upload_file("pgdh_campaign/structures/2GDZ.pdb",
                   "input/rfdiffusion3/2GDZ.pdb")
client.upload_file("projects/biolyceum/src/lyceum_rfdiffusion3.py",
                   "scripts/rfdiffusion3/lyceum_rfdiffusion3.py")

# Submit Docker job
exec_id, stream_url = client.submit_docker_job(
    docker_image="rosettacommons/foundry",
    command=[
        "python", "/mnt/s3/scripts/rfdiffusion3/lyceum_rfdiffusion3.py",
        "--input-json", "/mnt/s3/input/rfdiffusion3/rfd3_pgdh_binder.json",
        "--output-dir", "/mnt/s3/output/rfdiffusion3/r{N}/{strategy}",  # Replace {N} with current round from CLAUDE.md
        "--num-designs", "4",
        "--num-batches", "1",
        "--step-scale", "3",
        "--gamma-0", "0.2",
    ],
    execution_type="gpu.a100",
    timeout=600,
)

# Stream and wait
success, output = client.stream_output(exec_id, stream_url)

# Download results
client.download_prefix("output/rfdiffusion3/r1/", "pgdh_campaign/out/rfd3/r1")
```

### Via submission script

```bash
source .venv/bin/activate
cd pgdh_campaign
python run_rfd3_pgdh.py
```

## Key CLI parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--num-designs` | 8 | `diffusion_batch_size` — designs per batch |
| `--num-batches` | 1 | Number of batches |
| `--num-timesteps` | 200 | Diffusion timesteps (more = higher quality, slower) |
| `--step-scale` | 1.5 | Step scale (higher = more exploration) |
| `--gamma-0` | 0.6 | Gamma parameter (lower = more diverse) |
| `--dump-trajectories` | false | Save diffusion trajectory frames |

### Recommended settings for PGDH

| Mode | `--num-designs` | `--step-scale` | `--gamma-0` | `--num-timesteps` |
|------|----------------|----------------|-------------|-------------------|
| Quick test | 2-4 | 3 | 0.2 | 200 |
| Production | 8-16 | 1.5 | 0.6 | 200 |
| High diversity | 8 | 3 | 0.2 | 200 |
| High quality | 4 | 1.5 | 0.8 | 500 |

## Output format

Each design produces two files:
- `<config>_<task>_<batch>_model_<N>.cif.gz` — atomic structure (gzipped CIF)
- `<config>_<task>_<batch>_model_<N>.json` — metadata + metrics

### Output metrics (from JSON)

| Metric | Good | Description |
|--------|------|-------------|
| `max_ca_deviation` | < 0.5 Å | CA deviation from ideal geometry |
| `n_chainbreaks` | 0 | Should be zero |
| `n_clashing.interresidue_clashes_w_sidechain` | 0 | Sidechain clashes |
| `n_clashing.interresidue_clashes_w_backbone` | 0 | Backbone clashes |
| `non_loop_fraction` | > 0.7 | Fraction of secondary structure (not loop) |
| `helix_fraction` | varies | Fraction of helical content |
| `radius_of_gyration` | 10-20 Å | Compactness (depends on size) |
| `alanine_content` | < 0.3 | Not dominated by alanines |
| `glycine_content` | < 0.15 | Not dominated by glycines |

### Example output (PGDH active site, 4 designs)

```
rfd3_pgdh_binder_pgdh_active_site_0_model_0.cif.gz  + .json
rfd3_pgdh_binder_pgdh_active_site_0_model_1.cif.gz  + .json
rfd3_pgdh_binder_pgdh_active_site_0_model_2.cif.gz  + .json
rfd3_pgdh_binder_pgdh_active_site_0_model_3.cif.gz  + .json
```

## Pipeline timing (PGDH, A100)

| Phase | 4 Designs | 8 Designs |
|-------|-----------|-----------|
| Queue + container pull | ~13 min (first run) | ~13 min |
| RFD3 inference | ~3-5 min | ~6-10 min |
| **Total** | ~16-18 min | ~19-23 min |

Container pull is cached after first run.

**API Stability (Feb 2026)**: Lyceum API has high latency. **Schedule at most 1 job
at a time** and wait for completion before submitting the next. The Python client
uses 120s API timeouts to handle this.

## Common errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Could not find requested atoms 'CD2'` | Hotspot atom doesn't exist on that residue | Verify atom names in PDB (e.g., Val has CG1/CG2, not CD1/CD2) |
| `422 Unprocessable Content` | `docker_run_cmd` passed as string | Must be a list of strings |
| `500 Internal Server Error` | Transient Lyceum API error | Retry after a few seconds |
| Designs all high-alanine | Step scale too low | Increase `--step-scale` to 3+ |

## Differences from RFdiffusion v1

| Feature | RFdiffusion v1 | RFdiffusion3 |
|---------|---------------|--------------|
| Output | Backbone only (polyAla PDB) | Full atomic (CIF with sidechains) |
| Config | Hydra CLI args | JSON config file |
| Contig syntax | Space-separated | Comma-separated |
| Hotspots | `ppi.hotspot_res=[A45,A67]` | `select_hotspots: {"A45": "atom_names"}` |
| Install | pip + weights download | `rosettacommons/foundry` Docker |
| Sequence design | Needs ProteinMPNN after | Included (atomic-level) |

## Next steps after RFD3

1. **Extract sequences**: Decompress `.cif.gz`, parse designed chain sequence
2. **Cross-validate**: Run through Boltz-2 (`/boltz`) for independent structure prediction
3. **Score**: Use `/pgdh_ipsae` skill to rank by binding confidence
4. **Filter**: Apply `/protein-qc` thresholds
5. **Select**: Pick top designs for submission

## Files

- Lyceum script: `projects/biolyceum/src/lyceum_rfdiffusion3.py`
- Client: `projects/biolyceum/src/utils/client.py` (`submit_docker_job()`)
- Submission script: `pgdh_campaign/run_rfd3_pgdh.py`
- JSON config: `pgdh_campaign/configs/rfd3_pgdh_binder.json`
- Target structure: `pgdh_campaign/structures/2GDZ.pdb`
- S3 output dir: `output/rfdiffusion3/r{N}/{strategy}/` (use round prefix)
- Local output dir: `pgdh_campaign/out/rfd3/`
- Submission name convention: `-f "pgdh_rfd3_r{N}_{strategy}"`
- Docker image: `rosettacommons/foundry`
- Machine: `gpu.a100`
