---
name: propose-designs
description: >
  Analyse previous round results and propose design strategies for the next round.
  Use this skill when: (1) A design round has completed and you want to plan the next,
  (2) You want to adjust hotspots, binder lengths, or diffusion parameters based on metrics,
  (3) You want to identify which strategies are working and which need changes.

  Reads from the local source of truth (pgdh_modal/out/designs/) or S3 (designs/).
  For executing designs, use design-round-modal (Modal) or design-round (Lyceum).
license: MIT
category: analysis
tags: [pgdh, strategy, analysis, design]
---

# /propose-designs — Analyse Results & Propose Next Round

Read metrics from the source of truth, analyse what's working, and propose
updated design strategies for the next round.

## Instructions

### Step 1: Load current results

Read the ranked design index and per-design metrics:

```python
import json
from pathlib import Path

# Local (Modal pipeline)
index = json.loads(Path("pgdh_modal/out/designs/index.json").read_text())

# Or from S3 cache (Lyceum pipeline)
index = json.loads(Path("docs/data/index.json").read_text())
```

Also read round summaries from `pgdh_campaign/rounds/r*_summary.md` to understand
what was tried and what was observed.

### Step 2: Analyse by strategy

For each strategy (active_site, dimer_interface, surface, helix_hairpin_*), compute:

- **Pass rate**: % of designs with filter_rmsd < 2.5 A
- **Best composite score** and what made it good
- **ipTM distribution**: median, best, worst
- **Refolding RMSD distribution**: how many designs fold back well
- **Cross-validation survival**: % that maintain ipTM > 0.5 in Boltz-2
- **ipSAE/pDockQ scores** if available

### Step 3: Analyse by tool

Compare BoltzGen vs RFdiffusion3:

- Which tool produces better self-consistency (filter_rmsd)?
- Which survives cross-validation better?
- Are there systematic differences in binder length, secondary structure?

### Step 4: Propose adjustments

Based on the analysis, propose specific changes for the next round:

#### Hotspot adjustments
- Which hotspot residues appear in the best designs' interfaces?
- Should we add/remove hotspots? Try different atoms?
- Example: "Add A148:OE1 to active site strategy — top designs show Gln148 contacts"

#### Binder length
- Are shorter or longer binders performing better?
- Current ranges: active_site 80-120, dimer 80-140, surface 60-140
- Propose narrower ranges based on what's working
- Example: "Narrow active_site to 90-110 — 85% of good designs are in this range"

#### BoltzGen parameters
- Should we increase `--num-designs` for successful strategies?
- Any strategy getting zero good designs? Consider dropping or changing it.

#### RFD3 parameters
- Adjust `step_scale` (higher = more exploration, lower = more conservative)
- Adjust `gamma_0` (lower = more diverse, higher = more focused)
- Adjust `num_timesteps` (200 standard, 500 high quality)
- Example: "Reduce gamma_0 from 0.6 to 0.3 for dimer strategy — designs too similar"

#### Inpainting parameters
- Adjust partial diffusion `t` values (higher = more noise = more redesign)
- Try different segment lengths for segment replacement
- Example: "Try t=15 for helix_hairpin — t=10 designs are too conservative"

#### New strategies
- Propose entirely new configs if existing ones plateau
- Allosteric sites, cofactor-adjacent binding, epitope-targeted
- New inpainting targets based on structural analysis

### Step 5: Write proposal

Output a concrete proposal with:

1. **Round N+1 plan** — which strategies to run, how many designs each.
   **Limit: at most 3 design jobs per round.** Pick the most promising strategies.
2. **Config changes** — specific YAML/JSON edits with rationale
3. **New configs** — any new strategy files to create
4. **Expected improvement** — what metrics should improve and why

Write the proposal to `pgdh_campaign/rounds/r{N+1}_plan.md`.

## Metrics Reference

| Metric | Good | Strong | Source |
|--------|------|--------|--------|
| filter_rmsd | < 2.5 A | < 2.0 A | BoltzGen self-consistency |
| Refolding RMSD | < 2.5 A | < 1.5 A | BoltzGen folding mode |
| ipTM (design) | > 0.5 | > 0.7 | Design metrics |
| ipTM (Boltz-2) | > 0.5 | > 0.7 | Cross-validation |
| pLDDT (Boltz-2) | > 70 | > 85 | Cross-validation |
| min_interaction_pae | < 5.0 | < 3.0 | PAE interface metric |
| pDockQ | > 0.23 | > 0.50 | Docking quality |

## Config Locations

- BoltzGen YAMLs: `pgdh_campaign/configs/strategy*.yaml`
- RFD3 binder JSON: `pgdh_campaign/configs/rfd3_pgdh_binder.json`
- RFD3 inpainting JSON: `pgdh_campaign/configs/rfd3_helix_hairpin_inpaint.json`
- Target structures: `pgdh_campaign/structures/`
- Round summaries: `pgdh_campaign/rounds/r*_summary.md`
