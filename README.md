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
| 1 | Active site blocker | Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217 | 80-120 AA |
| 2 | Dimer disruptor | Phe161, Val150, Ala153, Leu167, Tyr206 | 80-140 AA |
| 3 | Surface (model-free) | Auto-detected | 60-140 AA |

## Architecture

```
                    ┌─────────────────────┐    ┌──────────────────────────┐
                    │  Streamlit Web App   │    │  Claude Code + Skills    │
                    │  (team members)      │    │  (developers)            │
                    │                      │    │                          │
                    │  Fixed pipelines:    │    │  Flexible orchestration: │
                    │  BoltzGen, RFD3,     │    │  /boltzgen-pgdh          │
                    │  Boltz-2, ipSAE      │    │  /pgdh_rfdiffusion3      │
                    └──────────┬───────────┘    └───────────┬──────────────┘
                               │                            │
                               │  Both write to output/     │
                               ▼                            ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  Lyceum S3 Storage                                                          │
│                                                                             │
│  output/boltzgen/s1_active_site/     ← raw tool outputs                    │
│  output/rfdiffusion3/active_site/                                           │
│  output/boltz2/                                                             │
│  output/ipsae/                                                              │
└──────────────────────────┬───────────────────────────────────────────────────┘
                           │
                           ▼  evaluate_designs.py (ONLY writer)
┌──────────────────────────────────────────────────────────────────────────────┐
│  designs/                        ← SOURCE OF TRUTH (read-only)              │
│  ├── index.json                  Master ranked index                        │
│  └── <tool>/<design_id>/         Per-design: structure.cif + metrics.json   │
│                                                                             │
│  tracker/state.json              ← Campaign state (dashboard + Claude Code) │
└──────────────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Dashboard / Skills    │
              │  read designs/ and     │
              │  tracker/state.json    │
              └────────────────────────┘
```

## Workflow

### 1. Design Generation

Two equivalent paths — both write raw outputs to `output/<tool>/` on Lyceum S3:

**Via the Streamlit web app** (for team members):
- Open the dashboard, go to "New Run" page
- Pick a tool (BoltzGen, RFdiffusion3), strategy, and parameters
- Click submit — the app sends a Docker job to Lyceum

**Via Claude Code skills** (for developers with a local repo):
```bash
# Use skills interactively through Claude Code
/boltzgen-pgdh    # BoltzGen design generation
/pgdh_rfdiffusion3  # RFdiffusion3 design generation
/pgdh-design      # Full orchestration (design → evaluate → rank)
```

### 2. Unified Evaluation Pipeline

After any design run, the evaluation pipeline collects all tool outputs,
standardises metrics, ranks by composite score, and writes the source of truth:

```bash
source .venv/bin/activate

# Standardise + rank (fast, no GPU)
python pgdh_campaign/evaluate_designs.py

# Include Boltz-2 cross-validation + ipSAE scoring (submits Lyceum jobs)
python pgdh_campaign/evaluate_designs.py --validate --score
```

The pipeline:
1. **Collect** — scans `output/boltzgen/`, `output/rfdiffusion3/`, etc. on S3
2. **Standardise** — parses tool-native metrics into unified `metrics.json` per design
3. **Attach scores** — picks up existing Boltz-2 and ipSAE results
4. **Rank** — computes composite score (ipTM, pTM, RMSD, validation, ipSAE)
5. **Write** — updates `designs/index.json` and `tracker/state.json`

The `designs/` directory on S3 is the **read-only source of truth**. Only
`evaluate_designs.py` writes to it. All other code reads from it.

### 3. Web Dashboard

Streamlit app at `dashboard/app.py` for team collaboration:

- **Dashboard** — summary cards, pipeline funnel, coverage matrix, suggested next steps
- **Designs** — filterable table with composite scores, bulk status updates
- **Jobs** — track Lyceum job status, auto-evaluate on completion
- **New Run** — submit design/validation/scoring jobs with pre-filled forms
- **Design Detail** — per-design metrics, sequence, notes, 3D viewer

```bash
cd dashboard
streamlit run app.py
```

## Project Structure

```
biohack/
├── pgdh_campaign/                 # Campaign files
│   ├── CAMPAIGN_PLAN.md           # Full campaign strategy
│   ├── evaluate_designs.py        # Unified evaluation pipeline
│   ├── configs/                   # Tool configs (YAML/JSON)
│   ├── structures/                # Target PDB/CIF files
│   ├── out/                       # Local design outputs
│   └── generate_viewer.py         # Static HTML viewer
│
├── dashboard/                     # Streamlit web app
│   ├── app.py                     # Main app (5 pages)
│   ├── client.py                  # Lyceum API client
│   ├── tracker.py                 # S3-backed campaign state
│   ├── loaders.py                 # Metric parsing + thresholds
│   └── requirements.txt
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
├── resources/biomodals/           # Modal reference scripts (legacy)
│
├── .claude/skills/                # 30+ Claude Code skills
│   ├── boltzgen-pgdh/             # BoltzGen PGDH design
│   ├── pgdh_rfdiffusion3/         # RFdiffusion3 PGDH design
│   ├── pgdh-design/               # Orchestrator skill
│   ├── propose-new-designs/       # Campaign analysis + suggestions
│   ├── pgdh_ipsae/                # ipSAE scoring for PGDH
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

#### 2. Parser adapter in evaluate_designs.py

Add a function in `pgdh_campaign/evaluate_designs.py`:

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

#### 3. Streamlit form in app.py

Add a submission form in the "New Run" page of `dashboard/app.py` so team
members can launch jobs from the web app.

**Without all three steps, designs from the new tool will not appear in the
dashboard or be ranked by the evaluation pipeline.**

### Adding a New Evaluation Model

To add a new scoring/validation method (e.g. a new binding predictor):

1. Write outputs to `output/<scorer>/` on S3
2. Add an `attach_<scorer>_scores()` function in `evaluate_designs.py`
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
`output/<tool>/` on S3 and that `evaluate_designs.py` must be run afterwards.

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
2. **`designs/` on S3 is read-only** — only `evaluate_designs.py` writes to it.
3. **Run evaluation after every design run** to keep the source of truth in sync.
4. **New tools must register** in evaluate_designs.py, app.py, and have a skill.
5. **Download Lyceum results immediately** — storage persistence is not guaranteed.
