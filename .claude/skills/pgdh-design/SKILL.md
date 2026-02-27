---
name: pgdh-design
description: >
  End-to-end PGDH binder design orchestrator. Use this skill when:
  (1) Running a complete design-validate-score pipeline for 15-PGDH,
  (2) Ensuring all tool outputs land in the standardised source-of-truth directory on S3,
  (3) Coordinating BoltzGen, RFdiffusion3, Boltz-2 validation, and ipSAE scoring in sequence,
  (4) Syncing design results into the campaign tracker (tracker/state.json).

  This skill invokes boltzgen-pgdh, pgdh_rfdiffusion3, pgdh_ipsae, and lyceum_ipsae as needed.
  For individual tool details, use those skills directly.
  For the campaign plan, see pgdh_campaign/CAMPAIGN_PLAN.md.
license: MIT
category: orchestration
tags: [pgdh, orchestration, binder-design, lyceum, campaign]
---

# PGDH Binder Design Orchestrator

Orchestrate the full 15-PGDH binder design pipeline on Lyceum, ensuring all outputs are
standardised into a single source-of-truth directory structure on S3.

## Target

- **Protein**: 15-PGDH (15-hydroxyprostaglandin dehydrogenase)
- **PDB**: 2GDZ (1.65 Å, homodimer, NAD+ bound)
- **Chain A**: 266 residues
- **Active site**: Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217
- **Dimer interface**: Phe161, Val150, Ala153, Ala146, Leu167, Ala168

## S3 Directory Structure — Source of Truth

Design tools can write their raw outputs in any format to any location.
The evaluation pipeline (`evaluate_designs.py`) is the ONLY thing that
writes to the `designs/` source of truth.

```
Lyceum S3 Storage
├── output/                         # Raw tool outputs (any format, any structure)
│   ├── boltzgen/s1_active_site/    #   Design tools write here freely
│   ├── rfdiffusion3/active_site/
│   ├── boltz2/                     #   Refolding/validation predictions
│   └── ipsae/                      #   Scoring results
│
├── designs/                        # ← SOURCE OF TRUTH (read-only)
│   │                               #   ONLY evaluate_designs.py writes here
│   ├── index.json                  # Master ranked index
│   └── <tool>/<design_id>/         # Per-design directory:
│       ├── metrics.json            #   All metadata + metrics + scores
│       ├── designed.cif            #   Designer's predicted structure (copied from output/)
│       └── refolded.cif            #   Boltz-2 refolded structure (if available)
│
├── tracker/
│   └── state.json                  # Campaign state (dashboard + Claude Code)
│
├── input/                          # Tool input files (uploaded before runs)
└── scripts/                        # Uploaded run scripts
```

## Pipeline Steps

### Step 1: Generate designs (BoltzGen and/or RFD3)

Use the individual skills for generation:

```bash
# BoltzGen — invoke boltzgen-pgdh skill
# Writes to: output/boltzgen/s{1,2,3}_*/

# RFdiffusion3 — invoke pgdh_rfdiffusion3 skill
# Writes to: output/rfdiffusion3/{active_site,dimer_interface}/
```

**CRITICAL**: Always use strategy-specific output subdirs and informative submission names.

### Step 2: Standardise outputs

After any design run completes and results are on S3, run the standardisation script:

```bash
source .venv/bin/activate
python pgdh_campaign/standardise_outputs.py
```

This script:
1. Scans `output/boltzgen/` and `output/rfdiffusion3/` on S3
2. Parses metrics from CSVs (BoltzGen) and JSONs (RFD3)
3. Copies structures and creates standardised `metrics.json` per design
4. Writes `designs/index.json` — the master design index
5. Syncs `tracker/state.json` with new designs

### Step 3: Validate top designs with Boltz-2

Select promising designs from the tracker, then validate:

```bash
# Use pgdh_ipsae or lyceum_ipsae skill for scoring
# Or submit Boltz-2 validation via the dashboard
```

### Step 4: Score with ipSAE

```bash
# Invoke pgdh_ipsae skill
# Results written to output/ipsae/, then standardised into designs/<id>/validation.json
```

### Step 5: Re-standardise after validation/scoring

```bash
python pgdh_campaign/standardise_outputs.py
# This picks up new validation.json and scoring data
```

## Database Schema

The evaluation pipeline (`evaluate_designs.py`) writes three types of files to S3.
All live under `designs/` and are **read-only** — no other code writes here.

### Per-design directory: `designs/<tool>/<design_id>/`

Each design gets a directory containing up to 3 files:

| File | Description | Always present? |
|------|-------------|-----------------|
| `metrics.json` | All metadata, metrics, and scores for this design | Yes |
| `designed.cif` | The structure predicted by the design tool (copied from `output/`) | If the tool produced one |
| `refolded.cif` | Boltz-2 refolded structure (for designability RMSD) | Only after `--refold` |

### `metrics.json` — Full schema

```json
{
  "design_id": "boltzgen_s1_rank3",
  "tool": "boltzgen",
  "strategy": "active_site",
  "status": "designed | validated | scored | selected | failed",
  "created_at": "2026-02-27T18:00:00Z",
  "sequence": "MKTL...",
  "num_residues": 95,
  "rank": 3,
  "composite_score": 0.6821,

  "source_files": {
    "structure": "output/boltzgen/s1_active_site/final_ranked_designs/rank3.cif",
    "metrics_csv": "output/boltzgen/s1_active_site/all_designs_metrics.csv",
    "refolded_structure": "output/boltz2/boltzgen_s1_rank3/refolded.cif"
  },

  "design_metrics": {
    "source": "boltzgen",
    "iptm": 0.72,
    "ptm": 0.85,
    "filter_rmsd": 1.8,
    "min_pae": 2.1,
    "helix": 0.45,
    "sheet": 0.15,
    "loop": 0.40,
    "plip_hbonds": 4,
    "delta_sasa": 1200.5,
    "quality_score": 0.82,
    "filters_passed": 5
  },

  "refolding": {
    "source": "boltz2",
    "refolded_at": "2026-02-27T19:30:00Z",
    "rmsd": 1.2,
    "plddt": 88.5,
    "ptm": 0.91
  },

  "validation": {
    "source": "boltz2",
    "validated_at": "2026-02-27T20:00:00Z",
    "iptm": 0.68,
    "ptm": 0.82,
    "plddt": 85.2
  },

  "scoring": {
    "source": "ipsae",
    "scored_at": "2026-02-27T21:00:00Z",
    "ipsae": 0.65,
    "pdockq": 0.52,
    "pdockq2": 0.48,
    "lis": 0.38
  }
}
```

#### Field descriptions

| Field | Type | Description |
|-------|------|-------------|
| `design_id` | string | Unique ID: `{tool}_{strategy_short}_{identifier}` |
| `tool` | string | Design tool: `boltzgen`, `rfdiffusion3`, `custom`, etc. |
| `strategy` | string | `active_site`, `dimer_interface`, `surface`, `unknown` |
| `status` | string | Pipeline stage: `designed` → `validated` → `scored` → `selected` (or `failed`) |
| `sequence` | string | Binder amino acid sequence (empty for backbone-only tools like RFD3) |
| `num_residues` | int | Binder length in amino acids |
| `rank` | int | Global rank by composite score (1 = best) |
| `composite_score` | float\|null | Weighted score from 0–1 (null if no metrics available) |
| `source_files` | object | S3 keys pointing to the raw output files this design came from |
| `design_metrics` | object | Tool-native metrics from the design step |
| `refolding` | object\|null | Boltz-2 monomer refolding results (designability check) |
| `validation` | object\|null | Boltz-2 complex prediction with target (binding check) |
| `scoring` | object\|null | ipSAE binding confidence scores |

#### `design_metrics` — varies by tool

**BoltzGen** provides: `iptm`, `ptm`, `filter_rmsd`, `min_pae`, `helix`, `sheet`, `loop`, `plip_hbonds`, `delta_sasa`, `quality_score`, `filters_passed`

**RFdiffusion3** provides: `helix`, `sheet`, `loop`, `radius_of_gyration`, `max_ca_deviation`, `n_chainbreaks`, `num_ss_elements`, `alanine_content`, `glycine_content`

**Custom uploads** have no design metrics (empty dict).

#### `refolding` vs `validation` — what's the difference?

- **Refolding** = Boltz-2 folds the binder sequence **alone** (monomer). Tests whether the designed sequence actually adopts the predicted structure. Key metric: **RMSD** between designed and refolded structure (lower = more designable).
- **Validation** = Boltz-2 predicts the binder **in complex with the target**. Tests whether the binding mode is plausible. Key metrics: **ipTM** (interface confidence) and **pLDDT** (per-residue confidence).

#### Composite score formula

```
composite = weighted_sum / weight_sum   (only includes available metrics)

Weights:
  design iptm:      0.25   (scaled 0–1)
  design ptm:       0.10   (scaled 0–1)
  design rmsd:      0.05   (inverted: 1 - rmsd/5, clamped to [0,1])
  refolding rmsd:   0.15   (inverted: 1 - rmsd/5, clamped to [0,1])
  validation iptm:  0.20   (scaled 0–1)
  validation plddt: 0.10   (scaled 0–100, normalised to 0–1)
  ipsae score:      0.25   (scaled 0–1)
```

### `designs/index.json` — Master ranked index

A summary of all designs for fast loading by the dashboard. Written by `evaluate_designs.py`.

```json
{
  "campaign": "pgdh_2gdz",
  "updated_at": "2026-02-27T22:00:00Z",
  "total_designs": 47,
  "by_tool": { "boltzgen": 30, "rfdiffusion3": 12, "custom": 5 },
  "by_strategy": { "active_site": 20, "dimer_interface": 15, "surface": 12 },
  "by_status": { "designed": 25, "validated": 12, "scored": 8, "selected": 2 },
  "designs": [
    {
      "design_id": "boltzgen_s1_rank3",
      "tool": "boltzgen",
      "strategy": "active_site",
      "status": "scored",
      "rank": 1,
      "composite_score": 0.6821,
      "num_residues": 95,
      "has_sequence": true,
      "has_refolding": true,
      "has_validation": true,
      "has_scoring": true,
      "iptm": 0.72,
      "ptm": 0.85,
      "filter_rmsd": 1.8,
      "refold_rmsd": 1.2,
      "ipsae": 0.65
    }
  ]
}
```

### `tracker/state.json` — Campaign state

Used by both the dashboard and Claude Code. Includes design summaries plus job history.

```json
{
  "campaign": "pgdh_2gdz",
  "updated_at": "2026-02-27T22:00:00Z",
  "designs": [
    {
      "id": "boltzgen_s1_rank3",
      "tool": "boltzgen",
      "strategy": "active_site",
      "status": "scored",
      "sequence": "MKTL...",
      "num_residues": 95,
      "metrics": { "iptm": 0.72, "ptm": 0.85 },
      "composite_score": 0.6821,
      "rank": 1,
      "notes": "Strong candidate — validate experimentally",
      "created_at": "2026-02-27T18:00:00Z"
    }
  ],
  "jobs": [
    {
      "id": "job_001",
      "tool": "boltzgen",
      "execution_id": "2e9e50ea-...",
      "status": "completed",
      "config": { "strategy": "active_site", "num_designs": 10 },
      "submitted_at": "2026-02-27T19:57:00Z",
      "completed_at": "2026-02-27T20:15:00Z"
    }
  ]
}
```

## Design ID Convention

Format: `{tool}_{strategy_short}_{identifier}`

Examples:
- `boltzgen_s1_rank3` — BoltzGen strategy 1, rank 3
- `boltzgen_s3_rank1` — BoltzGen strategy 3, rank 1
- `rfd3_active_site_model_0` — RFD3 active site, model 0
- `rfd3_dimer_model_2` — RFD3 dimer interface, model 2

## Key Rules

1. **Always download results immediately** after a Lyceum job completes — storage is not persistent.
2. **Always run `evaluate_designs.py`** after any new design run to keep `designs/` in sync.
3. **Use informative submission names** for Lyceum jobs.
4. **Never overwrite between strategies** — use strategy-specific output subdirs.
5. **The dashboard and Claude Code both read from `tracker/state.json`** — keep it in sync.
6. **NEVER delete design results** — flag as "failed" instead.
7. **The `designs/` directory on S3 is READ-ONLY** — only `evaluate_designs.py` writes to it. All other code (dashboard, Claude Code, skills) reads from it.

## Adding a New Design Tool

To integrate a new design tool (e.g. BindCraft), you must do three things:

### 1. S3 output directory

Write raw outputs to `output/<tool_name>/` on Lyceum S3. Use strategy-specific
subdirs if applicable (e.g. `output/bindcraft/active_site/`).

### 2. Parser adapter in evaluate_designs.py

Add a function in `pgdh_campaign/evaluate_designs.py`:

```python
def parse_bindcraft_outputs(client: LyceumClient, prefix: str = "output/bindcraft/") -> list[dict]:
    """Parse BindCraft outputs into standardised design dicts."""
    # Return list of dicts matching the standard schema:
    # design_id, tool, strategy, status, sequence, num_residues,
    # design_metrics, validation, scoring, composite_score, source_files
    ...
```

Register it in `TOOL_ADAPTERS`:

```python
TOOL_ADAPTERS = {
    "boltzgen": {...},
    "rfdiffusion3": {...},
    "bindcraft": {"fn": parse_bindcraft_outputs, "prefix": "output/bindcraft/"},
}
```

### 3. Streamlit form in app.py

Add a submission form in the "New Run" page of `dashboard/app.py` so team
members can launch the tool from the web app without CLI access.

**All three steps are required.** Without them, designs from the new tool
will not appear in the dashboard or be ranked by the evaluation pipeline.

## Two Ways to Generate Designs

1. **Streamlit web app** (`dashboard/app.py`) — fixed pipelines with pre-filled
   forms. For team members who want to launch jobs without CLI access.
2. **Claude Code skills** (`/boltzgen-pgdh`, `/pgdh_rfdiffusion3`, etc.) — flexible
   orchestration for developers with a local repo clone. More control over
   parameters and workflow.

Both paths write to the same `output/<tool>/` directories on S3. Both require
running `evaluate_designs.py` afterwards to populate the `designs/` source of truth.

## Files

- **Evaluation pipeline**: `pgdh_campaign/evaluate_designs.py` (THE ONLY writer to `designs/`)
- **Campaign tracker**: `dashboard/tracker.py` (Python class) / `tracker/state.json` (S3)
- **Dashboard app**: `dashboard/app.py`
- **Campaign plan**: `pgdh_campaign/CAMPAIGN_PLAN.md`
- **BoltzGen configs**: `pgdh_campaign/configs/strategy{1,2,3}_*.yaml`
- **RFD3 configs**: `pgdh_campaign/configs/rfd3_pgdh_binder.json`
- **Propose next steps**: `.claude/skills/propose-new-designs/SKILL.md`
