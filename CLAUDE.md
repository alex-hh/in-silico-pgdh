Never write plans, notes, or scratchpad files to ~/.claude or any directory outside this project. If you need to write planning or progress files, use the .claude/ directory within this project root.

Document utils required for pgdh design by my user account on lyceum in pgdh_campaign/readme.md
Document generic biolyceum features in biolyceum/README.md

## Campaign Round
**Current round: 3** (scaled RFD3 dimer, BoltzGen dimer x20, partial diffusion of top designs)
- Warmup designs (pre-ipSAE baseline) are archived, not part of any round
- Output dir convention: `output/{tool}/r{N}/{strategy}/`
- Submission name convention: `-f "pgdh_{tool}_r{N}_{strategy}"`

## Lyceum Rules
- **Always use informative submission names** via `-f` flag for Lyceum jobs (e.g. `-f "pgdh_boltzgen_s1_active_site"`) so that `lyceum execution ls` shows what each job was.
- **Always download results immediately** after a Lyceum job completes — storage is not guaranteed to persist.
- Never run `lyceum storage rmdir` on output directories without downloading first.
- Use round + strategy output subdirs (e.g. `output/boltzgen/r1/s1_active_site/`) to avoid overwriting between runs.
- **NEVER delete design results** — not from S3 (`output/`, `designs/`), not from local (`pgdh_campaign/out/`), not from `tracker/state.json`. Results are irreplaceable and cost GPU time to regenerate. If results seem wrong, flag them with status "failed" rather than deleting.
- **The `designs/` directory on S3 is the source of truth and is READ-ONLY.** It must NEVER be edited directly — not by Claude Code, not by manual commands. The ONLY thing that writes to `designs/` is `pgdh_campaign/sync_designs.py`. All other code reads from it.

## S3 Data Architecture

Two S3 locations, with strict ownership rules:

### 1. `output/` — raw tool outputs (written by Lyceum GPU jobs)
Unprocessed files from design tools: CSVs, JSONs, CIFs in tool-native formats.
Not a source of truth — these are inputs to the sync pipeline.
Subdirs: `output/boltzgen/`, `output/rfdiffusion3/`, `output/boltz2/`, `output/ipsae/`, `output/refolding/`.

### 2. `designs/` — ALL designs, standardised (written ONLY by `sync_designs.py`)
The **single source of truth for all design data** — both evaluated and unevaluated.
- `designs/index.json` — master ranked index of every design
- `designs/<tool>/<id>/metrics.json` — full standardised metrics per design
- `designs/<tool>/<id>/designed.cif` — designer's predicted structure
- `designs/<tool>/<id>/refolded.cif` — refolded structure (if available)

**READ-ONLY.** Never written by Claude Code or any other code. The ONLY writer is `pgdh_campaign/sync_designs.py`.

### Commands
```bash
source .venv/bin/activate

# Collect + rank (no GPU, fast)
python pgdh_campaign/sync_designs.py

# Fast eval: BoltzGen refolding for all designs
python pgdh_campaign/evaluate_designs.py --fast

# Slow eval: Boltz-2 cross-validation (specific IDs or --auto)
python pgdh_campaign/evaluate_designs.py --slow --auto
```

After design jobs:   run `sync_designs.py`
After eval jobs:     run `sync_designs.py` again (picks up new results)

## Display: GitHub Pages
Syncs from `designs/` on S3, caches in `docs/data/`, generates HTML:

```
python pgdh_campaign/generate_pages.py  # sync from S3 + generate docs/index.html
git add docs/ && git commit && git push # publish
```

Use `--no-sync` to skip the S3 download and use cached local data.

## Modal Pipeline (alternative to Lyceum)

`pgdh_modal/` is a self-contained Modal-based evaluation pipeline. All data is local — no S3.

```bash
source .venv/bin/activate

python pgdh_modal/sync.py                    # Collect + rank (local, no GPU)
python pgdh_modal/evaluate.py --fast         # BoltzGen refolding via Modal
python pgdh_modal/evaluate.py --slow --auto  # Boltz-2 cross-validation via Modal
python pgdh_modal/evaluate.py --score        # ipSAE scoring via Modal
python pgdh_modal/sync.py                    # Re-sync after eval

# Generate pages from local Modal results
python pgdh_campaign/generate_pages.py --designs-dir pgdh_modal/out/designs/
```

- Source of truth: `pgdh_modal/out/designs/` (local, written ONLY by `pgdh_modal/sync.py`)
- Raw outputs: `pgdh_modal/out/{boltzgen,refolding,boltz2,ipsae}/`
- Modal scripts: `resources/biomodals/modal_boltz.py`, `modal_boltzgen.py`, `modal_ipsae.py`
- Same ranking formula as Lyceum pipeline

## Design Tool Integration Rules
- **Design tools can write outputs in any format to any location.** There are no constraints on how a design tool stores its raw outputs. The sync pipeline handles all standardisation.
- **`pgdh_campaign/sync_designs.py` is the ONLY writer to `designs/` on S3.** It collects raw outputs from wherever design tools put them, copies the designer's predicted structures, attaches any existing validation/scoring/refolding results, and writes everything to the standardised `designs/` source of truth.
- **`pgdh_campaign/evaluate_designs.py` submits GPU jobs** (refolding, validation, scoring). It calls `sync_designs.sync_all()` first, then submits jobs. After jobs complete, run `sync_designs.py` again to pick up results.
- **Any new design model skill MUST have a parser adapter** in `pgdh_campaign/sync_designs.py` (registered in `TOOL_ADAPTERS`) so the sync pipeline can find and process its outputs. Without these, designs will not appear in the rankings.
- **Always run `sync_designs.py`** after generating designs to update `designs/index.json`.