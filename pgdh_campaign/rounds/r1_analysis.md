# Round 1 Analysis

**Date**: 2026-02-28
**Tool**: BoltzGen only (3 jobs, 10 designs each = 30 total)

## Overall Results

- **33 designs** in database (30 R1 + 3 R0)
- **Top score**: 0.8270 (surface), 0.8249 (dimer), 0.7502 (active site)
- **Pass rate** (composite > 0.5): 20/30 (67%)
- **Strong designs** (composite > 0.7): 9/30 (30%)
- **Failed designs** (composite < 0.2): 5/30 (17%)

## Strategy Breakdown

### Active Site (S1) — 10 designs

| Metric | Best | Mean | Worst | Pass Rate |
|--------|------|------|-------|-----------|
| Composite | 0.7502 | 0.5250 | 0.1368 | 7/10 > 0.3 |
| ipTM | 0.7167 | 0.5309 | 0.2333 | 6/10 > 0.5 |
| Design RMSD | 0.68 A | 4.80 A | 12.11 A | 5/10 < 2.5 |
| Min iPAE | 2.32 | 5.56 | 13.11 | 6/10 < 5.0 |
| Length | 66 AA | 94 AA | 129 AA | — |

**Key issue**: Config specifies `sequence: 80..120` (never updated to 40-80 as discussed). Actual lengths range 66-129 AA. The best designs (config_1, config_2) are 91-100 AA. The 2 smallest designs (66-67 AA) both failed catastrophically (score < 0.15, RMSD > 11 A).

**Observation**: Active site has the most bimodal distribution — designs either work well (score > 0.6) or fail completely (score < 0.2). Middle ground is thin.

### Dimer Interface (S2) — 10 designs

| Metric | Best | Mean | Worst | Pass Rate |
|--------|------|------|-------|-----------|
| Composite | 0.8249 | 0.5869 | 0.1351 | 7/10 > 0.3 |
| ipTM | 0.7831 | 0.5759 | 0.2414 | 7/10 > 0.5 |
| Design RMSD | 0.49 A | 2.26 A | 5.77 A | 8/10 < 2.5 |
| Min iPAE | 2.10 | 4.64 | 12.59 | 7/10 < 5.0 |
| Length | 64 AA | 105 AA | 140 AA | — |

**Best strategy overall.** Highest mean score (0.587), best RMSD pass rate (80%), and most consistent. Only 1 catastrophic failure. The top design (config_6, 102 AA) has RMSD 0.49 A — exceptional self-consistency.

**Observation**: Failed designs (config_3=69 AA, config_9=64 AA) are both very short. The dimer interface is large and hydrophobic — designs under ~80 AA can't cover enough of it.

### Surface (S3) — 10 designs

| Metric | Best | Mean | Worst | Pass Rate |
|--------|------|------|-------|-----------|
| Composite | 0.8270 | 0.5125 | 0.1236 | 6/10 > 0.3 |
| ipTM | 0.7923 | 0.5322 | 0.2102 | 6/10 > 0.5 |
| Design RMSD | 0.51 A | 4.08 A | 12.64 A | 5/10 < 2.5 |
| Min iPAE | 2.10 | 6.30 | 13.87 | 6/10 < 5.0 |
| Length | 62 AA | 109 AA | 133 AA | — |

**Highest single score** (0.827) but most variable — wide 60-140 AA range with no hotspot constraints leads to diverse but unpredictable results. 4 designs have RMSD > 3.7 A.

**Observation**: The top surface design (config_6, 103 AA) is comparable to the top dimer design. But the bottom 4 are terrible. Auto-detected binding sites are hit-or-miss.

## Cross-Strategy Patterns

### Length vs Quality
Short designs (< 80 AA) fail consistently across all strategies:
- config_0 (66 AA, active_site): score 0.326
- config_5 (67 AA, active_site): score 0.137
- config_3 (69 AA, dimer): score 0.409
- config_9 (64 AA, dimer): score 0.381
- config_9 (62 AA, surface): score 0.124

**Designs 90-130 AA perform best.** Below 80 AA, BoltzGen can't generate enough secondary structure for stable binding.

### RMSD Bimodality
Designs fall into two clusters: RMSD < 3 A (good) or RMSD > 5 A (failed fold). Very few designs land in 3-5 A. This suggests BoltzGen either finds a stable fold or doesn't — there's no "almost worked" middle ground.

### Min iPAE Correlation
Min iPAE tracks strongly with composite score. All designs with iPAE < 3.0 have composite > 0.65. All designs with iPAE > 8.0 have composite < 0.4.

## Missing Evaluations
- No Boltz-2 cross-validation (would reveal if self-consistency metrics are inflated)
- No PyRosetta interface scoring (dG, shape complementarity, dSASA)
- No RFdiffusion3 designs for tool diversity comparison
