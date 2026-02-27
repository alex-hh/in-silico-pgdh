# 15-PGDH Binder Design Campaign — Execution Plan

## Overview
- **Target**: 15-PGDH (PDB: 2GDZ, UniProt: P15428)
- **Goal**: 10 binder sequences (≤250 AA each) for Adaptyv submission
- **Judging**: Novelty/originality first, then binding affinity (KD)
- **Tool**: BoltzGen → Boltz-2 cross-validation pipeline on Modal

---

## Step 0: Setup & Target Prep (~5 min)
**Skills**: `/setup`, `/pdb`

- [ ] Verify Modal authenticated: `modal token list`
- [ ] Confirm `resources/biomodals/` exists with `modal_boltzgen.py` and `modal_boltz.py`
- [ ] Download 2GDZ structure:
  ```bash
  wget -O pgdh_campaign/structures/2GDZ.cif https://files.rcsb.org/download/2GDZ.cif
  ```
- [ ] Inspect CIF to verify chain IDs and `label_seq_id` numbering
  - Need: chain ID for monomer (likely A), residue numbers for hotspots
  - Active site: Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217
  - Dimer interface: Phe161, Leu150, Ala153, Ala146, Leu167, Ala168, Tyr206, Leu171, Met172
- [ ] Create directory structure:
  ```
  pgdh_campaign/
    structures/2GDZ.cif
    configs/
    out/boltzgen/
    out/boltz2/
    out/final/
  ```

---

## Step 1: Create BoltzGen YAML Configs (~10 min)
**Skills**: `/boltzgen`

Invoke `/boltzgen` skill to get exact YAML format, then create 3 configs:

### Strategy 1: Active Site Blocker
- **File**: `configs/strategy1_active_site.yaml`
- **Hotspots**: Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217 (chain A)
- **Binder size**: 80-120 AA
- **Rationale**: Block catalytic pocket → direct enzyme inhibition
- **Novelty**: Medium (common approach but effective)

### Strategy 2: Dimer Interface Disruptor
- **File**: `configs/strategy2_dimer_interface.yaml`
- **Hotspots**: Phe161, Leu150, Ala153, Ala146, Leu167, Ala168, Tyr206, Leu171, Met172 (chain A)
- **Binder size**: 80-140 AA
- **Rationale**: Disrupt homodimer → novel mechanism of inhibition
- **Novelty**: High (unconventional target site)

### Strategy 3: Surface (Model-Free)
- **File**: `configs/strategy3_surface.yaml`
- **Hotspots**: None — let BoltzGen auto-detect binding sites
- **Binder size**: 60-140 AA
- **Rationale**: Unbiased exploration may find novel binding sites
- **Novelty**: High (fully computational discovery)

**Residue numbering**: Must use `label_seq_id` from CIF (verified in Step 0).

---

## Step 2: Run BoltzGen — 3 Parallel Modal Jobs (~30-60 min)
**Skills**: `/boltzgen`

Launch all 3 in parallel (each generates 50 designs):

```bash
cd pgdh_campaign

# Strategy 1: Active site
TIMEOUT=300 modal run ../resources/biomodals/modal_boltzgen.py \
  --input-yaml configs/strategy1_active_site.yaml \
  --protocol protein-anything \
  --num-designs 50 \
  --out-dir out/boltzgen --run-name s1_active_site

# Strategy 2: Dimer interface
TIMEOUT=300 modal run ../resources/biomodals/modal_boltzgen.py \
  --input-yaml configs/strategy2_dimer_interface.yaml \
  --protocol protein-anything \
  --num-designs 50 \
  --out-dir out/boltzgen --run-name s2_dimer

# Strategy 3: Surface free
TIMEOUT=300 modal run ../resources/biomodals/modal_boltzgen.py \
  --input-yaml configs/strategy3_surface.yaml \
  --protocol protein-anything \
  --num-designs 50 \
  --out-dir out/boltzgen --run-name s3_surface
```

**Expected output per strategy**: `out/boltzgen/<run_name>/`
- `final_ranked_designs/` — top candidates with CIF structures
- `intermediate_designs_inverse_folded/aggregate_metrics_analyze.csv` — all metrics

---

## Step 3: Collect & Filter Results (~10 min)
**Skills**: `/protein-qc`

Invoke `/protein-qc` to confirm thresholds, then filter:

### Filter Criteria (from protein-qc skill)
| Metric | Threshold | Meaning |
|--------|-----------|---------|
| `iptm` | > 0.4 | Interface confidence |
| `plddt_design` | > 0.70 | Structural quality |
| `filter_rmsd_design` | < 3.0 Å | Self-consistency |

### Process
1. Parse `aggregate_metrics_analyze.csv` from each strategy
2. Apply filters above
3. Rank by composite: `0.4*iptm + 0.3*plddt + 0.3*(1 - rmsd/3.0)`
4. Select top ~10 per strategy (30 total candidates for cross-validation)
5. Extract binder sequences from CIF files for each candidate

---

## Step 4: Cross-Validate with Boltz-2 (~30-60 min)
**Skills**: `/boltz`

For each of the ~30 top candidates:

1. Create Boltz-2 YAML with target chain A sequence + binder sequence
2. Run Boltz-2 prediction:
   ```bash
   modal run ../resources/biomodals/modal_boltz.py \
     --input-yaml validation_N.yaml \
     --params-str "--use_msa_server --recycling_steps 10 --diffusion_samples 5" \
     --out-dir out/boltz2 --run-name candidate_N
   ```
3. Parse Boltz-2 ipTM and pLDDT from output
4. Compare pose with BoltzGen prediction for consistency

**Batching**: Run multiple Boltz-2 jobs in parallel (batches of ~5-10).

---

## Step 5: Final Ranking & Selection (~10 min)
**Skills**: `/protein-qc`, `/ipsae`

### Composite Score Formula
```
score = 0.25 * boltzgen_iptm
      + 0.20 * boltz2_iptm
      + 0.20 * plddt
      + 0.15 * pose_consistency
      + 0.10 * delta_sasa
      + 0.10 * diversity_bonus
```

### Selection Rules
1. Rank all ~30 candidates by composite score
2. Pick top 10 ensuring:
   - At least 2 designs per strategy (diversity across binding modes)
   - No two designs with >70% sequence identity (sequence diversity)
   - All sequences ≤ 250 AA
3. If ipSAE is available, use it as tiebreaker

### Output
- `out/final/submission.fasta` — 10 binder sequences with headers containing:
  - Design name, strategy, composite score, key metrics
- `out/final/ranking_table.csv` — full ranking with all metrics

---

## Verification Checklist

- [ ] After Step 0: `2GDZ.cif` exists, chain IDs confirmed
- [ ] After Step 1: 3 YAML configs created, residue numbers match CIF
- [ ] After Step 2: Each strategy dir has CSV with results (150 designs total)
- [ ] After Step 3: ~30 filtered candidates identified
- [ ] After Step 4: Boltz-2 ipTM available for all candidates
- [ ] After Step 5: `submission.fasta` has exactly 10 sequences, all ≤ 250 AA

---

## Time Budget

| Step | Duration | Status |
|------|----------|--------|
| Step 0: Setup & target prep | ~5 min | ⬜ |
| Step 1: Create YAML configs | ~10 min | ⬜ |
| Step 2: BoltzGen (3×50 parallel) | ~30-60 min | ⬜ |
| Step 3: Filter results | ~10 min | ⬜ |
| Step 4: Boltz-2 validation | ~30-60 min | ⬜ |
| Step 5: Final ranking | ~10 min | ⬜ |
| **Total** | **~2-3 hours** | |

---

## Key Decisions & Notes

- **Why BoltzGen over RFdiffusion?** BoltzGen does all-atom design (backbone + sequence + side chains) in one shot — faster iteration, and good for ligand-aware design around NAD+.
- **Why 3 strategies?** Diversity is rewarded in judging. Different binding modes = higher novelty score.
- **Why Boltz-2 cross-validation?** Independent structure predictor catches BoltzGen hallucinations. Designs that look good in both tools are more likely real binders.
- **50 designs per strategy**: Gives enough statistical power to find good candidates after filtering (~20-30% pass rate expected).
