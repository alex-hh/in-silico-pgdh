# 15-PGDH Binder Design Campaign — Berlin Bio Hackathon

## Context

Design protein binders targeting 15-PGDH (PDB: 2GDZ, UniProt: P15428) for the Berlin Bio Hackathon x Adaptyv competition. Submissions: up to 10 designs (max 250 AA), judged on **novelty/originality** then **binding affinity (KD)**. Deadline: Feb 27-28.

The workflow is **agentic**: Claude Code orchestrates the campaign by invoking skills (`/boltzgen`, `/boltz`, `/protein-qc`, etc.) and running Modal commands step-by-step. No standalone orchestrator script — Claude Code _is_ the orchestrator.

## Target: 15-PGDH

- 1.65 Å crystal structure, homodimer, NAD+ bound
- Active site: Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217
- Dimer interface: Phe161, Leu150, Ala153, Ala146, Leu167, Ala168, Tyr206, Leu171, Met172

## Campaign Strategy: 3 Binding Approaches

| # | Strategy | Hotspots | Binder Size | Novelty |
|---|----------|----------|-------------|---------|
| 1 | Active site blocker | Catalytic + substrate residues | 80-120 AA | Medium |
| 2 | Dimer disruptor | Interface residues | 80-140 AA | High |
| 3 | Surface (model-free) | None — BoltzGen auto-detects | 60-140 AA | High |

## Step-by-Step Agentic Workflow

### Step 0: Setup & Target Prep
**Skills**: `/setup`, `/pdb`

1. Verify Modal is authenticated: `modal token list`
2. Confirm biomodals repo exists at `resources/biomodals/`
3. Download 2GDZ structure:
   ```bash
   wget -O pgdh_campaign/structures/2GDZ.cif https://files.rcsb.org/download/2GDZ.cif
   ```
4. Inspect CIF to verify chain IDs and `label_seq_id` numbering for hotspot residues
5. Create campaign directory structure:
   ```
   pgdh_campaign/
     structures/2GDZ.cif
     configs/
     out/boltzgen/
     out/boltz2/
     out/final/
   ```

### Step 1: Create BoltzGen YAML Configs
**Skills**: `/boltzgen`

Write 3 YAML files in `pgdh_campaign/configs/`, one per strategy. Each references `../structures/2GDZ.cif`. Residue numbers must use `label_seq_id` (verified in Step 0).

**strategy1_active_site.yaml** — hotspots on catalytic pocket
**strategy2_dimer_interface.yaml** — hotspots on dimer interface
**strategy3_surface.yaml** — no hotspots, free targeting

### Step 2: Run BoltzGen (3 parallel Modal jobs)
**Skills**: `/boltzgen`

Launch all 3 strategies in parallel from `pgdh_campaign/`:
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

**Expected output per strategy**: `out/boltzgen/<run_name>/` containing:
- `final_ranked_designs/` — top candidates with metrics
- `intermediate_designs_inverse_folded/aggregate_metrics_analyze.csv` — all metrics

### Step 3: Collect & Filter Results
**Skills**: `/protein-qc`

Parse the BoltzGen output CSVs. Filter using thresholds from the protein-qc skill:
- `iptm > 0.4` (interface confidence)
- `plddt_design > 0.70` (structural quality)
- `filter_rmsd_design < 3.0 Å` (self-consistency)

Rank by composite score and select top ~10 per strategy (30 total) for cross-validation.

### Step 4: Cross-Validate Top Candidates with Boltz-2
**Skills**: `/boltz`

For each top candidate:
1. Extract binder sequence from BoltzGen output CIF
2. Create Boltz-2 YAML with target + binder sequences
3. Run Boltz-2 prediction:
   ```bash
   modal run ../resources/biomodals/modal_boltz.py \
     --input-yaml validation_N.yaml \
     --params-str "--use_msa_server --recycling_steps 10 --diffusion_samples 5" \
     --out-dir out/boltz2 --run-name candidate_N
   ```
4. Parse Boltz-2 ipTM and pLDDT from outputs

### Step 5: Final Ranking & Selection
**Skills**: `/protein-qc`, `/ipsae`

Combine BoltzGen metrics + Boltz-2 cross-validation into final composite score:
```
0.25*boltzgen_iptm + 0.20*boltz2_iptm + 0.20*plddt +
0.15*pose_consistency + 0.10*delta_sasa + 0.10*diversity_bonus
```

Selection rules:
- Pick top 10 by composite score
- Ensure at least 2 per strategy (novelty diversity)
- No two designs with >70% sequence identity
- All sequences <= 250 AA

Output: `out/final/submission.fasta` with 10 binder sequences

## Files to Create

| File | Purpose |
|------|---------|
| `pgdh_campaign/structures/2GDZ.cif` | Downloaded target structure |
| `pgdh_campaign/configs/strategy1_active_site.yaml` | BoltzGen config — active site |
| `pgdh_campaign/configs/strategy2_dimer_interface.yaml` | BoltzGen config — dimer interface |
| `pgdh_campaign/configs/strategy3_surface.yaml` | BoltzGen config — free surface |
| `pgdh_campaign/out/final/submission.fasta` | Final 10 sequences for submission |

## Existing Files (no changes needed)

| File | Used in |
|------|---------|
| `resources/biomodals/modal_boltzgen.py` | Step 2 — design generation |
| `resources/biomodals/modal_boltz.py` | Step 4 — cross-validation |

## Verification

1. After Step 0: `2GDZ.cif` exists, chain IDs and residue numbering confirmed
2. After Step 1: 3 YAML files pass `boltzgen check` validation
3. After Step 2: Each strategy dir has `aggregate_metrics_analyze.csv` with results
4. After Step 5: `submission.fasta` has exactly 10 sequences, all <= 250 AA

## Time Budget

| Step | Duration |
|------|----------|
| Steps 0-1: Prep + configs | ~15 min |
| Step 2: BoltzGen (3 parallel, 50 each) | ~30-60 min |
| Step 3: Filter | ~10 min |
| Step 4: Boltz-2 validation | ~30-60 min |
| Step 5: Rank + select | ~10 min |
| **Total** | **~2-3 hours** |
