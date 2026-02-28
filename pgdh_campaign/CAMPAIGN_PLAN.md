# 15-PGDH Binder Design Campaign — Berlin Bio Hackathon

## Context

Design protein binders targeting 15-PGDH (PDB: 2GDZ, UniProt: P15428) for the Berlin Bio Hackathon x Adaptyv competition. Submissions: up to 10 designs (max 250 AA), judged on **novelty/originality** then **binding affinity (KD)**. Deadline: Feb 27-28.

The workflow is **agentic**: Claude Code orchestrates the campaign by invoking skills (`/boltzgen`, `/boltz`, `/protein-qc`, etc.) and running Lyceum commands step-by-step. No standalone orchestrator script — Claude Code _is_ the orchestrator.

**Platform**: Lyceum (via biolyceum client at `projects/biolyceum/src/utils/client.py`)

**API Stability (Feb 2026)**: Lyceum API has high latency. Schedule at most 1 job at a time.
Client timeouts increased to 120s. Standalone A100 server scripts in `server/` available as fallback.

## Target: 15-PGDH

- 1.65 Å crystal structure, homodimer, NAD+ bound
- Active site: Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217
- Dimer interface: Phe161, **Val150** (not Leu), Ala153, Ala146, Leu167, Ala168, Tyr206, Leu171, Met172

## Campaign Strategy: 3 Binding Approaches

| # | Strategy | Hotspots | Binder Size | Novelty |
|---|----------|----------|-------------|---------|
| 1 | Active site blocker | Catalytic + substrate residues | 80-120 AA | Medium |
| 2 | Dimer disruptor | Interface residues | 80-140 AA | High |
| 3 | Surface (model-free) | None — BoltzGen auto-detects | 60-140 AA | High |

## Step-by-Step Agentic Workflow

### Step 0: Setup & Target Prep
**Skills**: `/setup`, `/pdb`

1. Activate venv: `source .venv/bin/activate`
2. Verify Lyceum auth: `lyceum auth status` or check `~/.lyceum/config.json`
3. Confirm biolyceum scripts exist at `projects/biolyceum/src/`
4. Download 2GDZ structure:
   ```bash
   wget -O pgdh_campaign/structures/2GDZ.cif https://files.rcsb.org/download/2GDZ.cif
   ```
5. Inspect CIF to verify chain IDs and `label_seq_id` numbering for hotspot residues
6. Create campaign directory structure:
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

### Step 2: Run BoltzGen (3 parallel Lyceum jobs)
**Skills**: `/boltzgen`

Launch all 3 strategies in parallel. Two options:

**Option A — Via Modal (existing, tested)**:
```bash
source .venv/bin/activate && cd pgdh_campaign

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

**Option B — Via Lyceum (biolyceum)**:
Uses `projects/biolyceum/src/lyceum_boltzgen.py` + `run_boltzgen.sh` Docker execution.
Upload YAML + CIF to Lyceum storage, submit Docker job with BoltzGen image, stream output, download results.

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
3. Run Boltz-2 prediction (Modal for now — Lyceum port pending):
   ```bash
   modal run resources/biomodals/modal_boltz.py \
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

## PGDH MSA Cache

The AlphaFast MSA server enriches input JSONs with pre-computed MSAs (multiple sequence alignments).
Since the PGDH target sequence never changes, we **cache the enriched MSA data** to avoid redundant
server calls during cross-validation.

- **Cache location**: `pgdh_campaign/msa_cache/`
- **Target MSA**: `pgdh_campaign/msa_cache/pgdh_target_msa.json` — enriched AF3 input for the full PGDH monomer
- **Homodimer MSA**: `pgdh_campaign/msa_cache/pgdh_homodimer_msa.json` — enriched AF3 input for the PGDH homodimer

**How to use cached MSA for binder validation**:
1. Create AF3 input JSON with target + binder sequences
2. Call `lyceum_alphafast.py --input complex.json --skip-msa` if MSA is pre-embedded
3. Or merge cached target MSA data into the complex input JSON before running inference

**MSA server**: `https://romero-lab--alphafold3-msa-server-msa.modal.run` (public, free, GPU-accelerated)

**Note**: AF3 model weights are required for inference. They must be obtained from Google DeepMind
(approval takes 2-5 business days) and uploaded to Lyceum storage at `models/alphafast/weights/af3.bin`.

## RFdiffusion3 (Alternative/Complementary to BoltzGen)

**Skill**: `/pgdh_rfdiffusion3`

RFdiffusion3 is the latest Baker lab tool — designs at the atomic level (full sidechains, not just backbone). Uses the `rosettacommons/foundry` Docker image on Lyceum.

### Key differences from BoltzGen
- JSON config (not YAML)
- Comma-separated contigs (not space-separated)
- Hotspots specify exact atom names (must match PDB)
- Output: `.cif.gz` + `.json` per design (not a ranked pipeline)
- No built-in inverse folding or refolding — needs separate validation

### How to run

```bash
source .venv/bin/activate && cd pgdh_campaign
python run_rfd3_pgdh.py
```

Config: `pgdh_campaign/configs/rfd3_pgdh_binder.json` (2 strategies: active site + dimer interface)

### Tested parameters (PGDH, A100)
- `--num-designs 4 --step-scale 3 --gamma-0 0.2` — quick test, ~16 min total
- Docker image: `rosettacommons/foundry`, machine: `gpu.a100`

### Output
- `pgdh_campaign/out/rfd3/*.cif.gz` — designed structures
- `pgdh_campaign/out/rfd3/*.json` — metrics (clashes, SS fractions, RoG, etc.)

### Known issues
- Res 150 is **Val** (CG1/CG2), not Leu (CD1/CD2) — hotspot atoms must match PDB exactly
- Container pull takes ~13 min on first run (cached after)
- RFD3 outputs need sequence extraction + cross-validation (no built-in QC pipeline like BoltzGen)

## Files to Create

| File | Purpose |
|------|---------|
| `pgdh_campaign/structures/2GDZ.cif` | Downloaded target structure |
| `pgdh_campaign/configs/strategy1_active_site.yaml` | BoltzGen config — active site |
| `pgdh_campaign/configs/strategy2_dimer_interface.yaml` | BoltzGen config — dimer interface |
| `pgdh_campaign/configs/strategy3_surface.yaml` | BoltzGen config — free surface |
| `pgdh_campaign/msa_cache/pgdh_target_msa.json` | Cached MSA for PGDH monomer |
| `pgdh_campaign/msa_cache/pgdh_homodimer_msa.json` | Cached MSA for PGDH homodimer |
| `pgdh_campaign/configs/rfd3_pgdh_binder.json` | RFdiffusion3 config — active site + dimer |
| `pgdh_campaign/out/final/submission.fasta` | Final 10 sequences for submission |

## Existing Files

| File | Used in |
|------|---------|
| `projects/biolyceum/src/lyceum_alphafast.py` | AlphaFast inference (Lyceum) |
| `projects/biolyceum/src/run_alphafast.sh` | Docker setup for AlphaFast on Lyceum |
| `projects/biolyceum/src/lyceum_boltzgen.py` | Step 2 — design generation (Lyceum) |
| `projects/biolyceum/src/lyceum_rfdiffusion3.py` | RFD3 Docker entrypoint (Lyceum) |
| `projects/biolyceum/src/utils/client.py` | Lyceum API client |
| `projects/biolyceum/src/run_boltzgen.sh` | Docker setup for BoltzGen on Lyceum |
| `pgdh_campaign/run_rfd3_pgdh.py` | RFD3 submission script |

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
