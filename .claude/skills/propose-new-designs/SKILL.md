---
name: propose-new-designs
description: >
  Analyse the current PGDH campaign state and suggest next design runs.
  Use this skill when: (1) Deciding what to run next in the campaign,
  (2) Checking if there are coverage gaps across tools/strategies,
  (3) Assessing pass rates and identifying bottlenecks,
  (4) Planning how to spend remaining compute budget.

  Reads from designs/index.json and tracker/state.json on Lyceum S3.
  For running designs, use boltzgen-pgdh or pgdh_rfdiffusion3.
  For evaluation, use pgdh-design.
license: MIT
category: orchestration
tags: [pgdh, campaign, planning, analysis]
---

# Propose New Designs

Analyse the current campaign and recommend what to run next.

## How to analyse

### Step 1: Load current state

```bash
source .venv/bin/activate
python -c "
import json, sys
sys.path.insert(0, 'projects/biolyceum/src/utils')
from client import LyceumClient
c = LyceumClient()
print(c.download_bytes('designs/index.json').decode())
" > /tmp/pgdh_index.json
```

Or if `designs/index.json` doesn't exist yet, run the evaluation pipeline first:
```bash
python pgdh_campaign/evaluate_designs.py
```

### Step 2: Analyse coverage

Read `designs/index.json` and compute:

**Coverage matrix** (tool × strategy → count):

|              | active_site | dimer_interface | surface |
|--------------|-------------|-----------------|---------|
| boltzgen     | ?           | ?               | ?       |
| rfdiffusion3 | ?           | ?               | N/A     |
| bindcraft    | ?           | ?               | ?       |

**Pipeline funnel**:
- designed → validated → scored → selected
- What % pass each gate?
- Where is the biggest drop-off?

**Metric distributions** per tool:
- ipTM: mean, min, max, % above 0.5
- pTM: mean, min, max, % above 0.7
- RMSD (BoltzGen): mean, % below 2.5 Å
- ipSAE (if scored): mean, % above 0.61

**Diversity**:
- How many unique strategies represented?
- Binder length distribution
- Any sequence clustering (if sequences available)?

### Step 3: Identify gaps and issues

Look for:
1. **Empty cells** in the coverage matrix — strategies not yet tried with a tool
2. **Low counts** — strategies with < 5 designs (not enough to find winners)
3. **Low pass rates** — if < 20% pass QC, the strategy or parameters may need adjustment
4. **Bottlenecks** — many designs stuck at "designed" that need validation
5. **Missing tools** — BindCraft not tried yet? Consider adding it.
6. **Score distribution** — if all ipSAE scores cluster near 0.4, the binding mode may be weak

### Step 4: Make recommendations

Format recommendations as an actionable list:

```
## Recommendations

1. **[HIGH] Run RFD3 dimer_interface** — 0 designs for this combination.
   Suggested: 8 designs, step_scale=3, gamma_0=0.2
   Command: invoke /pgdh_rfdiffusion3 or use Streamlit "New Run" page

2. **[MED] Validate BoltzGen S3 designs** — 12 designs at "designed" status.
   Run: python pgdh_campaign/evaluate_designs.py --validate

3. **[MED] Increase BoltzGen S1 count** — only 3 passed QC out of 10.
   Suggested: 20 more designs with num_designs=20

4. **[LOW] Score validated designs** — 5 validated but not scored.
   Run: python pgdh_campaign/evaluate_designs.py --score
```

Priority levels:
- **HIGH**: Coverage gap or blocking issue — campaign can't progress without this
- **MED**: Would improve campaign quality or coverage
- **LOW**: Nice to have, can wait

### Step 5: Estimate impact

For each recommendation, estimate:
- **Time**: ~X minutes on A100
- **Expected yield**: ~Y designs passing QC (based on historical pass rate)
- **Impact on final selection**: "Would add diversity to dimer strategy" or "Likely to produce top-3 candidate"

## Decision heuristics

### When to generate more designs
- A strategy has < 10 designs total
- Pass rate > 30% but count is low (the strategy works, just need more)
- No designs from a tool/strategy combination (coverage gap)

### When to NOT generate more designs
- Pass rate < 10% with > 20 attempts (strategy is not working)
- Already have > 5 strong candidates (composite_score > 0.7) from this strategy
- Approaching compute budget limit

### When to switch tools
- BoltzGen ipTM consistently < 0.4 → try RFD3 for that strategy
- RFD3 backbone-only → need ProteinMPNN for sequences before validation
- All methods struggling → consider BindCraft hallucination approach

### When to adjust parameters
- High alanine content (> 30%) → reduce temperature or try different protocol
- Chain breaks in RFD3 → increase `--num-timesteps` or adjust contig length
- Poor interface shape (low delta_SASA) → adjust hotspot selection

## Target thresholds for "good" designs

| Metric | Pass | Strong | Source |
|--------|------|--------|--------|
| ipTM (design) | > 0.5 | > 0.7 | BoltzGen |
| pTM (design) | > 0.7 | > 0.8 | BoltzGen |
| RMSD | < 2.5 Å | < 2.0 Å | BoltzGen |
| ipTM (validation) | > 0.5 | > 0.7 | Boltz-2 |
| pLDDT (validation) | > 70 | > 85 | Boltz-2 |
| ipSAE | > 0.61 | > 0.70 | ipSAE |
| pDockQ | > 0.23 | > 0.50 | ipSAE |
| Composite | > 0.50 | > 0.70 | Pipeline |

## Files

- **Campaign index**: `designs/index.json` on S3
- **Campaign state**: `tracker/state.json` on S3
- **Evaluation pipeline**: `pgdh_campaign/evaluate_designs.py`
- **Campaign plan**: `pgdh_campaign/CAMPAIGN_PLAN.md`
