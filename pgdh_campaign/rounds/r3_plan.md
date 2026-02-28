# Round 3 Plan

**Date**: 2026-02-28
**Previous round**: R2 (51 designs total, 43 BoltzGen + 8 RFD3)

## Rationale

R2 results showed:
- **Dimer interface is the dominant strategy** — top 4 designs are all R2 dimer (scores 0.78–0.81)
- **90–115 AA is the sweet spot** for dimer binders
- **RFD3 is very efficient** (~2 min for 8 designs on A100) but R2 RFD3 designs lack evaluation metrics
- **Active site 40–80 AA was too aggressive** — only 3 designs produced, best active site designs from R1 were 90–110 AA

## Jobs (3 total)

### Job 1: RFD3 Scaled Dimer (de novo)
- **Config**: `rfd3_r3_dimer_scaled.json`
- **Profile**: `pgdh_dimer_r3`
- **Contig**: `80-120,/0,A0-265` (narrowed from R2's 60-140 to match BoltzGen sweet spot)
- **Hotspots**: A161:CZ, A150:CG1, A206:OH, A167:CD2, A172:SD
- **Batch**: `diffusion_batch_size=16, n_batches=2` → **32 designs**
- **Parameters**: `num_timesteps=200, step_scale=3.0, gamma_0=0.2`
- **Output**: `output/rfdiffusion3/r3/`
- **Execution ID**: `2e246495-0885-4beb-8c15-1b588edf7ed1`

### Job 2: BoltzGen Dimer (20 designs)
- **Config**: `strategy2_dimer_interface.yaml` (unchanged from R2)
- **Sequence range**: 90–120 AA
- **Hotspots**: 148,155,163,169,170,173,174,208
- **Designs**: 20 (doubled from R2's 10)
- **Output**: `output/boltzgen/r3/s2_dimer/`
- **Note**: Fixed `s3_output_subdir` to write to round-specific path (bug fix from R2)

### Job 3: RFD3 Partial Diffusion (refine top designs)
- **Config**: `rfd3_r3_partial_diffusion.json`
- **Templates**: Top 2 BoltzGen R2 dimer designs (#1 and #3)
  - `boltzgen_r2_s2_config_r2_s2_0` (102 AA, score 0.808)
  - `boltzgen_r2_s2_config_r2_s2_8` (105 AA, score 0.794)
- **Profiles** (4 total):
  - `partial_diff_r2s2_0_t8` — design #1, partial_t=8 (moderate noise)
  - `partial_diff_r2s2_0_t12` — design #1, partial_t=12 (more exploration)
  - `partial_diff_r2s2_8_t8` — design #3, partial_t=8
  - `partial_diff_r2s2_8_t12` — design #3, partial_t=12
- **Batch**: `diffusion_batch_size=4, n_batches=1` → **16 designs** (4 per profile)
- **Fixed atoms**: Target chain B (0-265)
- **Unfixed**: Entire binder chain A (sequence redesigned)
- **Output**: `output/rfdiffusion3/r3/`
- **Execution ID**: `51ab58db-749f-45a1-bc13-1302f738ab3a`

## Expected Output

- ~68 new designs (32 RFD3 de novo + 20 BoltzGen + 16 RFD3 partial diffusion)
- Total campaign: ~119 designs

## Fixes Applied in R3

1. **BoltzGen YAML path bug**: Changed `../structures/2GDZ.cif` → `2GDZ.cif` in all strategy YAMLs (was causing FileNotFoundError in Docker container)
2. **BoltzGen S3 output path**: Added `s3_output_subdir` parameter to `run_boltzgen()` so outputs go to round-specific dirs instead of root
3. **RFD3 partial diffusion indexing**: Fixed to 0-based residue indexing (A0-101 not A1-102)

## Post-Job Steps

1. Download results immediately
2. Run `sync_designs.py` to collect new designs
3. Run `evaluate_designs.py --fast` for BoltzGen refolding of RFD3 designs
4. Run `evaluate_designs.py --slow --auto` for Boltz-2 cross-validation of top designs
5. Fix RFD3 refolding metric parsing (RMSD computation from designed vs refolded CIFs)
6. Write R2 summary (`r2_summary.md`) and R3 summary
7. Regenerate pages and push
