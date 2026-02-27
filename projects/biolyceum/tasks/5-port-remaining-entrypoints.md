# Task 5: Port Remaining Entrypoints

## Status: Pending

## Objective
Port all remaining biomodals entrypoints to Lyceum, one at a time, ordered from simple to complex.

## Porting Order

### Tier 1 — Simple (Python execution mode)

#### 5d. ligandmpnn
- **Source**: `modal_ligandmpnn.py` (220 lines)
- **GPU**: A10G (use `gpu` on Lyceum)
- **Challenge**: Needs LigandMPNN repo cloned + model weights downloaded. Docker execution mode.
- **Input**: PDB structure → Output: Designed sequences (FASTA), scores

#### 5e. boltz
- **Source**: `modal_boltz.py` (274 lines)
- **GPU**: L40S (use `gpu.a100` on Lyceum)
- **Challenge**: Uses Modal Volume for model caching → map to `/lyceum/storage/models/boltz/`. Needs conda (kalign2, hhsuite).
- **Docker approach**: Complex — needs JAX + ColabFold + conda packages
- **Input**: FASTA or YAML → Output: Structure predictions

### Tier 2 — Medium Complexity

#### 5g. proteinmpnn
- Same pattern as ligandmpnn (subset of parameters)

#### 5h. boltzgen

#### 5i. protenix
- **Source**: `modal_protenix.py` (279 lines)
- **GPU**: L40S
- **Challenge**: NVIDIA base image + protenix package

#### 5j. pdb2png
- **Source**: `modal_pdb2png.py` (396 lines)
- **GPU**: None
- **Challenge**: PyMOL installation (complex). Docker mode with pymol base image.

#### 5k. md_protein_ligand
- **Source**: `modal_md_protein_ligand.py` (224 lines)
- **GPU**: Optional (T4)
- **Challenge**: OpenMM + RDKit + gnina binary. Docker mode.

### Tier 3 — Complex (port last)

#### 5l. rfdiffusion
- **Source**: `modal_rfdiffusion.py` (627 lines)
- **GPU**: A10G
- **Challenge**: RFdiffusion repo + DGL + SE3-Transformer. Complex Docker build.

#### 5m. bindcraft
- **Source**: `modal_bindcraft.py` (1157 lines)
- **GPU**: L40S
- **Challenge**: Largest entrypoint. PyRosetta + ColabDesign + BindCraft. Very complex.


#### Tier 4 - Optional

#### 5a. minimap2
- **Source**: `modal_minimap2.py` (63 lines)
- **GPU**: None (CPU only)
- **Challenge**: Needs minimap2 binary (compiled from source). Use Docker execution mode with a base image that has build tools, or find a pre-built minimap2 wheel/binary.
- **Docker approach**: Base `python:3.11-slim` + `apt-get install minimap2` or build from source in setup commands.
- **Input**: Reference FASTA + reads FASTQ → Output: PAF alignment file

#### 5b. anarci
- **Source**: `modal_anarci.py` (128 lines)
- **GPU**: None
- **Challenge**: Needs HMMER (conda) + ANARCI (pip install from source). Docker execution mode recommended.
- **Docker approach**: Base `condaforge/miniforge3` + `conda install hmmer` + pip install ANARCI
- **Input**: FASTA → Output: CSV numbering files

#### 5c. chai


## Per-Entrypoint Checklist

For each entrypoint, repeat this pattern:
- [ ] Read the biomodals source (`modal_X.py`)
- [ ] Identify execution mode (Python vs Docker)
- [ ] Write `src/lyceum_X.py` with business logic + argparse
- [ ] Write `src/requirements/X.txt` (or document Docker setup)
- [ ] Test with sample input on Lyceum
- [ ] Verify outputs match biomodals
- [ ] Add to `client.py` if needed (Docker-mode tools may need specific submit logic)

## Acceptance Criteria
- [ ] All Tier 1 entrypoints ported and tested
- [ ] All Tier 2 entrypoints ported and tested
- [ ] Tier 3 entrypoints ported as needed (some may be deferred)

## Dependencies on Other Tasks
- Task 3 (client.py needed for orchestration)
- Task 4 (ESM2 port validates the pattern)
