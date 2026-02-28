# 15-PGDH Binder Design Campaign

Design protein binders targeting 15-PGDH (PDB: 2GDZ) for the Berlin Bio Hackathon x Adaptyv competition.

## TODO

- [ ] Regenerate `docs/index.html` with table view once Lyceum S3 is accessible (`python pgdh_campaign/generate_pages.py`)
- [ ] Test `evaluate_designs.py --fast` and `--slow` with actual Lyceum job submissions

## Lyceum API Stability (Feb 2026)

The Lyceum API is experiencing high latency (30-120s per API call). Mitigations:

- **Client timeouts**: Increased to 120s (API calls), 300s (status), 1200s (streaming) in `client.py`
- **Schedule 1 job at a time**: Submitting multiple concurrent jobs increases failure risk
- **Standalone fallback**: If Lyceum is fully down, use `server/` scripts on a standalone A100 VM (SCP-based workflow, no Lyceum dependency)

## Evaluation Pipeline

Two evaluation modes, both in `evaluate_designs.py`:

### Fast eval (`--fast`)
Designability check via BoltzGen refolding. Cheap, run for all designs.

- **BoltzGen designs**: Already have `filter_rmsd` from design-time self-consistency. Promoted into the `refolding` field automatically (no GPU needed).
- **RFD3 designs**: Submits a single batched BoltzGen folding job on Lyceum. Results land in `output/refolding/`.

### Slow eval (`--slow`)
Full Boltz-2 cross-validation. Expensive, manually triggered for promising designs.

```bash
# Specific designs
python pgdh_campaign/evaluate_designs.py --slow design_id_1 design_id_2

# Auto-select designs with good refolding (RMSD < 2.5A)
python pgdh_campaign/evaluate_designs.py --slow --auto
```

### Scoring (`--score`, `--interface`)
ipSAE binding confidence and PyRosetta interface metrics. Can combine with either mode.

### Metrics naming convention

All refolding metrics use the `boltzgen_` prefix to make the source explicit:

| Field | Meaning |
|-------|---------|
| `boltzgen_rmsd` | BoltzGen refolding RMSD (designability) |
| `boltzgen_plddt` | BoltzGen refolding pLDDT |
| `boltzgen_iptm` | BoltzGen refolding ipTM |

This applies to both BoltzGen self-consistency (promoted from `filter_rmsd`) and cross-tool refolding of RFD3 designs.

### Job batching

Both `--fast` and `--slow` batch all candidates into a **single Docker job** with a shell script that loops over designs. This avoids submitting hundreds of separate Lyceum jobs.

### Skipping already-evaluated designs

`evaluate_designs.py` calls `sync_all()` first, which preserves existing evaluation data via `_merge_designs`. Designs that already have refolding/validation results are skipped.

## GitHub Pages

The static site at `docs/index.html` has two views:
- **Cards view** (Evaluated / Unevaluated / All tabs): 3Dmol.js structure viewers for the top 30 designs by rank, with full metrics panels.
- **Table view**: Sortable, filterable table of all designs with all metrics columns (composite, ipTM, pTM, RMSD, BoltzGen metrics, validation, ipSAE, pDockQ, etc.).

Regenerate with:
```bash
python pgdh_campaign/generate_pages.py  # sync from S3 + generate docs/index.html
```

Use `--no-sync` to skip S3 download and use cached local data.

## Quick Start: Score a binder with ipSAE

Use the `/pgdh_ipsae` skill in Claude Code to score binder designs against the PGDH target.

### What ipSAE needs (and doesn't need)

ipSAE scores the **predicted confidence** of a protein-protein interaction. It does NOT
score a raw crystal structure — it needs outputs from a structure predictor (AF2, AF3, or Boltz)
that ran on your binder + PGDH complex together.

You cannot use `structures/2GDZ.pdb` directly — that's the raw PGDH crystal structure
(single chain, no binder, no PAE matrix). The workflow is:

```
1. Design a binder sequence
2. Predict the binder+PGDH complex with Boltz/AF2/AF3
   → produces a PAE file + predicted structure (2 chains)
3. Score that prediction with ipSAE
```

### 1. Get prediction files

Run a structure predictor on your binder sequence + PGDH sequence together.
The predictor outputs two files you need:

| File | What it is | Why ipSAE needs it |
|------|------------|-------------------|
| **PAE file** | Predicted Aligned Error matrix (NxN) | Core input — ipSAE scores inter-chain PAE confidence |
| **Structure file** | Predicted complex (target + binder) | Provides chain IDs, residue positions, CB distances |

The format depends on which predictor you used:

| Predictor | PAE file | Structure file |
|-----------|----------|----------------|
| AlphaFold2 | `scores_rank_001.json` | `unrelaxed_rank_001.pdb` |
| AlphaFold3 | `fold_full_data_0.json` | `fold_model_0.cif` |
| Boltz1 | `pae_model_0.npz` | `model_0.cif` |

### 2. Run ipSAE scoring

```bash
python projects/biolyceum/src/lyceum_ipsae.py \
    --pae-file <path_to_pae> \
    --structure-file <path_to_predicted_complex> \
    --pae-cutoff 10 --dist-cutoff 10 \
    --output-dir pgdh_campaign/out/ipsae/<candidate_name>
```

On Lyceum, upload both prediction files first, then run:

```bash
# Upload prediction outputs to Lyceum storage
lyceum storage upload boltz2_output/pae.json input/pae.json
lyceum storage upload boltz2_output/model_0.cif input/model.cif

# Run scoring (CPU, no GPU needed)
lyceum python run lyceum_ipsae.py -r requirements/ipsae.txt -m cpu \
    -- --pae-file /job/work/input/pae.json \
       --structure-file /job/work/input/model.cif \
       --pae-cutoff 10 --dist-cutoff 10
```

### 3. Read the results

The `.txt` file contains one row per chain pair. Look for the `max` row:

```
Chn1,Chn2,PAE,Dist,Type,ipSAE,...
A,B,10,10,max,0.723456,...
```

**Key metrics** (from the `max` row, comma-separated):

| Column | Field | Pass | Strong |
|--------|-------|------|--------|
| 6 | ipSAE | > 0.61 | > 0.70 |
| 11 | pDockQ | > 0.50 | > 0.60 |
| 12 | pDockQ2 | > 0.50 | > 0.60 |
| 13 | LIS | > 0.35 | > 0.45 |

### 4. Visualize in PyMOL

```
# Load your structure, then run the .pml script:
@pgdh_campaign/out/ipsae/candidate_1/complex_10_10.pml
color_A_B
```

This colors interface residues on each chain (magenta = target, marine = binder).

## Example run (synthetic test data)

Since real prediction files require running Boltz/AF2 first, a synthetic test is included
at `out/ipsae_test/` to verify the scoring pipeline works. It contains:

- `pgdh_binder_complex.pdb` — 2-chain PDB: PGDH chain A (from 2GDZ) + a fake 60-residue
  helical binder as chain B, simulating what a structure predictor would output
- `pgdh_binder_scores.json` — synthetic AF2-format PAE JSON with fabricated inter-chain
  PAE values (low PAE at target residues 135-155 / binder residues 26-41)

These are **not** real predictions — they just test that the scoring code runs correctly.

```bash
python projects/biolyceum/src/lyceum_ipsae.py \
    --pae-file pgdh_campaign/out/ipsae_test/pgdh_binder_scores.json \
    --structure-file pgdh_campaign/out/ipsae_test/pgdh_binder_complex.pdb \
    --pae-cutoff 10 --dist-cutoff 10 \
    --output-dir pgdh_campaign/out/ipsae_test/results
```

Output:
```
Wrote 3 output files:
  pgdh_campaign/out/ipsae_test/results/pgdh_binder_complex_10_10.txt
  pgdh_campaign/out/ipsae_test/results/pgdh_binder_complex_10_10_byres.txt
  pgdh_campaign/out/ipsae_test/results/pgdh_binder_complex_10_10.pml
```

To score real designs, run the full pipeline: design binder → predict complex with
Boltz-2/AF2 → then score with ipSAE (see Campaign workflow below).

## Batch scoring

```python
from pathlib import Path
from projects.biolyceum.src.lyceum_ipsae import compute_ipsae

for candidate_dir in sorted(Path("pgdh_campaign/out/boltz2").iterdir()):
    pae = next(candidate_dir.glob("*.json"), None)
    struct = next(candidate_dir.glob("*.cif"), None) or next(candidate_dir.glob("*.pdb"), None)
    if pae and struct:
        compute_ipsae(str(struct), str(pae), 10.0, 10.0, f"pgdh_campaign/out/ipsae/{candidate_dir.name}")
```

## BoltzGen Binder Design (Lyceum)

Generate PGDH binder candidates using BoltzGen on Lyceum. Use the `/boltzgen-pgdh` skill for guided execution.

### Strategies

3 pre-configured binding strategies in `configs/`:

| # | Config | Approach | Hotspots |
|---|--------|----------|----------|
| 1 | `strategy1_active_site.yaml` | Active site blocker | Ser138, Gln148, Tyr151, Lys155, Phe185, Tyr217 |
| 2 | `strategy2_dimer_interface.yaml` | Dimer disruptor | Ala146, Ala153, Phe161, Leu167, Ala168, Leu171, Met172, Tyr206 |
| 3 | `strategy3_surface.yaml` | Model-free surface | None (auto-detect) |

### Running on Lyceum

```bash
source .venv/bin/activate

# 1. Upload target structure + YAML config
#    Note: YAML paths must use flat filenames (2GDZ.cif not ../structures/2GDZ.cif)
#    because run_boltzgen.sh copies all input/boltzgen/* to the same working dir
lyceum storage load pgdh_campaign/structures/2GDZ.cif --key input/boltzgen/2GDZ.cif
lyceum storage load /tmp/my_config.yaml --key input/boltzgen/config.yaml

# 2. Run BoltzGen (Docker mode, A100 GPU)
lyceum docker run pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime \
  -m gpu.a100 -t 600 \
  -c "bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh \
      --input-yaml /root/boltzgen_work/config.yaml \
      --output-dir /mnt/s3/output/boltzgen \
      --num-designs 3 \
      --cache /mnt/s3/models/boltzgen"

# 3. Download results
lyceum storage download output/boltzgen/final_ranked_designs/all_designs_metrics.csv \
  --output pgdh_campaign/out/boltzgen/all_designs_metrics.csv
lyceum storage download output/boltzgen/final_ranked_designs/results_overview.pdf \
  --output pgdh_campaign/out/boltzgen/results_overview.pdf
```

### Preparing YAML configs for Lyceum

The configs in `configs/` use relative paths (`../structures/2GDZ.cif`) for local use. For Lyceum, you need flat-path versions since `run_boltzgen.sh` copies all `input/boltzgen/*` into a single working directory:

```bash
# Create a Lyceum-compatible version (change path to flat filename)
sed 's|path: ../structures/2GDZ.cif|path: 2GDZ.cif|' \
  pgdh_campaign/configs/strategy3_surface.yaml > /tmp/pgdh_strategy3.yaml

# Upload it
lyceum storage load /tmp/pgdh_strategy3.yaml --key input/boltzgen/config.yaml
```

### Uploading scripts (one-time setup)

The BoltzGen Docker execution needs two scripts on Lyceum storage:

```bash
lyceum storage load projects/biolyceum/src/lyceum_boltzgen.py \
  --key scripts/boltzgen/lyceum_boltzgen.py
lyceum storage load projects/biolyceum/src/run_boltzgen.sh \
  --key scripts/boltzgen/run_boltzgen.sh
```

These persist in storage and don't need to be re-uploaded unless you change them.

### Lyceum storage layout

BoltzGen on Lyceum uses Docker execution mode. Storage is backed by **S3** and mounted at `/mnt/s3/` inside containers:

| Storage Path | Purpose | Persists? |
|-------------|---------|-----------|
| `/mnt/s3/input/boltzgen/` | Input YAML + structure files | Unreliable |
| `/mnt/s3/output/boltzgen/` | Pipeline outputs (designs, metrics, PDFs) | **Unreliable** |
| `/mnt/s3/models/boltzgen/` | Cached model weights (~5 GB) | Usually yes |
| `/mnt/s3/scripts/boltzgen/` | Lyceum scripts | Usually yes |
| `/mnt/s3/pip_cache/boltzgen/` | Cached pip wheels (speeds up installs) | Usually yes |
| `/mnt/s3/boltzgen_repo/` | Cached git repo (skips clone) | Usually yes |

### Storage persistence warning

**Lyceum storage is NOT guaranteed to persist.** Files can disappear between sessions.

- The storage API uses **temporary S3 credentials** obtained from `POST /api/v2/external/storage/credentials`. These credentials (and file visibility) can expire.
- We lost S1 and S2 BoltzGen CIF files — they showed up in `lyceum storage ls` but returned 404 on download minutes later.
- Model weights and scripts *seem* more stable, but don't count on it.

**Rules:**
1. **Always download results immediately** after a job completes
2. Download **both** metrics CSVs **and** CIF structure files (CSVs have sequences but not 3D coordinates)
3. **Never run `lyceum storage rmdir`** without downloading everything first
4. Use strategy-specific output subdirs (e.g. `output/boltzgen/s1_active_site/`) so runs don't overwrite each other

### Browsing storage directly

```bash
# Via CLI
lyceum storage ls output/boltzgen/
lyceum storage ls output/boltzgen/s1_active_site/final_ranked_designs/

# Via Python (boto3 S3 client)
python3 -c "
from projects.biolyceum.src.utils.client import LyceumClient
c = LyceumClient()
c._ensure_s3()
print(f'Bucket: {c._s3_bucket}')
print(f'Endpoint: {c._s3_client.meta.endpoint_url}')
for f in c.list_files('output/boltzgen/'):
    print(f)
"
```

The Python client gives you raw boto3 access. You can also use `aws s3 ls` if you extract the credentials.

### Downloading results

**Download immediately after each job completes:**

```bash
# List what's available
lyceum storage ls output/boltzgen/
lyceum storage ls output/boltzgen/final_ranked_designs/

# Download metrics CSV
lyceum storage download output/boltzgen/final_ranked_designs/all_designs_metrics.csv \
  --output pgdh_campaign/out/boltzgen/all_designs_metrics.csv

# Download aggregate metrics (more detailed)
lyceum storage download output/boltzgen/intermediate_designs_inverse_folded/aggregate_metrics_analyze.csv \
  --output pgdh_campaign/out/boltzgen/aggregate_metrics.csv

# Download summary PDF
lyceum storage download output/boltzgen/final_ranked_designs/results_overview.pdf \
  --output pgdh_campaign/out/boltzgen/results_overview.pdf

# Download top design CIF files
lyceum storage ls output/boltzgen/final_ranked_designs/final_30_designs/
# Then for each file:
lyceum storage download output/boltzgen/final_ranked_designs/final_30_designs/<filename>.cif \
  --output pgdh_campaign/out/boltzgen/designs/<filename>.cif
```

### Auth troubleshooting

If you get `Token verification timeout`, re-authenticate:

```bash
lyceum auth login
```

Auth tokens are stored at `~/.lyceum/config.json` and expire periodically.

### Timing (PGDH target, A100)

Measured from actual runs against 2GDZ (266 residues):

| Phase | Per Design | 3 Designs |
|-------|-----------|-----------|
| Setup (apt + pip, cached) | — | ~120s |
| Design (diffusion) | ~55s | ~170s |
| Inverse folding | ~3s | ~9s |
| Folding validation | ~26s | ~77s |
| Design folding | ~11s | ~33s |
| Analysis + filtering | ~30s | ~90s |
| **Total** | — | **~500s** |

| Designs | Fits in 600s timeout? |
|---------|-----------------------|
| 1-3 | Yes |
| 5+ | No — split into multiple runs |
| 50 | ~17 runs of 3 designs each |

### Via Python client

```python
from projects.biolyceum.src.utils.client import LyceumClient

client = LyceumClient()
success, files = client.run_boltzgen(
    yaml_path="/tmp/pgdh_surface.yaml",
    structure_files=["pgdh_campaign/structures/2GDZ.cif"],
    output_dir="pgdh_campaign/out/boltzgen",
    num_designs=3,
)
```

### Visualization

An interactive Jupyter notebook is provided at `visualize_designs.ipynb` for exploring results:
- Metrics overview and filtering
- RMSD distribution plots
- Amino acid composition
- 3D structure visualization (py3Dmol)
- Binder sequence extraction and FASTA export

## RFdiffusion3 Output Format

RFD3 does **joint backbone + sequence design** — unlike RFdiffusion v1, no ProteinMPNN step is needed. The designed sequences are embedded in the output CIF files and extracted automatically by `sync_designs.py`.

### Output types

RFD3 produces two files per design:
- **JSON** (`<name>.json`) — metadata, metrics, specification, `diffused_index_map`
- **CIF** (`<name>.cif` or `.cif.gz`) — full atomic structure with designed sequences

### Chain mapping (how to find the binder sequence)

There are two RFD3 output formats depending on the design mode:

#### 1. De novo binder design (multi-chain)

Used for: `pgdh_active_site`, `pgdh_dimer_interface`, etc.

| Chain | Content | Typical size |
|-------|---------|-------------|
| **A** | Designed binder | 60-100 residues |
| **B** | PGDH target (fixed) | 266 residues |

The `diffused_index_map` maps input target residues (keys like `A0`-`A265`) to output chain B positions (values like `B0`-`B265`). The binder is whichever chain is NOT the target — always chain A in observed outputs.

#### 2. Inpainting / segment replacement (single-chain)

Used for: `helix_hairpin_inpaint` with `segment_replace` contig

| Chain | Content | Typical size |
|-------|---------|-------------|
| **A** | Complete binder (fixed + diffused residues) | 78-82 residues |

The `diffused_index_map` maps input binder residues to output positions **within the same chain** (both keys and values are `A*`). The contig specifies which residues are fixed vs. diffused, e.g. `A96-121,15-25,A141-177` means: keep residues 96-121 and 141-177, replace the segment between them with 15-25 new residues.

The extracted sequence includes the **full chain** (fixed + diffused residues together) — this is the complete binder sequence needed for refolding.

### Detection logic in evaluate_designs.py

`_detect_binder_chain()` determines the correct chain automatically:
- If input chain letters == output chain letters → single-chain inpainting → binder is that chain
- If they differ → multi-chain design → binder is the chain NOT in the output values
- Fallback: chain A

## Campaign workflow

See [CAMPAIGN_PLAN.md](CAMPAIGN_PLAN.md) for the full pipeline:

1. **BoltzGen** — generate 150 binder candidates (3 strategies x 50) ← `/boltzgen-pgdh`
2. **Sync** — collect + rank designs: `python pgdh_campaign/sync_designs.py`
3. **Fast eval** — designability check: `python pgdh_campaign/evaluate_designs.py --fast`
4. **Sync again** — pick up refolding results: `python pgdh_campaign/sync_designs.py`
5. **Slow eval** — Boltz-2 cross-validation for promising designs: `python pgdh_campaign/evaluate_designs.py --slow --auto`
6. **Sync again** — pick up validation results: `python pgdh_campaign/sync_designs.py`
7. **Submit** — top 10 designs

Two commands to remember:
- `python pgdh_campaign/sync_designs.py` — Collect + rank (no GPU, fast). ONLY writer to `designs/`.
- `python pgdh_campaign/evaluate_designs.py` — Submit GPU jobs (--fast/--slow/--score). Writes to `output/`.

## Files

| Path | Description |
|------|-------------|
| `structures/2GDZ.cif` | PGDH target structure (CIF format, preferred by BoltzGen) |
| `structures/2GDZ.pdb` | PGDH target structure (PDB format) |
| `configs/strategy[1-3]_*.yaml` | BoltzGen design configs (3 binding strategies) |
| `out/boltzgen/` | BoltzGen design outputs |
| `out/ipsae_test/` | Synthetic test data + results |
| `visualize_designs.ipynb` | Interactive notebook for exploring designs |
| `CAMPAIGN_PLAN.md` | Full campaign strategy |
