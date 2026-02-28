---
name: design-round-modal
description: >
  Run a complete PGDH design round via Modal (not Lyceum). Use this skill when:
  (1) Running binder design + evaluation end-to-end using Modal GPUs,
  (2) Lyceum is down or unreliable,
  (3) Running BoltzGen or RFdiffusion3 design rounds with Modal.

  For the Lyceum version, use design-round.
  For evaluation only (no new designs), use pgdh-evaluate with Modal commands.
license: MIT
category: design
tags: [pgdh, modal, boltzgen, rfdiffusion3, design, evaluation]
---

# /design-round-modal — Complete Round Lifecycle (Modal)

Run a full design round end-to-end using Modal GPUs instead of Lyceum.

## Prerequisites

```bash
pip install modal
modal token set   # authenticate once
```

## Instructions

Read the current round number N from CLAUDE.md (`Current round: N`).

Execute these phases in order. Wait for each phase to complete before starting the next.

### Phase 0: PROPOSE (optional)

Before designing, analyse previous round results to refine strategies:

```
/propose-designs
```

This reads metrics from the source of truth, identifies what's working,
and proposes hotspot/length/parameter adjustments. Writes a plan to
`pgdh_campaign/rounds/r{N}_plan.md`. Skip this for the first round.

### Phase 1: DESIGN

Submit design jobs via Modal. Choose tools and strategies based on campaign plan
(or the proposal from Phase 0).

**Limit: at most 3 design jobs per round.** Each job costs GPU time and produces
designs that need evaluation. Focus on the most promising strategies rather than
running everything.

**BoltzGen** (binder design with self-consistency filtering):
```bash
source .venv/bin/activate

# Single strategy
python pgdh_modal/design.py boltzgen --strategy active_site --num-designs 10

# All 3 strategies
python pgdh_modal/design.py boltzgen --strategy all --num-designs 10

# Strategies: active_site, dimer_interface, surface
```

**RFdiffusion3** (atomic-level binder design with hotspots):
```bash
# Binder design — quick test
python pgdh_modal/design.py rfd3 --strategy active_site --num-designs 4 --step-scale 3 --gamma-0 0.2

# Binder design — production
python pgdh_modal/design.py rfd3 --strategy active_site --num-designs 8 --step-scale 1.5 --gamma-0 0.6

# All binder strategies (active_site + dimer_interface)
python pgdh_modal/design.py rfd3 --strategy binder --num-designs 8

# Helix-hairpin inpainting (requires helix_hairpin_binder.pdb in structures/)
python pgdh_modal/design.py rfd3 --strategy helix_hairpin_segment --num-designs 8
python pgdh_modal/design.py rfd3 --strategy helix_hairpin_partial_t6 --num-designs 8
python pgdh_modal/design.py rfd3 --strategy helix_hairpin_partial_t10 --num-designs 8

# All inpainting strategies
python pgdh_modal/design.py rfd3 --strategy inpaint --num-designs 8

# Strategies: active_site, dimer_interface, helix_hairpin_segment,
#             helix_hairpin_partial_t6, helix_hairpin_partial_t10,
#             binder (=active_site+dimer), inpaint (=all helix_hairpin), all
```

Outputs land in `pgdh_modal/out/{boltzgen,rfdiffusion3}/r{N}/{strategy}/`.

### Phase 2: SYNC

```bash
python pgdh_modal/sync.py
```

Verify output shows round N designs in `by_round`.

### Phase 3: FAST EVAL (BoltzGen refolding)

```bash
python pgdh_modal/evaluate.py --fast
```

This promotes BoltzGen self-consistency metrics (free) and submits refolding
for RFD3 designs (GPU). Then re-sync:

```bash
python pgdh_modal/sync.py
```

### Phase 4: SLOW EVAL (Boltz-2 cross-validation, optional)

Only if fast eval results look promising (designs with RMSD < 2.5A):

```bash
python pgdh_modal/evaluate.py --slow --auto
```

Then re-sync:

```bash
python pgdh_modal/sync.py
```

### Phase 5: SCORING (ipSAE, optional)

For designs with structures + PAE files:

```bash
python pgdh_modal/evaluate.py --score
python pgdh_modal/sync.py
```

### Phase 6: ROUND SUMMARY

Write a round summary at `pgdh_campaign/rounds/r{N}_summary.md`:

- Round number and date
- Strategies used (tools, binding sites, parameters)
- Design count per tool/strategy
- Results overview (pass rates, best metrics)
- Key observations
- Recommendations for next round

### Phase 7: PUBLISH

```bash
python pgdh_campaign/generate_pages.py --designs-dir pgdh_modal/out/designs/
git add docs/ pgdh_campaign/rounds/ && git commit -m "Round N results" && git push
```

### Phase 8: ADVANCE

Update CLAUDE.md: change `Current round: N` to `Current round: N+1`.

## Tool Parameters

### BoltzGen
| Parameter | Default | Notes |
|-----------|---------|-------|
| `--num-designs` | 10 | 3-5 for testing, 10-50 for production |
| `--strategy` | required | active_site, dimer_interface, surface, all |
| `--extra-args` | `--write_full_pae` | Additional BoltzGen CLI args |

### RFdiffusion3
| Parameter | Default | Notes |
|-----------|---------|-------|
| `--num-designs` | 8 | Designs per batch |
| `--num-batches` | 1 | Number of batches |
| `--step-scale` | 1.5 | Higher = more exploration (3 for quick test) |
| `--gamma-0` | 0.6 | Lower = more diverse (0.2 for quick test) |
| `--num-timesteps` | 200 | Diffusion steps (500 for high quality) |

## Quality Thresholds

| Metric | Good | Strong | Source |
|--------|------|--------|--------|
| filter_rmsd (BoltzGen) | < 2.5 A | < 2.0 A | Design self-consistency |
| Refolding RMSD | < 2.5 A | < 1.5 A | BoltzGen folding mode |
| ipTM (design) | > 0.5 | > 0.7 | BoltzGen design metrics |
| ipTM (Boltz-2) | > 0.5 | > 0.7 | Cross-validation |
| pLDDT (Boltz-2) | > 70 | > 85 | Cross-validation |
| ipSAE | > 0.61 | > 0.70 | ipSAE scoring |
| pDockQ | > 0.23 | > 0.50 | ipSAE scoring |

## Files

| File | Role |
|------|------|
| `pgdh_modal/design.py` | Submit design jobs (BoltzGen, RFD3) |
| `pgdh_modal/evaluate.py` | Submit eval jobs (refolding, Boltz-2, ipSAE) |
| `pgdh_modal/sync.py` | Collect, rank, write to local designs/ |
| `pgdh_modal/out/` | All local data |
| `resources/biomodals/modal_boltzgen.py` | Modal BoltzGen wrapper |
| `resources/biomodals/modal_rfdiffusion3.py` | Modal RFD3 wrapper |
| `resources/biomodals/modal_boltz.py` | Modal Boltz-2 wrapper |
| `resources/biomodals/modal_ipsae.py` | Modal ipSAE wrapper |

## Checklist

- [ ] Design jobs submitted with correct round/strategy dirs
- [ ] `sync.py` shows round N designs in by_round
- [ ] Fast eval (--fast) run, re-synced
- [ ] Slow eval (--slow --auto) if promising designs exist
- [ ] Round summary written to pgdh_campaign/rounds/r{N}_summary.md
- [ ] Pages generated with --designs-dir and pushed
- [ ] CLAUDE.md updated to next round
