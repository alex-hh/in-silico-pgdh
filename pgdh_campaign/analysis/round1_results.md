# Round 1 Results Analysis

## Overview

Round 1 production runs used BoltzGen on Lyceum (A100, 600s timeout) with 10 designs per strategy. All runs completed successfully within the timeout.

| Strategy | Designs | ipTM >= 0.75 | ipTM 0.65-0.75 | ipTM < 0.65 | Best ipTM |
|----------|---------|-------------|----------------|-------------|-----------|
| S1: Active Site | 10 | 5 (50%) | 2 (20%) | 3 (30%) | 0.792 |
| S2: Dimer Interface | 10 | 7 (70%) | 0 (0%) | 3 (30%) | 0.809 |
| S3: Surface (test, 3 only) | 3 | 0 | 1 | 2 | 0.614 |

**S2 Dimer Interface is clearly the strongest strategy** — 70% pass rate at ipTM >= 0.75 vs 50% for S1.

### S3 Surface R2 (10 designs, model-free)

S3 R2 completed with refined size range (80-120 AA). Much improved over the 3-design test:

| Rank | Design | ipTM | Min PAE | pTM | RMSD | Res | H-bonds | ALA |
|------|--------|------|---------|-----|------|-----|---------|-----|
| 1 | config_9 | **0.768** | 2.17 | 0.856 | 0.88 | 103 | 4 | 0.13 |
| 2 | config_0 | 0.693 | 2.38 | 0.785 | 0.98 | 99 | 8 | 0.15 |
| 3 | config_4 | 0.681 | 2.58 | 0.756 | 0.75 | 103 | 9 | 0.13 |
| 4 | config_3 | 0.655 | 2.82 | 0.730 | 1.41 | 96 | 5 | 0.17 |
| 5 | config_1 | 0.705 | 2.46 | 0.803 | 2.39 | 109 | 6 | 0.11 |
| 6-10 | (poor) | < 0.45 | > 7.0 | < 0.73 | > 2.7 | — | — | > 0.28 |

**1 excellent, 4 good** — 50% pass rate at ipTM >= 0.65. The bottom 5 all have very high ALA fractions (0.28-0.51), confirming that BoltzGen surface-free designs are more prone to degenerate alanine-rich solutions.

### Updated Summary Across All Strategies

| Strategy | Designs | ipTM >= 0.75 | ipTM 0.65-0.75 | Pass Rate (>= 0.65) | Best ipTM |
|----------|---------|-------------|----------------|---------------------|-----------|
| S1: Active Site | 10 | 5 (50%) | 2 (20%) | **70%** | 0.792 |
| S2: Dimer Interface | 10 | 7 (70%) | 0 (0%) | **70%** | 0.809 |
| S3: Surface R2 | 10 | 1 (10%) | 4 (40%) | **50%** | 0.768 |
| **Total** | **30** | **13** | **6** | **63%** | **0.809** |

## Top Candidates

Ranked by ipTM across all strategies:

| Rank | Design | Strategy | ipTM | Min PAE | pTM | RMSD (A) | Res | H-bonds | dSASA |
|------|--------|----------|------|---------|-----|----------|-----|---------|-------|
| 1 | S2 config_6 | Dimer | **0.809** | 1.61 | 0.891 | 0.68 | 136 | 7 | 1769 |
| 2 | S1 config_3 | Active Site | **0.792** | 2.03 | 0.866 | 0.57 | 104 | 6 | 1494 |
| 3 | S1 config_5 | Active Site | **0.791** | 1.81 | 0.858 | 0.58 | 131 | 6 | 1891 |
| 4 | S2 config_1 | Dimer | **0.789** | 1.89 | 0.872 | 0.42 | 127 | 4 | 1688 |
| 5 | S1 config_2 | Active Site | **0.778** | 2.23 | 0.858 | 0.47 | 84 | 3 | 1408 |
| 6 | S2 config_4 | Dimer | **0.771** | 1.98 | 0.850 | 0.55 | 98 | 5 | 1453 |
| 7 | S1 config_4 | Active Site | **0.765** | 1.99 | 0.851 | 0.69 | 134 | 5 | 1773 |
| 8 | S2 config_9 | Dimer | **0.759** | 2.27 | 0.833 | 0.62 | 87 | 5 | 1652 |
| 9 | S2 config_8 | Dimer | **0.751** | 2.39 | 0.846 | 0.51 | 82 | 1 | 1433 |
| 10 | S2 config_5 | Dimer | 0.746 | 2.25 | 0.844 | 0.98 | 109 | 8 | 1459 |
| 11 | S2 config_3 | Dimer | 0.740 | 2.24 | 0.823 | 0.74 | 84 | 9 | 1579 |
| 12 | S1 config_7 | Active Site | 0.712 | 2.26 | 0.806 | 0.67 | 92 | 11 | 1577 |

## Key Observations

### 1. Self-consistency (filter_rmsd) is excellent

Nearly all designs have filter_rmsd < 2.5 A (BoltzGen's internal refolding check). Only 3 outliers across both strategies — the same designs that score poorly on ipTM. This suggests BoltzGen's diffusion + inverse folding pipeline is robust for this target.

### 2. Design-folding RMSD is universally poor

The `designfolding-filter_rmsd` column (which uses Boltz-2's folding model) is > 4 A for every single design, even the best ones. This likely means:
- The design-folding step uses **sequence-only** prediction (no template), so the binder folds differently without the target present
- This is expected for binders — they're designed in the context of the target and may be intrinsically disordered or adopt a different fold alone
- **This metric should NOT be used as a filter** for binder design

### 3. Interface quality correlates with ipTM

Designs with ipTM > 0.75 consistently show:
- Min PAE < 2.5 (confident interface contacts)
- pTM > 0.83 (confident overall fold)
- filter_rmsd < 1.0 A (self-consistent)
- Multiple H-bonds (3-11)

### 4. Alanine fraction is a concern

Several designs have ALA fractions above 0.15-0.20:
- S1 config_7: ALA = 0.23
- S2 config_8: ALA = 0.24
- S1 config_6: ALA = 0.34 (worst design, ipTM = 0.26)

High alanine correlates with poor designs but moderate levels (0.10-0.18) appear in some good designs too. BoltzGen's built-in filter threshold is 0.30.

### 5. Unpaired cysteines are common

8 out of 20 designs have exactly 1 unpaired cysteine. These are a liability for expression (can form non-native disulfides). None of the designs have paired cysteines forming intended disulfide bonds.

### 6. Size distribution

- S1: 83-134 AA (median ~109)
- S2: 82-136 AA (median ~98)
- All well under the 250 AA submission limit

### 7. Failure modes

The 1-2 clearly bad designs per strategy (ipTM < 0.5) share:
- Very high filter_rmsd (> 7 A)
- Min PAE > 7
- High alanine/glycine fractions
- These are easy to filter out

## Strategy Comparison

### S1 Active Site
- **Strengths**: Good H-bond counts at the catalytic site, several designs with strong minPAE < 2.0
- **Weaknesses**: More variable quality, the worst designs are worse than S2's worst
- **Best for**: If targeting enzymatic inhibition is important for the competition

### S2 Dimer Interface
- **Strengths**: Higher pass rate (70%), more consistent quality, best overall design (0.809)
- **Weaknesses**: Slightly fewer H-bonds on average
- **Best for**: Novelty score in competition (dimer disruption is less conventional)

### S3 Surface
- **Strengths**: Fully model-free, highest novelty
- **Weaknesses**: Poor results with only 3 test designs; R2 with 10 designs still running
- **Needs**: More designs to properly evaluate; may need hotspot guidance

## Recommendations for Next Steps

### Immediate (R2 refinements)
1. **S3 R2 is running** (10 designs, 80-120 AA range) — wait for results
2. Consider S1/S2 R2 with tighter size ranges based on what worked:
   - S1: 90-120 AA (config_3 at 104 AA was excellent)
   - S2: 80-110 AA (config_4 at 98 AA, config_9 at 87 AA both strong)

### Filtering pipeline
3. Apply multi-stage filtering:
   - **Stage 1 (hard filters)**: ipTM > 0.60, filter_rmsd < 2.5, ALA_fraction < 0.25
   - **Stage 2 (quality)**: ipTM > 0.70, min_PAE < 2.5, pTM > 0.80
   - **Stage 3 (liabilities)**: Flag unpaired Cys, high hydrophobic patches

### Cross-validation
4. Run top 10-15 designs through **Boltz-2** for independent structure prediction
5. Run **ipSAE** scoring for binding confidence ranking
6. Consider **AlphaFold2-Multimer** for a third orthogonal validation

### Final selection
7. Pick 10 for submission ensuring:
   - At least 2 per strategy (diversity)
   - No two designs > 70% sequence identity
   - All pass Stage 1+2 filters
   - Prioritise S2 designs for novelty bonus
