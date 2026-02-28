# Round 1 Summary

**Date**: 2026-02-28
**Tool**: BoltzGen (3 jobs, 10 designs each)
**Total designs**: 30

## Strategies

| Strategy | Binder Size | Designs | Best Score | Mean Score |
|----------|-------------|---------|------------|------------|
| S1 Active Site | 40-80 AA (config) | 10 | 0.7502 | 0.5250 |
| S2 Dimer Interface | 80-140 AA | 10 | 0.8249 | 0.5869 |
| S3 Surface | 60-140 AA | 10 | 0.8270 | 0.5125 |

## Top 5 Designs

| Rank | Design | Strategy | Score | ipTM | RMSD | Min iPAE | Length |
|------|--------|----------|-------|------|------|----------|--------|
| 1 | boltzgen_r1_s3_config_6 | surface | 0.8270 | 0.7923 | 0.51 | 2.10 | 103 |
| 2 | boltzgen_r1_s2_config_6 | dimer | 0.8249 | 0.7831 | 0.49 | 2.10 | 102 |
| 3 | boltzgen_r1_s1_config_1 | active_site | 0.7502 | 0.7167 | 1.30 | 2.32 | 100 |
| 4 | boltzgen_r1_s2_config_1 | dimer | 0.7474 | 0.7201 | 1.60 | 2.14 | 139 |
| 5 | boltzgen_r1_s3_config_2 | surface | 0.7457 | 0.7027 | 1.33 | 2.36 | 131 |

## Key Observations

- **Top 2 designs have excellent RMSD < 0.6 A** — very high structural consistency
- **Dimer interface strategy has highest mean score** (0.587), followed by active site (0.525) and surface (0.513)
- **Active site designs came out 91-118 AA** despite config targeting 40-80 AA — BoltzGen may be ignoring the length constraints
- **All min interaction PAE values are 2.0-3.0** for top designs, indicating confident binding predictions
- **Wide score variance** within each strategy (best ~0.82, worst ~0.13) — many designs scoring poorly
- Only BoltzGen self-consistency metrics available (no Boltz-2 cross-validation or PyRosetta yet)

## Evaluation Status

- [x] BoltzGen self-consistency (refolding RMSD, ipTM, min iPAE)
- [ ] PyRosetta interface scoring (dG, SC, dSASA)
- [ ] Boltz-2 cross-validation

## Recommendations for Round 2

1. **Submit PyRosetta + Boltz-2 eval** for round 1 before designing more
2. **Investigate active site binder length** — configs specify 40-80 AA but designs come out 91-118 AA
3. **Scale up dimer interface** — best mean performance
4. **Try RFdiffusion3** for diversity — all R1 designs are BoltzGen only
5. **Consider tighter hotspot constraints** for active site to force smaller binders
