---
name: pgdh-evaluate
description: >
  Sync and evaluate PGDH binder designs. Use this skill when:
  (1) Collecting and ranking designs from S3 (sync_designs.py),
  (2) Submitting Boltz-2 cross-validation jobs for designed binders,
  (3) Submitting ipSAE scoring jobs for binding confidence,
  (4) Submitting BoltzGen refolding for designability metrics,
  (5) Populating the designs/ source of truth on S3.

  For generating designs, use boltzgen-pgdh or pgdh_rfdiffusion3.
  For individual Boltz-2 runs, use pgdh_boltz2.
  For individual ipSAE scoring, use pgdh_ipsae.
  For the full campaign workflow, see pgdh_campaign/CAMPAIGN_PLAN.md.
license: MIT
category: evaluation
tags: [pgdh, evaluation, ranking, boltz2, ipsae, lyceum]
---

# PGDH Design Sync + Evaluation

Two scripts with clear roles:

- **`pgdh_campaign/sync_designs.py`** — Collect, standardise, rank (no GPU, fast). The ONLY writer to `designs/` on S3.
- **`pgdh_campaign/evaluate_designs.py`** — Submit GPU jobs for refolding, validation, scoring. Writes to `output/`.

## Quick Start

```bash
source .venv/bin/activate

# Collect + rank (no GPU, fast) — run after every design job
python pgdh_campaign/sync_designs.py

# Submit GPU evaluation jobs
python pgdh_campaign/evaluate_designs.py --refold     # BoltzGen refolding
python pgdh_campaign/evaluate_designs.py --validate   # Boltz-2 cross-validation
python pgdh_campaign/evaluate_designs.py --score      # ipSAE scoring

# After GPU jobs complete, sync again to pick up results
python pgdh_campaign/sync_designs.py
```

## What sync_designs.py Does

1. **Collect**: Scans `output/boltzgen/` and `output/rfdiffusion3/` on S3 via tool adapters
2. **Attach**: Picks up existing Boltz-2 results from `output/boltz2/`, refolding from `output/refolding/`, ipSAE from `output/ipsae/`
3. **Rank**: Computes composite scores and sorts
4. **Write**: Uploads `metrics.json` + structures to `designs/<tool>/<id>/`, writes `designs/index.json`, syncs `tracker/state.json`

## What evaluate_designs.py Flags Do

### `--refold`

Submits BoltzGen folding-mode jobs for designs that have sequences but no refolding result. Tests whether the designed sequence actually folds into the predicted structure (designability).

- Uses `--steps folding` in BoltzGen
- Key output metric: **RMSD** between designed and refolded structure
- Good: RMSD < 2.5 A

### `--validate`

Submits Boltz-2 cross-validation jobs for designs that have sequences but no validation. Predicts the binder+PGDH complex structure using an independent MSA-based predictor.

- Creates YAML pairing binder sequence with PGDH target (chain A)
- Runs on A100, ~3-5 min per complex
- Key output metrics: **ipTM** (interface confidence), **pTM**, **pLDDT**
- Good: ipTM > 0.5, strong: ipTM > 0.7

### `--score`

Submits ipSAE scoring for validated designs (those with Boltz-2 structures and PAE).

- Key output metrics: **ipSAE**, **pDockQ**, **LIS**
- Good: ipSAE > 0.61, strong: ipSAE > 0.70

## Typical Workflow

```
1. Generate designs        ->  /boltzgen-pgdh or /pgdh_rfdiffusion3
2. Collect + rank          ->  python sync_designs.py
3. Refold (designability)  ->  python evaluate_designs.py --refold
4. Wait for jobs...        ->  lyceum execution ls
5. Re-sync                 ->  python sync_designs.py
6. Validate (Boltz-2)      ->  python evaluate_designs.py --validate
7. Wait for jobs...        ->  lyceum execution ls
8. Re-sync + score         ->  python evaluate_designs.py --score
9. Wait for jobs...
10. Final sync             ->  python sync_designs.py
```

**API Stability (Feb 2026)**: Lyceum API has high latency. **Schedule at most 1 job
at a time** and wait for completion before submitting the next. The Python client
uses 120s API timeouts to handle this. For parallel work or if Lyceum is down,
use the standalone A100 server scripts in `server/` — see `server/README.md`.

## Modal Alternative

If Lyceum is down or unreliable, use the Modal-based pipeline in `pgdh_modal/`:

```bash
source .venv/bin/activate

# Collect + rank (no GPU, local filesystem)
python pgdh_modal/sync.py

# GPU evaluation via Modal
python pgdh_modal/evaluate.py --fast       # BoltzGen refolding
python pgdh_modal/evaluate.py --slow --auto # Boltz-2 cross-validation
python pgdh_modal/evaluate.py --score       # ipSAE scoring

# After evaluation, sync again
python pgdh_modal/sync.py

# Generate pages from Modal results
python pgdh_campaign/generate_pages.py --designs-dir pgdh_modal/out/designs/
```

All data is local in `pgdh_modal/out/` — no S3 dependency. See `pgdh_modal/README.md`.

After each `sync_designs.py` run, update GitHub Pages:
- **GitHub Pages**: `generate_pages.py` (syncs from S3 + generates HTML) + git push

## Composite Score Formula

```
composite = weighted_sum / weight_sum   (only includes available metrics)

Weights:
  design iptm:      0.25   (from BoltzGen, scaled 0-1)
  design ptm:       0.10   (from BoltzGen, scaled 0-1)
  design rmsd:      0.05   (inverted: 1 - rmsd/5, clamped to [0,1])
  refolding rmsd:   0.15   (inverted: 1 - rmsd/5, clamped to [0,1])
  validation iptm:  0.20   (from Boltz-2, scaled 0-1)
  validation plddt: 0.10   (from Boltz-2, scaled 0-100 -> 0-1)
  ipsae score:      0.25   (scaled 0-1)
```

Designs with more evaluation stages get more accurate composite scores.

## S3 Source of Truth

`sync_designs.py` writes to:

```
designs/                           # READ-ONLY source of truth
├── index.json                     # Master ranked index
└── <tool>/<design_id>/
    ├── metrics.json               # All metadata + metrics + scores
    ├── designed.cif               # Designer's predicted structure
    └── refolded.cif               # BoltzGen refolded structure (if available)

tracker/state.json                 # Campaign state (for dashboard + Claude Code)
```

`evaluate_designs.py` submits jobs that write to:

```
output/
├── boltz2/<design_id>/            # Boltz-2 validation predictions
├── refolding/<design_id>/         # BoltzGen refolding predictions
└── ipsae/                         # ipSAE scoring results
```

## Adding a New Tool Adapter

To integrate outputs from a new design tool:

1. Write `parse_<tool>_outputs(client, prefix)` in `sync_designs.py`
2. Register it in `TOOL_ADAPTERS`:
   ```python
   TOOL_ADAPTERS = {
       "boltzgen": {...},
       "rfdiffusion3": {...},
       "new_tool": {"fn": parse_new_tool_outputs, "prefix": "output/new_tool/"},
   }
   ```
3. Add a Streamlit form in `dashboard/app.py`

## Python API

```python
from pgdh_campaign.sync_designs import sync_all
from pgdh_campaign.evaluate_designs import run_evaluation
from projects.biolyceum.src.utils.client import LyceumClient

client = LyceumClient()

# Just sync (no GPU)
designs = sync_all(client=client)

# Sync + submit GPU jobs
designs = run_evaluation(client=client, validate=True, score=False)

for d in designs[:5]:
    print(f"#{d['rank']} {d['design_id']}: {d['composite_score']:.4f}")
```

## Files

- **Sync pipeline**: `pgdh_campaign/sync_designs.py` (ONLY writer to `designs/`)
- **GPU evaluation**: `pgdh_campaign/evaluate_designs.py` (submits jobs to `output/`)
- **Boltz-2 scripts**: `projects/biolyceum/src/lyceum_boltz2.py`, `run_boltz2.sh`
- **ipSAE script**: `projects/biolyceum/src/lyceum_ipsae.py`
- **Client**: `projects/biolyceum/src/utils/client.py`
- **Campaign plan**: `pgdh_campaign/CAMPAIGN_PLAN.md`
