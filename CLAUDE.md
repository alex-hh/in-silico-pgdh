Never write plans, notes, or scratchpad files to ~/.claude or any directory outside this project. If you need to write planning or progress files, use the .claude/ directory within this project root.

Document utils required for pgdh design by my user account on lyceum in pgdh_campaign/readme.md
Document generic biolyceum features in biolyceum/README.md

## Lyceum Rules
- **Always use informative submission names** via `-f` flag for Lyceum jobs (e.g. `-f "pgdh_boltzgen_s1_active_site"`) so that `lyceum execution ls` shows what each job was.
- **Always download results immediately** after a Lyceum job completes — storage is not guaranteed to persist.
- Never run `lyceum storage rmdir` on output directories without downloading first.
- Use strategy-specific output subdirs (e.g. `output/boltzgen/s1_active_site/`) to avoid overwriting between runs.
- **NEVER delete design results** — not from S3 (`output/`, `designs/`), not from local (`pgdh_campaign/out/`), not from `tracker/state.json`. Results are irreplaceable and cost GPU time to regenerate. If results seem wrong, flag them with status "failed" rather than deleting.
- **The `designs/` directory on S3 is the source of truth and is READ-ONLY.** It must NEVER be edited directly — not by Claude Code, not by manual commands, not by the dashboard. The ONLY thing that writes to `designs/` is `pgdh_campaign/evaluate_designs.py` (invoked by the pgdh-design skill). All other code reads from it.

## S3 Source of Truth
- **`designs/`** on Lyceum S3 is the canonical location for all evaluated design data. Contains `index.json` (master ranked index) and `<tool>/<design_id>/` dirs with `metrics.json` + CIF structures.
- **`output/`** on Lyceum S3 contains raw tool outputs (boltzgen/, rfdiffusion3/, boltz2/, ipsae/). These are inputs to the evaluation pipeline, not the source of truth.
- **`tracker/state.json`** on Lyceum S3 stores campaign metadata (job log, notes, status overrides).

## Two Display Paths
Both read from the same `designs/` source of truth on S3:

1. **GitHub Pages** (`docs/`): Static HTML, loads instantly, no S3 calls at page load. Must be manually synced:
   ```
   python pgdh_campaign/sync_to_pages.py   # S3 → docs/data/
   python pgdh_campaign/generate_pages.py  # docs/data/ → docs/index.html
   git add docs/ && git commit && git push # publish
   ```
2. **Streamlit Dashboard** (`dashboard/app.py`): Reads S3 live on each visit. Slower on cold start but always current. Read-only pages are public; write actions need Google OAuth.

After running `evaluate_designs.py`, **both** viewers need updating: Streamlit sees changes on next page load (or after cache TTL), but GitHub Pages requires running `sync_to_pages.py` + `generate_pages.py` + push.

## Design Tool Integration Rules
- **Design tools can write outputs in any format to any location.** There are no constraints on how a design tool stores its raw outputs. The evaluation pipeline handles all standardisation.
- **The evaluation pipeline (`pgdh_campaign/evaluate_designs.py`) is the ONLY writer to `designs/` on S3.** It collects raw outputs from wherever design tools put them, copies the designer's predicted structures, runs Boltz-2 refolding for designability metrics (RMSD between designed and refolded structure), scores with ipSAE, and writes everything to the standardised `designs/` source of truth.
- **Any new design model skill MUST have a parser adapter** in `pgdh_campaign/evaluate_designs.py` (registered in `TOOL_ADAPTERS`) so the evaluation pipeline can find and process its outputs. Also add a Streamlit form in `dashboard/app.py` for team access. Without these, designs will not appear in the dashboard or be ranked.
- **Two ways to generate designs**: (1) Fixed pipelines via the Streamlit web app at `dashboard/app.py` (for team members), (2) Claude Code skills for developers with a local repo clone.
- **Always run the evaluation pipeline** after generating designs to keep `designs/index.json` and `tracker/state.json` in sync.