# Next Steps: Filtering & Design Improvements

## Current State

- 30 production designs: 10 S1, 10 S2, 10 S3 R2
- 13 excellent (ipTM >= 0.75), 6 good (0.65-0.75) — 63% pass rate at ipTM >= 0.65
- No cross-validation yet (Boltz-2, ipSAE, AF2)
- Deadline: Feb 27-28

### Key finding from S3 R2
S3 Surface (model-free) produced 1 excellent design (ipTM 0.768) but 50% of designs had degenerate high-ALA compositions (0.28-0.51). The strategy works but is less reliable than hotspot-guided S1/S2. Worth including 1-2 S3 designs for novelty diversity.

## 1. Filtering Pipeline

### Proposed multi-stage filter

```
Stage 1: Hard cutoffs (remove clearly bad designs)
  - ipTM > 0.55
  - filter_rmsd < 3.0 A
  - ALA_fraction < 0.30
  - No "X" residues in sequence
  → Expected: ~17/20 pass (removes S1-config_6, S2-config_0, borderline S2-config_7)

Stage 2: Quality gate (select promising candidates)
  - ipTM > 0.70
  - min_PAE < 2.5
  - pTM > 0.80
  - filter_rmsd < 1.5
  → Expected: ~12/20 pass

Stage 3: Liability check (flag but don't hard-filter)
  - Unpaired cysteines → flag, consider mutating Cys→Ser
  - ALA_fraction > 0.20 → flag
  - Large hydrophobic patches > 2500 A² → flag
  - High liability score > 200 → flag
  → Use for tiebreaking, not elimination
```

### Metrics NOT to use as filters

- **designfolding-filter_rmsd**: Universally > 4 A even for best designs. This is sequence-only refolding without the target — binders are expected to be disordered or differently folded alone.
- **designfolding-* metrics generally**: All zero or non-informative because the binder doesn't fold correctly in isolation.

### Implementation

The `evaluate_designs.py` pipeline already handles metric standardisation. We should:
1. Add the Stage 1/2/3 thresholds as a config
2. Compute a composite score weighting ipTM (0.35), min_PAE (0.25), pTM (0.20), filter_rmsd (0.10), H-bonds (0.10)
3. Normalise each metric to 0-1 range before combining

## 2. Cross-Validation Plan

### Boltz-2 (highest priority)
- Run top 12-15 designs through Boltz-2 with MSA server
- Key params: `--use_msa_server --recycling_steps 10 --diffusion_samples 5`
- Look for: ipTM agreement with BoltzGen (confirms interface), pLDDT of binder
- Machine: gpu.a100, ~2-3 min per design

### ipSAE scoring
- Score all passing designs using ipSAE
- CPU-only, fast — run on Lyceum cpu machine
- Provides orthogonal binding confidence metric

### What to look for in cross-validation
- **Agreement**: Boltz-2 ipTM should correlate with BoltzGen ipTM
- **Red flags**: If Boltz-2 shows no interface (ipTM < 0.3) for a BoltzGen ipTM > 0.7 design, that's a false positive
- **Binding mode**: Check if Boltz-2 places the binder at the intended site (active site vs dimer interface)

## 3. Design Improvements for R2/R3

### What worked in R1
- **S2 dimer interface** had highest success rate (70% excellent)
- **Mid-range sizes** (84-136 AA) — neither too small nor too large
- **Low self-consistency RMSD** (< 1.0 A) strongly correlates with high ipTM
- Designs with balanced amino acid composition (no single AA > 20%)

### R2 config changes (already created)
- S1: Narrowed from 80-120 to **90-120 AA** (drop the very small designs)
- S2: Narrowed from 80-140 to **100-140 AA** (favour slightly larger for more interface contacts)
- S3: Narrowed from 60-140 to **80-120 AA** (avoid very small/large)

### Potential R3 ideas
1. **Hotspot refinement**: Based on which residues BoltzGen chose to contact, refine hotspot lists
   - Inspect top designs' contact maps to see which target residues are contacted
   - Add/remove hotspots that consistently appear/don't appear
2. **Size-optimised runs**: Run 20 designs at the exact sweet-spot size range for each strategy
3. **Alternative tool**: Try RFdiffusion3 for backbone generation + ProteinMPNN for sequence design
   - Different generative model may find different solutions
   - `/pgdh_rfdiffusion3` skill is ready
4. **Sequence optimisation**: Take top backbone from BoltzGen, redesign sequence with ProteinMPNN/SolubleMPNN
   - May improve expression (reduce liabilities)
   - Can specifically remove unpaired cysteines

## 4. Liability Remediation

### Unpaired cysteines (8/20 designs)
- **Option A**: Mutate Cys→Ser in affected designs, re-validate with Boltz-2
- **Option B**: Prefer cysteine-free designs in final selection (S2 has 4 designs with no Cys)
- **Recommendation**: Option B for now (simpler), Option A only if needed to fill slots

### Hydrophobic patches
- Several designs have large hydrophobic patches (> 2500 A²)
- These may cause aggregation during expression
- Monitor but don't hard-filter — competition judges on binding, not expression

### Protease sites
- Most designs have multiple tryptic/Asp-cleavage sites
- Normal for designed proteins, not a concern for in vitro binding assays

## 5. Final Selection Strategy

Need exactly 10 designs for submission. Strategy:

```
Allocation:
  - 4-5 from S2 Dimer (strongest strategy, highest novelty)
  - 3-4 from S1 Active Site (good diversity, direct inhibition)
  - 1-2 from S3 Surface (model-free novelty, if R2 produces good candidates)

Selection criteria (ordered):
  1. Boltz-2 cross-validated ipTM > 0.5 (if cross-validation done)
  2. BoltzGen ipTM > 0.70
  3. No unpaired cysteines (preferred)
  4. Sequence diversity (< 70% identity between any pair)
  5. Size diversity (mix of 80-140 AA)
```

## 6. Timeline

| Task | Priority | Time | Status |
|------|----------|------|--------|
| Wait for S3 R2 results | High | ~20 min | **Done** (1 excellent, 4 good) |
| Apply Stage 1+2 filters | High | 10 min | Not started |
| Run Boltz-2 on top 12 | High | 30-60 min | Not started |
| Run ipSAE on all passing | Medium | 15 min | Not started |
| Consider R2 launches (S1/S2) | Medium | 30-60 min | Configs ready |
| Final ranking + selection | High | 15 min | Blocked on validation |
| Sequence liability fixes | Low | 15 min | If needed |
