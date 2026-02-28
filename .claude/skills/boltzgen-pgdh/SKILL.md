---
name: boltzgen-pgdh
description: >
  Run BoltzGen binder design against 15-PGDH on Lyceum.
  Use this skill when: (1) Generating PGDH binder candidates on Lyceum,
  (2) Running any of the 3 PGDH binding strategies (active site, dimer interface, surface),
  (3) Managing BoltzGen PGDH design campaigns end-to-end.

  For general BoltzGen usage, use boltzgen.
  For scoring designs, use pgdh_ipsae.
  For the full campaign workflow, see pgdh_campaign/CAMPAIGN_PLAN.md.
license: MIT
category: design-tools
tags: [pgdh, boltzgen, binder-design, lyceum]
lyceum_script: lyceum_boltzgen.py
---

# BoltzGen PGDH Binder Design (Lyceum)

Generate protein binders targeting 15-PGDH (PDB: 2GDZ) using BoltzGen on Lyceum.

## Target

- **Protein**: 15-PGDH (15-hydroxyprostaglandin dehydrogenase)
- **PDB**: 2GDZ (1.65 Å, homodimer, NAD+ bound)
- **Chain A**: 266 residues
- **Active site**: Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217
- **Dimer interface**: Phe161, Leu150, Ala153, Ala146, Leu167, Ala168

## 3 Binding Strategies

| # | Strategy | Config File | Hotspots | Binder Size |
|---|----------|-------------|----------|-------------|
| 1 | Active site blocker | `strategy1_active_site.yaml` | Catalytic + substrate residues | 80-120 AA |
| 2 | Dimer disruptor | `strategy2_dimer_interface.yaml` | Interface residues | 80-140 AA |
| 3 | Surface (model-free) | `strategy3_surface.yaml` | None (auto-detect) | 60-140 AA |

Configs are in `pgdh_campaign/configs/`. Residue numbers use `label_seq_id` (= `auth_seq_id` + 2 for 2GDZ).

## Prerequisites

1. Lyceum auth: `lyceum auth login` (or check `~/.lyceum/config.json`)
2. Activate venv: `source .venv/bin/activate`
3. Scripts already uploaded to Lyceum storage:
   - `scripts/boltzgen/lyceum_boltzgen.py`
   - `scripts/boltzgen/run_boltzgen.sh`
4. BoltzGen models cached at `/mnt/s3/models/boltzgen/` (auto-downloaded on first run)

## How to run

### Step 1: Upload input files

The YAML configs reference `../structures/2GDZ.cif` for local use. For Lyceum, create flat-path versions and upload:

```bash
source .venv/bin/activate

# Upload the target structure
lyceum storage load pgdh_campaign/structures/2GDZ.cif --key input/boltzgen/2GDZ.cif

# Upload the YAML config (modify path from ../structures/2GDZ.cif to 2GDZ.cif)
# The run_boltzgen.sh script copies all input/boltzgen/* to /root/boltzgen_work/
lyceum storage load /path/to/modified_config.yaml --key input/boltzgen/config.yaml
```

**Important**: In the Lyceum YAML, change `path: ../structures/2GDZ.cif` to `path: 2GDZ.cif` since all input files are copied to the same working directory.

### Step 2: Run BoltzGen

```bash
# Option A: Custom image (fast — skips ~2min setup)
lyceum docker run alexhh/boltzgen:latest \
  -m gpu.a100 \
  -t 600 \
  -c "bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh \
      --input-yaml /root/boltzgen_work/config.yaml \
      --output-dir /mnt/s3/output/boltzgen \
      --num-designs 10 \
      --cache /mnt/s3/models/boltzgen"

# Option B: Generic image (slower — installs boltzgen each run)
lyceum docker run pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime \
  -m gpu.a100 \
  -t 600 \
  -c "bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh \
      --input-yaml /root/boltzgen_work/config.yaml \
      --output-dir /mnt/s3/output/boltzgen \
      --num-designs 10 \
      --cache /mnt/s3/models/boltzgen"
```

The `run_boltzgen.sh` script auto-detects whether `boltzgen` is pre-installed and skips setup if so.

**Parameters**:
- `--num-designs`: Number of designs (3-10 for testing, 50 for production)
- `--protocol`: `protein-anything` (default, correct for binder design)
- `-t 600`: Max timeout (600s). Custom image: ~5 designs fit in 600s. Generic: ~3.

**API Stability (Feb 2026)**: Lyceum API has high latency. The Python client uses
120s timeouts for API calls. **Schedule at most 1 job at a time** and wait for it
to complete before submitting the next. Alternatively, use the standalone A100 server
scripts in `server/` to bypass Lyceum entirely.

### Step 3: Download results

```bash
# List outputs
lyceum storage ls output/boltzgen/final_ranked_designs/

# Download all results
lyceum storage download output/boltzgen/final_ranked_designs/all_designs_metrics.csv \
  --output pgdh_campaign/out/boltzgen/all_designs_metrics.csv

# Download ranked designs
mkdir -p pgdh_campaign/out/boltzgen/designs
for f in $(lyceum storage ls output/boltzgen/final_ranked_designs/final_30_designs/ | grep '.cif'); do
  lyceum storage download "output/boltzgen/final_ranked_designs/final_30_designs/$f" \
    --output "pgdh_campaign/out/boltzgen/designs/$f"
done

# Download summary PDF
lyceum storage download output/boltzgen/final_ranked_designs/results_overview.pdf \
  --output pgdh_campaign/out/boltzgen/results_overview.pdf
```

### Via Python client

```python
from projects.biolyceum.src.utils.client import LyceumClient

client = LyceumClient()
success, files = client.run_boltzgen(
    yaml_path="pgdh_campaign/configs/strategy3_surface_lyceum.yaml",
    structure_files=["pgdh_campaign/structures/2GDZ.cif"],
    output_dir="pgdh_campaign/out/boltzgen",
    protocol="protein-anything",
    num_designs=10,
    machine="gpu.a100",
    timeout=600,
)
```

## Running all 3 strategies

**IMPORTANT: Download results immediately after each run.** Lyceum storage is NOT
guaranteed to persist — files can disappear between sessions. Always download
metrics CSVs and CIF files locally before starting the next run.

Each strategy writes to its own output subdirectory to avoid overwriting:

```bash
# Strategy 1: Active site
lyceum storage load /tmp/strategy1_lyceum.yaml --key input/boltzgen/config.yaml
lyceum docker run pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime \
  -m gpu.a100 -t 600 \
  -c "bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh \
      --input-yaml /root/boltzgen_work/config.yaml \
      --output-dir /mnt/s3/output/boltzgen/s1_active_site \
      --num-designs 3 --cache /mnt/s3/models/boltzgen"

# >>> DOWNLOAD S1 RESULTS IMMEDIATELY <<<
mkdir -p pgdh_campaign/out/boltzgen/s1_active_site/designs
lyceum storage download output/boltzgen/s1_active_site/final_ranked_designs/all_designs_metrics.csv \
  --output pgdh_campaign/out/boltzgen/s1_active_site/all_designs_metrics.csv
# Download each CIF from final_30_designs/ too

# Strategy 2: Dimer interface
lyceum storage load /tmp/strategy2_lyceum.yaml --key input/boltzgen/config.yaml
lyceum docker run pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime \
  -m gpu.a100 -t 600 \
  -c "bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh \
      --input-yaml /root/boltzgen_work/config.yaml \
      --output-dir /mnt/s3/output/boltzgen/s2_dimer \
      --num-designs 3 --cache /mnt/s3/models/boltzgen"

# >>> DOWNLOAD S2 RESULTS IMMEDIATELY <<<

# Strategy 3: Surface
lyceum storage load /tmp/strategy3_lyceum.yaml --key input/boltzgen/config.yaml
lyceum docker run pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime \
  -m gpu.a100 -t 600 \
  -c "bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh \
      --input-yaml /root/boltzgen_work/config.yaml \
      --output-dir /mnt/s3/output/boltzgen/s3_surface \
      --num-designs 3 --cache /mnt/s3/models/boltzgen"

# >>> DOWNLOAD S3 RESULTS IMMEDIATELY <<<
```

**NEVER run `lyceum storage rmdir output/boltzgen/`** without downloading first — this permanently deletes all results.

**Note**: Lyceum max timeout is 600s. With cached models, BoltzGen takes ~3-5 min per design for PGDH-sized targets (266 residues). For 50 designs, you'll need multiple runs of ~10 designs each.

## Pipeline timing (PGDH, A100)

| Phase | Per Design | 3 Designs | 10 Designs |
|-------|-----------|-----------|------------|
| Design (diffusion) | ~30s | ~90s | ~300s |
| Inverse folding | ~3s | ~10s | ~30s |
| Folding validation | ~20s | ~60s | ~200s |
| Design folding | ~15s | ~45s | ~150s |
| Analysis + filtering | ~15s | ~15s | ~30s |
| **Total** | ~80s | ~220s | ~710s |

For 10+ designs, split across multiple 600s jobs.

## Output structure

```
output/boltzgen/
├── config/                              # Pipeline configs
├── intermediate_designs/                # Raw diffusion outputs
├── intermediate_designs_inverse_folded/ # After inverse folding
│   ├── aggregate_metrics_analyze.csv    # All metrics
│   ├── refold_cif/                      # Refolded structures
│   └── refold_design_cif/              # Design refolded structures
├── final_ranked_designs/
│   ├── all_designs_metrics.csv          # Full metrics table
│   ├── final_designs_metrics_30.csv     # Top 30 metrics
│   ├── final_30_designs/                # Top 30 CIF files
│   ├── intermediate_ranked_10_designs/  # Top 10 CIF files
│   └── results_overview.pdf             # Summary plots
└── steps.yaml                           # Pipeline step manifest
```

## Key metrics from BoltzGen output

| Metric | Good | Description |
|--------|------|-------------|
| `filter_rmsd` | < 2.5 Å | Self-consistency (design refolds correctly) |
| `designfolding-filter_rmsd` | < 2.5 Å | Design-folding RMSD |
| `ALA_fraction` | < 0.3 | Not dominated by alanines |

## Next steps after BoltzGen

1. **Filter**: Use `/protein-qc` skill to apply quality thresholds
2. **Cross-validate**: Run top designs through Boltz-2 (`/boltz`) for independent structure prediction
3. **Score**: Use `/pgdh_ipsae` skill to rank by binding confidence
4. **Select**: Pick top 10 designs for submission

## Files

- Lyceum scripts: `projects/biolyceum/src/lyceum_boltzgen.py`, `run_boltzgen.sh`
- Client: `projects/biolyceum/src/utils/client.py` (`run_boltzgen()` method)
- Target structure: `pgdh_campaign/structures/2GDZ.cif`
- Strategy configs: `pgdh_campaign/configs/strategy[1-3]_*.yaml`
- Campaign plan: `pgdh_campaign/CAMPAIGN_PLAN.md`
- Docker image: `pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime`
- Machine: `gpu.a100`
