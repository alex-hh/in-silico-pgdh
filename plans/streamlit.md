# PGDH Design Platform — Full Plan

## Architecture Overview

Two ways to generate designs, one unified evaluation pipeline, one source of truth.

```
┌─────────────────────────────────┐   ┌──────────────────────────────────┐
│  Streamlit Web App (team)       │   │  Claude Code + Skills (devs)     │
│                                 │   │                                  │
│  Fixed pipelines:               │   │  Flexible orchestration:         │
│  - BoltzGen (3 strategies)      │   │  - /boltzgen-pgdh               │
│  - RFdiffusion3 (2 strategies)  │   │  - /pgdh_rfdiffusion3           │
│  - Future tools via forms       │   │  - /pgdh-design (orchestrator)  │
│                                 │   │  - Any new design model skill    │
└───────────┬─────────────────────┘   └───────────┬────────────────────────┘
            │                                     │
            │  Both write to tool-specific        │
            │  output/ dirs on S3                 │
            ▼                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Unified Evaluation Pipeline (evaluate_designs.py)                      │
│                                                                         │
│  1. Standardise: scan output/*/  →  designs/<tool>/<id>/metrics.json    │
│  2. Validate: Boltz-2 cross-validation  →  designs/<id>/validation.json │
│  3. Score: ipSAE + pDockQ + LIS  →  designs/<id>/scoring.json          │
│  4. Rank: composite score  →  designs/index.json (sorted)               │
│  5. Sync: update tracker/state.json                                     │
│                                                                         │
│  Triggered by: app button, CLI, or Claude Code after any design run     │
└──────────────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Lyceum S3 — Source of Truth                                            │
│                                                                         │
│  designs/index.json          ← master ranked list                       │
│  designs/<tool>/<id>/        ← per-design: structure + metrics + scores │
│  tracker/state.json          ← campaign state (dashboard + Claude Code) │
└──────────────────────────────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  propose-new-designs skill                                              │
│                                                                         │
│  Reads designs/index.json + tracker/state.json                          │
│  Analyses: coverage gaps, metric distributions, strategy balance        │
│  Suggests: which tool, which strategy, parameter tweaks, how many       │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Unified Evaluation Pipeline

**Problem**: Metrics currently come from different tools in different formats.
BoltzGen gives ipTM/pTM/RMSD in CSV. RFD3 gives helix_fraction/RoG in JSON.
ipSAE gives ipsae/pDockQ in TXT. No single place to compare across tools.

**Solution**: Replace `standardise_outputs.py` with `evaluate_designs.py` that
does standardisation + validation + scoring + ranking in one pipeline.

### 1.1 Create `pgdh_campaign/evaluate_designs.py`

Replaces and extends the current `standardise_outputs.py`. Steps:

1. **Collect** — scan `output/boltzgen/`, `output/rfdiffusion3/`, any future
   `output/<tool>/` dirs on S3. Parse tool-native metrics.
2. **Standardise** — write `designs/<tool>/<id>/metrics.json` with unified schema.
   All metrics normalised to consistent names and types.
3. **Validate** (optional, flag-gated) — for designs with sequences, submit
   Boltz-2 predictions on Lyceum. Write `designs/<id>/validation.json`.
4. **Score** (optional, flag-gated) — run ipSAE on validated predictions.
   Write `designs/<id>/scoring.json`.
5. **Rank** — compute composite score across all available metrics. Write
   `designs/index.json` sorted by rank.
6. **Sync tracker** — update `tracker/state.json` with new/changed designs.

```bash
# Just standardise + rank (fast, no GPU)
python pgdh_campaign/evaluate_designs.py

# Full pipeline including validation + scoring (submits Lyceum jobs, slow)
python pgdh_campaign/evaluate_designs.py --validate --score
```

### 1.2 Unified metrics schema

Every design gets the same JSON shape regardless of source tool:

```json
{
  "design_id": "boltzgen_s3_rank1",
  "tool": "boltzgen",
  "strategy": "surface",
  "status": "designed|validated|scored|selected",

  "sequence": "MKTL...",
  "num_residues": 95,

  "design_metrics": {
    "iptm": 0.72,
    "ptm": 0.85,
    "filter_rmsd": 1.8,
    "min_pae": 2.1,
    "helix": 0.45,
    "sheet": 0.15,
    "loop": 0.40,
    "source": "boltzgen"
  },

  "validation": {
    "iptm": 0.68,
    "ptm": 0.82,
    "plddt": 85.2,
    "source": "boltz2",
    "validated_at": "2026-02-27T20:00:00Z"
  },

  "scoring": {
    "ipsae": 0.65,
    "pdockq": 0.52,
    "lis": 0.38,
    "source": "ipsae",
    "scored_at": "2026-02-27T21:00:00Z"
  },

  "composite_score": 0.71,
  "rank": 3
}
```

Fields like `validation` and `scoring` are null until those pipeline steps run.
The composite score formula uses whatever metrics are available.

### 1.3 Tool adapter pattern

Each design tool gets a small adapter function in `evaluate_designs.py`:

```python
TOOL_ADAPTERS = {
    "boltzgen": parse_boltzgen_outputs,
    "rfdiffusion3": parse_rfd3_outputs,
    # Future tools register here:
    # "bindcraft": parse_bindcraft_outputs,
}
```

When someone adds a new design tool, they add one adapter function and register
it in this dict. The rest of the pipeline (validate, score, rank) works
automatically.

---

## Phase 2: propose-new-designs Skill

### 2.1 Create `.claude/skills/propose-new-designs/SKILL.md`

A Claude Code skill that analyses the current campaign state and suggests next
steps. Not a Python script — it's instructions for Claude to follow.

When invoked, Claude should:

1. **Read** `designs/index.json` from S3 (or local cache)
2. **Analyse**:
   - How many designs per tool and strategy
   - Metric distributions (mean/min/max ipTM, pTM, etc.)
   - How many have been validated/scored vs just designed
   - Pass rates at each pipeline stage
   - Sequence diversity (rough clustering by length + identity)
3. **Identify gaps**:
   - Strategies with few/no designs
   - Tools not yet tried
   - Designs stuck at "designed" that need validation
   - High-potential designs that need scoring
4. **Recommend**:
   - Which tool + strategy to run next
   - How many designs to generate
   - Parameter adjustments (e.g. "increase num_designs for S2, only 2 passed QC")
   - Whether to run evaluation pipeline
   - Timeline estimate

### 2.2 Integration

The skill reads from the same S3 source of truth. It can be invoked by
a developer running Claude Code locally, or called from the Streamlit app's
"Suggest Next Steps" button (which would display pre-canned analysis using
the same logic but as Python, not as a Claude skill).

---

## Phase 3: Fix the Streamlit App

### 3.1 Update app.py — evaluation pipeline integration

Replace the separate "Sync BoltzGen" / "Sync RFD3" buttons with a single
"Run Evaluation Pipeline" button that calls `evaluate_designs.py` logic:

```
Dashboard page:
  [Run Evaluation Pipeline]  ← standardise + rank (fast)
  [Run Full Evaluation]      ← + validate + score (slow, submits jobs)
```

### 3.2 Update app.py — New Run page improvements

The forms should pre-fill sensible defaults from skill knowledge. Make it
clear these are fixed pipelines with known-good parameters:

- BoltzGen: strategy picker, num_designs, protocol (defaults from skill)
- RFD3: strategy picker, num_designs, contig (defaults from skill)
- Each form shows estimated run time and cost

### 3.3 Update app.py — "Suggest Next Steps" section

On the Dashboard page, add a section that runs the same analysis logic as
the `propose-new-designs` skill but as Python:

- Show coverage table: tool × strategy → count
- Flag gaps ("No RFD3 dimer designs yet")
- Show validation/scoring funnel
- Suggest next action as text

### 3.4 Auto-standardise on job completion

When the Jobs page detects a job has completed (via status poll), automatically
trigger the standardisation step so new designs appear without manual sync.

### 3.5 Get it running locally

1. Install deps: `pip install streamlit pandas plotly`
2. Create secrets: copy template, fill in Lyceum API key + team password
3. Add `.gitignore` entry for secrets
4. Fix import paths if needed
5. Run: `cd dashboard && streamlit run app.py`
6. Test: password → dashboard → evaluation → designs table → detail view

### 3.6 Add 3D structure viewer

Install `stmol` + `py3Dmol`, uncomment the viewer code in Design Detail.
Download CIF from S3, render with py3Dmol, color binder vs target chain.

### 3.7 Error handling + polish

- Wrap S3 calls in try/except
- Handle credential expiry
- Graceful empty states on all pages
- Metric color coding in designs table

---

## Phase 4: Documentation + Rules

### 4.1 Update CLAUDE.md

Add these rules:

```markdown
## Design Tool Integration Rules
- **Any new design model skill MUST register with the pgdh-design orchestrator.**
  This means: (1) writing raw outputs to `output/<tool>/` on S3, (2) adding a
  parser adapter in `pgdh_campaign/evaluate_designs.py`, and (3) updating the
  Streamlit app's New Run page to include a submission form for the new tool.
  Without all three, designs from the new tool will not appear in the dashboard
  or be evaluated by the unified pipeline.
- **Two ways to generate designs**: (1) Fixed pipelines via the Streamlit web app
  (for team members), (2) Claude Code skills for developers with a local repo clone.
  Both must write outputs to the same S3 directory structure.
- **Always run the evaluation pipeline** after generating designs to keep
  `designs/index.json` and `tracker/state.json` in sync.
```

### 4.2 Update pgdh-design skill

Add a "Adding a New Design Tool" section explaining the three integration points:
1. S3 output directory convention
2. Parser adapter in evaluate_designs.py
3. Streamlit form in app.py

### 4.3 Document in pgdh_campaign/README.md

- S3 directory structure diagram
- How to add a new tool
- How evaluation pipeline works
- How to access the dashboard

---

## Phase 5: Deploy

### 5.1 Push to GitHub
### 5.2 Configure Streamlit Community Cloud
### 5.3 Add secrets in Streamlit Cloud settings
### 5.4 Share URL with team

---

## Order of Work

| # | Task | Depends on |
|---|------|------------|
| 1 | Write `evaluate_designs.py` (unified pipeline) | — |
| 2 | Write `propose-new-designs` skill | — |
| 3 | Update `app.py` (eval button, suggest next steps, auto-sync) | 1 |
| 4 | Update CLAUDE.md + pgdh-design skill + README | 1, 2 |
| 5 | Local test: install deps, secrets, run app | 3 |
| 6 | Add 3D viewer + polish | 5 |
| 7 | Deploy to Streamlit Cloud | 6 |

Steps 1 and 2 can run in parallel. Step 4 can run in parallel with 3.
