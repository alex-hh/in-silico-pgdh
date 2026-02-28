---
name: pgdh_ipsae
description: >
  Score 15-PGDH binder designs using ipSAE on Lyceum.
  Use this skill when: (1) Ranking PGDH binder candidates after BoltzGen/BindCraft,
  (2) Filtering designs before Boltz-2 cross-validation,
  (3) Scoring AF2/AF3/Boltz predictions against 15-PGDH (2GDZ).
license: MIT
category: evaluation
tags: [pgdh, ranking, scoring, binding, lyceum]
lyceum_script: lyceum_ipsae.py
---

# PGDH ipSAE Scoring

Score 15-PGDH (PDB: 2GDZ) binder designs using ipSAE on Lyceum.

## Target

- **Protein**: 15-PGDH (15-hydroxyprostaglandin dehydrogenase)
- **PDB**: 2GDZ (1.65 A, homodimer, NAD+ bound)
- **UniProt**: P15428
- **Chain A**: 266 residues
- **Active site**: Ser138, Tyr151, Lys155, Gln148, Phe185, Tyr217
- **Dimer interface**: Phe161, Leu150, Ala153, Ala146, Leu167, Ala168

## Prerequisites

ipSAE scores the **predicted confidence** of a protein-protein interaction. It needs
outputs from a structure predictor (Boltz/AF2/AF3) that predicted your binder + PGDH
as a complex. You cannot use the raw 2GDZ crystal structure directly.

Required inputs (both from the same prediction run):

- **PAE file** — predicted aligned error matrix. JSON for AF2/AF3, NPZ for Boltz1.
- **Structure file** — predicted 2-chain complex (target + binder). PDB for AF2, CIF for AF3/Boltz1.

Typical workflow:
```
Design binder → Predict complex (Boltz-2/AF2) → Score with ipSAE
```

## How to run

### Single design

```bash
python projects/biolyceum/src/lyceum_ipsae.py \
    --pae-file pgdh_campaign/out/boltz2/candidate_1/pae.json \
    --structure-file pgdh_campaign/out/boltz2/candidate_1/model_0.cif \
    --pae-cutoff 10 --dist-cutoff 10 \
    --output-dir pgdh_campaign/out/ipsae/candidate_1
```

### On Lyceum

```bash
lyceum python run lyceum_ipsae.py -r requirements/ipsae.txt -m cpu \
    -- --pae-file /job/work/input/pae.json \
       --structure-file /job/work/input/model.cif \
       --pae-cutoff 10 --dist-cutoff 10
```

### Batch scoring all candidates

```python
from pathlib import Path
from projects.biolyceum.src.lyceum_ipsae import compute_ipsae

boltz2_dir = Path("pgdh_campaign/out/boltz2")
for candidate_dir in sorted(boltz2_dir.iterdir()):
    if not candidate_dir.is_dir():
        continue

    # Find PAE + structure files
    pae_files = list(candidate_dir.glob("*.json")) + list(candidate_dir.glob("*.npz"))
    struct_files = list(candidate_dir.glob("*.cif")) + list(candidate_dir.glob("*.pdb"))
    if not pae_files or not struct_files:
        continue

    out_dir = Path("pgdh_campaign/out/ipsae") / candidate_dir.name
    compute_ipsae(
        pdb_path=str(struct_files[0]),
        pae_file_path=str(pae_files[0]),
        pae_cutoff=10.0,
        dist_cutoff=10.0,
        output_dir=str(out_dir),
    )
```

### Parse results for ranking

```python
import csv
from pathlib import Path

results = []
for txt_file in Path("pgdh_campaign/out/ipsae").glob("*/*_10_10.txt"):
    candidate = txt_file.parent.name
    with open(txt_file) as f:
        for line in f:
            if ",max," in line:
                fields = line.strip().split(",")
                results.append({
                    "candidate": candidate,
                    "ipSAE": float(fields[5]),
                    "ipSAE_d0chn": float(fields[6]),
                    "ipSAE_d0dom": float(fields[7]),
                    "ipTM_af": float(fields[8]),
                    "pDockQ": float(fields[10]),
                    "pDockQ2": float(fields[11]),
                    "LIS": float(fields[12]),
                })

# Rank by ipSAE
results.sort(key=lambda r: r["ipSAE"], reverse=True)
for r in results[:10]:
    print(f"{r['candidate']:20s}  ipSAE={r['ipSAE']:.4f}  pDockQ={r['pDockQ']:.4f}  LIS={r['LIS']:.4f}")
```

## PGDH-specific thresholds

| Metric | Pass | Strong | Notes |
|--------|------|--------|-------|
| ipSAE | > 0.61 | > 0.70 | Primary ranking metric |
| ipSAE_d0dom | > 0.10 | > 0.20 | Domain-normalized, more stable |
| pDockQ | > 0.50 | > 0.60 | Contact-based confidence |
| LIS | > 0.35 | > 0.45 | Interface quality |
| nres1 + nres2 | > 20 | > 40 | Interface size (both chains) |

## Campaign integration

This scoring step fits into the PGDH campaign (see `pgdh_campaign/CAMPAIGN_PLAN.md`):

```
Step 2: BoltzGen → 50 designs per strategy (150 total)
Step 3: protein-qc filter → ~30 candidates
Step 4: Boltz-2 cross-validation → structures + PAE
Step 5: >>> pgdh_ipsae scoring <<< → rank by ipSAE
        Select top 10 for submission
```

## Output files

| File | Contents |
|------|----------|
| `*_10_10.txt` | Chain-pair summary: ipSAE, pDockQ, pDockQ2, LIS per chain pair |
| `*_10_10_byres.txt` | Per-residue ipSAE — identify which residues drive binding |
| `*_10_10.pml` | PyMOL script — visualize interface colored by chain |

## Files

- Script: `projects/biolyceum/src/lyceum_ipsae.py`
- Requirements: `projects/biolyceum/src/requirements/ipsae.txt`
- Target structure: `pgdh_campaign/structures/2GDZ.pdb`
- Campaign plan: `pgdh_campaign/CAMPAIGN_PLAN.md`
- Machine: CPU (no GPU needed)
