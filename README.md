# In Silico PGDH

Protein binder design campaign targeting 15-PGDH (PDB: 2GDZ) for the Berlin Bio Hackathon x Adaptyv competition.

**[View designs dashboard](https://alex-hh.github.io/in-silico-pgdh/)**

## Target

- **15-PGDH** (15-hydroxyprostaglandin dehydrogenase, UniProt: P15428)
- 1.65 A crystal structure, homodimer, NAD+ bound
- PDB: [2GDZ](https://www.rcsb.org/structure/2GDZ)

Three binding strategies:

| # | Strategy | Hotspots | Binder Size |
|---|----------|----------|-------------|
| 1 | Active site blocker | Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217 | 40-80 AA |
| 2 | Dimer disruptor | Phe161, Val150, Ala153, Leu167, Tyr206 | 80-140 AA |
| 3 | Surface (model-free) | Auto-detected | 60-140 AA |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Claude Code + Skills (/design-round orchestrates full rounds)         │
│                                                                         │
│  /boltzgen-pgdh  /pgdh_rfdiffusion3  /pgdh-evaluate  /design-round    │
└──────────┬──────────────────────────────────┬───────────────────────────┘
           │  submit Docker jobs              │  submit eval jobs
           ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Lyceum GPU/CPU                                                          │
│  ├── BoltzGen design (A100)        → output/boltzgen/r{N}/{strategy}/   │
│  ├── RFdiffusion3 design (A100)    → output/rfdiffusion3/r{N}/{strat}/  │
│  ├── Boltz-2 cross-validation      → output/boltz2/                     │
│  └── PyRosetta interface scoring   → output/pyrosetta/                  │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                           ▼  sync_designs.py (ONLY writer to designs/)
┌──────────────────────────────────────────────────────────────────────────┐
│  designs/                        ← SOURCE OF TRUTH (read-only)           │
│  ├── index.json                  Master ranked index (all rounds)        │
│  └── <tool>/<design_id>/         Per-design: designed.cif + metrics.json │
│                                                                          │
│  Metrics: ipTM, pTM, RMSD, min_interaction_pae, composite score         │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │
                           ▼  generate_pages.py
              ┌─────────────────────────────┐
              │  GitHub Pages (docs/)        │
              │  3D viewer, metrics table,   │
              │  round badges + filtering    │
              └─────────────────────────────┘
```

## Workflow

Designs are organised into **rounds** (r0, r1, r2...). Each round follows the
`/design-round` skill lifecycle: design → sync → eval → publish → advance.

### 1. Design Generation

Claude Code skills submit Docker jobs to Lyceum. Raw outputs go to
`output/{tool}/r{N}/{strategy}/` on S3:

```bash
/boltzgen-pgdh       # BoltzGen binder design
/pgdh_rfdiffusion3   # RFdiffusion3 binder design
/design-round        # Full round lifecycle (all phases)
```

### 2. Sync + Evaluate

**`sync_designs.py`** — Collect, standardise, rank (no GPU, fast):
```bash
source .venv/bin/activate
python pgdh_campaign/sync_designs.py          # normal sync
python pgdh_campaign/sync_designs.py --force  # force re-promote refolding data
```

This is the **ONLY writer to `designs/`** (the source of truth on S3). It:
1. **Collects** — scans `output/boltzgen/`, `output/rfdiffusion3/` on S3
2. **Attaches** — picks up Boltz-2, PyRosetta, and refolding results
3. **Ranks** — composite score from ipTM, pTM, RMSD, min_interaction_pae
4. **Writes** — updates `designs/index.json`, per-design `metrics.json`

**`evaluate_designs.py`** — Submit evaluation jobs:
```bash
python pgdh_campaign/evaluate_designs.py --fast --round 1         # promote BoltzGen self-consistency
python pgdh_campaign/evaluate_designs.py --fast --interface --round 1  # + PyRosetta scoring (CPU)
python pgdh_campaign/evaluate_designs.py --slow --auto --round 1  # Boltz-2 cross-validation (GPU)
python pgdh_campaign/evaluate_designs.py --fast --force            # force re-promote all
```

After eval jobs complete, run `sync_designs.py` again to pick up results.

### 3. Viewing Designs

The viewer at **https://alex-hh.github.io/in-silico-pgdh/** shows all designs
with 3D structures, metrics, and round filtering:

```bash
python pgdh_campaign/generate_pages.py    # sync from S3 + generate docs/index.html
git add docs/ && git commit && git push   # publish
```

- **Features**: 3Dmol.js 3D viewer, sortable metrics table, round badges/filter
- **Metrics**: ipTM, pTM, RMSD, min interaction PAE, composite score

## S3 Source of Truth

All design data flows through Lyceum S3 storage. The key directories:

| S3 Path | What | Writer | Readers |
|---------|------|--------|---------|
| `output/boltzgen/r{N}/{strat}/` | Raw BoltzGen outputs (CSVs, CIFs) | Lyceum GPU jobs | `sync_designs.py` |
| `output/rfdiffusion3/r{N}/{strat}/` | Raw RFD3 outputs (JSONs, CIFs) | Lyceum GPU jobs | `sync_designs.py` |
| `output/boltz2/` | Boltz-2 cross-validation | `evaluate_designs.py --slow` | `sync_designs.py` |
| `output/pyrosetta/` | Interface scoring (dG, SC, dSASA) | `evaluate_designs.py --interface` | `sync_designs.py` |
| **`designs/index.json`** | **Master ranked index (all rounds)** | **`sync_designs.py` only** | GitHub Pages |
| **`designs/<tool>/<id>/`** | **Per-design: metrics.json + CIFs** | **`sync_designs.py` only** | GitHub Pages |

The `designs/` directory is **read-only** — only `sync_designs.py` writes to it.
The `output/` directories contain raw tool outputs that get standardised into `designs/`.

## Project Structure

```
biohack/
├── pgdh_campaign/                 # Campaign files
│   ├── CAMPAIGN_PLAN.md           # Full campaign strategy
│   ├── sync_designs.py            # Collect + rank designs → writes designs/ on S3
│   ├── evaluate_designs.py        # Submit eval jobs (--fast/--slow/--interface/--round)
│   ├── generate_pages.py          # Sync from S3 + generate docs/index.html
│   ├── rounds/                    # Per-round summary docs (r0_summary.md, ...)
│   ├── configs/                   # Tool configs (YAML/JSON)
│   ├── structures/                # Target PDB/CIF files
│   └── out/                       # Local design outputs
│
├── docs/                          # GitHub Pages site
│   ├── index.html                 # Main viewer (Evaluated/Unevaluated/All tabs)
│   ├── data/                      # Cached from S3 designs/ by generate_pages.py
│   │   ├── index.json
│   │   ├── evaluated.json
│   │   ├── unevaluated.json
│   │   └── <tool>/<id>/designed.cif
│   └── historic/                  # Archived old viewers
│
├── projects/biolyceum/            # Lyceum platform integration
│   ├── src/
│   │   ├── utils/client.py        # LyceumClient (auth, S3, jobs)
│   │   ├── lyceum_boltzgen.py     # BoltzGen on Lyceum
│   │   ├── lyceum_rfdiffusion3.py # RFdiffusion3 on Lyceum
│   │   ├── lyceum_ipsae.py        # ipSAE scoring on Lyceum
│   │   └── lyceum_alphafast.py    # AlphaFast on Lyceum
│   └── README.md
│
├── .claude/skills/                # Claude Code skills
│   ├── boltzgen-pgdh/             # BoltzGen PGDH design
│   ├── pgdh_rfdiffusion3/         # RFdiffusion3 PGDH design
│   ├── design-round/              # Full round lifecycle orchestrator
│   ├── pgdh-evaluate/             # Evaluation pipeline skill
│   ├── pgdh-design/               # Design orchestration
│   └── ...                        # Generic tools (boltzgen, boltz, etc.)
│
├── plans/                         # Implementation plans
├── CLAUDE.md                      # Project rules for Claude Code
└── README.md                      # This file
```

## Development

### Setup

```bash
# Clone the repo
git clone <repo-url> && cd biohack

# Create and activate venv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r dashboard/requirements.txt
pip install lyceum-cli boto3 httpx

# Authenticate with Lyceum
lyceum auth login
```

### Adding a New Design Tool

Any new design tool must integrate with the pgdh-design system in **three places**:

#### 1. S3 output directory

Write raw outputs to `output/<tool_name>/` on Lyceum S3. Use strategy-specific
subdirs (e.g. `output/bindcraft/active_site/`).

#### 2. Parser adapter in sync_designs.py

Add a function in `pgdh_campaign/sync_designs.py`:

```python
def parse_mynewtool_outputs(client: LyceumClient, prefix: str = "output/mynewtool/") -> list[dict]:
    """Parse MyNewTool outputs into standardised design dicts."""
    # Must return list of dicts with these fields:
    # design_id, tool, strategy, status, sequence, num_residues,
    # design_metrics, validation, scoring, composite_score, source_files
    ...
```

Register it in `TOOL_ADAPTERS`:

```python
TOOL_ADAPTERS = {
    "boltzgen": {"fn": parse_boltzgen_outputs, "prefix": "output/boltzgen/"},
    "rfdiffusion3": {"fn": parse_rfd3_outputs, "prefix": "output/rfdiffusion3/"},
    "mynewtool": {"fn": parse_mynewtool_outputs, "prefix": "output/mynewtool/"},
}
```

#### 3. Claude Code skill (optional)

Create a skill in `.claude/skills/<tool-name>/SKILL.md` documenting
how to run the tool, its parameters, and output format.

**Without steps 1-2, designs from the new tool will not appear in the
pages viewer or be ranked by the evaluation pipeline.**

### Adding a New Evaluation Model

To add a new scoring/validation method (e.g. a new binding predictor):

1. Write outputs to `output/<scorer>/` on S3
2. Add an `attach_<scorer>_scores()` function in `sync_designs.py`
3. Update the composite score formula in `compute_composite_scores()` if the new metric should influence ranking

### Creating a Claude Code Skill

Skills live in `.claude/skills/<skill-name>/SKILL.md`. Follow the existing pattern:

```yaml
---
name: my-skill
description: >
  Short description. Use this skill when: (1) ..., (2) ...
license: MIT
category: design-tools
tags: [pgdh, my-tool, lyceum]
---

# Skill Title

Instructions for Claude to follow when this skill is invoked...
```

If the skill generates designs, it **must** mention that outputs go to
`output/<tool>/` on S3 and that `sync_designs.py` must be run afterwards.

## Lyceum Platform

[Lyceum](https://lyceum.technology) provides GPU compute with persistent S3 storage.

### Authentication

```bash
lyceum auth login          # Interactive login
lyceum auth status         # Check auth status
cat ~/.lyceum/config.json  # View stored credentials
```

### Machine Types

| Type | GPU | VRAM | Use case |
|------|-----|------|----------|
| `gpu.t4` | T4 | 16 GB | ESM2, light inference |
| `gpu.a100` | A100 | 80 GB | BoltzGen, RFD3, Boltz-2 |
| `gpu.l40s` | L40S | 48 GB | Medium workloads |
| `gpu.h100` | H100 | 80 GB | Large/fast runs |
| `cpu` | — | — | ipSAE scoring |

### Storage

Lyceum provides S3-backed storage mounted at `/mnt/s3/` in Docker jobs.

```bash
# Upload files
lyceum storage load local_file.cif --key input/boltzgen/file.cif

# List files
lyceum storage ls output/boltzgen/

# Download files
lyceum storage download output/boltzgen/results.csv --dest ./results.csv
```

**Critical**: Storage is **not guaranteed to persist** between sessions. Always
download results immediately after a job completes.

### Python Client

```python
from projects.biolyceum.src.utils.client import LyceumClient

client = LyceumClient()

# Upload/download
client.upload_file("local.cif", "input/boltzgen/local.cif")
client.download_file("output/results.csv", "./results.csv")

# Submit Docker job
exec_id, stream_url = client.submit_docker_job(
    docker_image="pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime",
    command="bash /mnt/s3/scripts/run.sh",
    execution_type="gpu.a100",
    timeout=600,
)

# Poll status
success, status = client.wait_for_completion(exec_id)
```

### Submission Rules

- **Always use informative names** via `-f` flag: `-f "pgdh_boltzgen_s1_active_site_10designs"`
- **Always use strategy-specific output subdirs** to prevent overwriting
- **Always download results immediately** after completion
- **Never delete output directories** without downloading first
- **Never delete design results** — flag as "failed" instead

## Key Rules

1. **Never delete design results** — from S3, local, or tracker. Flag as "failed" instead.
2. **`designs/` on S3 is read-only** — only `sync_designs.py` writes to it.
3. **Run `sync_designs.py` after every design run** to keep the source of truth in sync.
4. **New tools must register** in sync_designs.py, app.py, and have a skill.
5. **Download Lyceum results immediately** — storage persistence is not guaranteed.
