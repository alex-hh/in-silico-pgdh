# Round 2 Plan

**Based on**: Round 1 analysis (30 BoltzGen designs)

## Key Takeaways from R1

1. **Dimer interface is the best strategy** — highest mean score, best pass rate
2. **Short binders (< 80 AA) fail consistently** across all strategies
3. **Active site config was never updated** — still says 80-120 AA, not 40-80 AA
4. **No RFdiffusion3 designs yet** — zero tool diversity
5. **No cross-validation** — don't know if BoltzGen self-consistency is inflated

## Proposed Jobs (3 max)

### Job 1: BoltzGen — Active Site (fixed config)

**Rationale**: Fix the length range to 40-80 AA as originally intended. R1 active site designs were 80-120 AA (old config). Need to test whether shorter binders can actually reach the active site pocket.

**Config change** (`strategy1_active_site.yaml`):
```yaml
entities:
  - protein:
      id: B
      sequence: 40..80   # was 80..120
```

**Parameters**: 10 designs, 600s timeout

**Expected**: Shorter, more focused binders targeting the catalytic pocket. May have higher failure rate (pocket is tight) but successful designs would be more druggable.

### Job 2: BoltzGen — Dimer Interface (narrowed range)

**Rationale**: Best-performing strategy. Narrow the range to sweet spot identified in R1. Failed designs were all < 80 AA or > 130 AA. Best designs were 95-111 AA.

**Config change** (`strategy2_dimer_interface.yaml`):
```yaml
entities:
  - protein:
      id: B
      sequence: 90..120   # was 80..140, narrowed to R1 sweet spot
```

**Parameters**: 10 designs, 600s timeout

**Expected**: Higher pass rate by eliminating the too-short and too-long failure modes. Should produce more designs in the 0.65-0.83 score range.

### Job 3: RFdiffusion3 — Active Site (first RFD3 round)

**Rationale**: Zero tool diversity so far. RFD3 takes a completely different approach (backbone diffusion + ProteinMPNN sequence design vs BoltzGen's all-atom generation). Even if metrics are lower, structural diversity is valuable.

**Config**: Use existing `rfd3_pgdh_binder.json` with `pgdh_active_site` profile:
- Contig: `60-120` (already set)
- Hotspots: Ser138, Gln148, Tyr151, Lys155, Phe185, Tyr217 (atom-level)
- `is_non_loopy: true` (helical binders)

**Parameters**: 4 designs (RFD3 generates 8 models per design = 32 structures), 600s timeout

**Expected**: Different binding modes, potentially better hotspot targeting (RFD3 uses atom-level hotspot specification vs BoltzGen's residue-level).

## Config Changes Summary

| File | Field | Old | New | Rationale |
|------|-------|-----|-----|-----------|
| `strategy1_active_site.yaml` | `sequence` | `80..120` | `40..80` | Test shorter active site binders |
| `strategy2_dimer_interface.yaml` | `sequence` | `80..140` | `90..120` | Narrow to R1 sweet spot |

## Pre-Round 2 Actions

Before submitting R2 designs, consider running evaluations on R1:
- `evaluate_designs.py --fast --interface --round 1` — PyRosetta CPU scoring
- `evaluate_designs.py --slow --auto --round 1` — Boltz-2 cross-validation for top designs

This would give us dG/shape complementarity data and independent structure validation before investing more compute in new designs.

## Expected Outcomes

| Job | Designs | Expected Pass Rate | Key Question |
|-----|---------|-------------------|--------------|
| BoltzGen S1 (40-80 AA) | 10 | 30-50% (risky) | Can short binders reach the active site? |
| BoltzGen S2 (90-120 AA) | 10 | 70-80% (safe) | Does narrowing the range improve consistency? |
| RFD3 Active Site | ~32 models | 10-20% (exploratory) | Does RFD3 find different binding modes? |
