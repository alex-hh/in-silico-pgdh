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

## Design Tool Integration Rules
- **Design tools can write outputs in any format to any location.** There are no constraints on how a design tool stores its raw outputs. The evaluation pipeline handles all standardisation.
- **The evaluation pipeline (`pgdh_campaign/evaluate_designs.py`) is the ONLY writer to `designs/` on S3.** It collects raw outputs from wherever design tools put them, copies the designer's predicted structures, runs Boltz-2 refolding for designability metrics (RMSD between designed and refolded structure), scores with ipSAE, and writes everything to the standardised `designs/` source of truth.
- **Any new design model skill MUST have a parser adapter** in `pgdh_campaign/evaluate_designs.py` (registered in `TOOL_ADAPTERS`) so the evaluation pipeline can find and process its outputs. Also add a Streamlit form in `dashboard/app.py` for team access. Without these, designs will not appear in the dashboard or be ranked.
- **Two ways to generate designs**: (1) Fixed pipelines via the Streamlit web app at `dashboard/app.py` (for team members), (2) Claude Code skills for developers with a local repo clone.
- **Always run the evaluation pipeline** after generating designs to keep `designs/index.json` and `tracker/state.json` in sync.