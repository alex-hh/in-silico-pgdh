# PGDH Design Tracker — Streamlit Dashboard

Web interface for tracking the 15-PGDH binder design campaign. Read-only access is public; write actions (job submission, status updates) require Google OAuth sign-in.

**Live app**: deployed on Streamlit Community Cloud (check the team for the URL).

## Pages

### Dashboard

Campaign overview. Shows:

- **Summary cards** — total designs, count by tool (BoltzGen / RFdiffusion3), unevaluated count, selected count
- **Evaluation pipeline funnel** — raw → collected → validated → scored → selected
- **Designs by strategy** — bar chart of active_site / dimer_interface / surface coverage
- **Recent jobs** — last 10 Lyceum jobs with status
- **Sync Raw Designs from S3** — imports designs from `output/boltzgen/` and `output/rfdiffusion3/` into the tracker (parses CSVs/JSONs, no GPU)
- **Run Evaluation Pipeline** — runs `evaluate_designs.py` end-to-end: collect → standardise → rank → write to `designs/` source of truth (auth required)
- **Submit Refolding Jobs** — sends BoltzGen folding jobs for designs that haven't been refolded yet (auth required)
- **Suggested Next Steps** — coverage matrix (tool x strategy) and bottleneck detection

### Designs

Filterable table of all tracked designs with three tabs:

- **All Designs** — every design in the tracker
- **Raw (unevaluated)** — designs with only designer-native metrics
- **Evaluated** — designs that have been through the evaluation pipeline

Each row shows: design ID, tool, strategy, evaluation stage, status, rank, composite score, residue count, ipTM, pTM, RMSD, PAE, secondary structure fractions.

Filters by tool, strategy, and status. Bulk actions (auth required): mark as validated / scored / selected.

### Jobs

Lyceum job tracker (auth required). Shows all submitted jobs with status badges. Actions:

- **Refresh Running Jobs** — polls Lyceum API for status updates on pending/running jobs
- **Auto-evaluate on completion** — when a job finishes, automatically runs the evaluation pipeline

### New Run

Submit new Lyceum GPU jobs (auth required). Tool options:

| Tool | What it does | GPU | Key parameters |
|------|-------------|-----|----------------|
| **BoltzGen** | Generate binder candidates | A100 | Strategy (S1/S2/S3), num_designs, protocol |
| **RFdiffusion3** | Backbone diffusion design | A100 | Strategy, num_designs, contig string |
| **Boltz-2 Validation** | Refold designed complexes | A100 | Select designs from table |
| **ipSAE Scoring** | Rank by binding confidence | CPU | Select designs from table |
| **Custom FASTA Upload** | Import external sequences | — | Upload .fasta file, runs evaluation |

### Design Detail

Deep dive into a single design. Shows:

- **Header** — tool, strategy, status, evaluation stage, residue count
- **Metrics** — all available metrics with colour coding (green = good, orange = warn, red = bad)
- **Sequence** — full amino acid sequence (if available; RFD3 designs are backbone-only)
- **3D Structure** — interactive 3Dmol.js viewer (cartoon, spectrum coloured) loaded from `designs/<tool>/<id>/designed.cif` on S3
- **Notes** — free-text notes field, saved to `tracker/state.json` (auth required)
- **Status update** — change design status (auth required)

## Metric colour thresholds

| Metric | Good | Warn | Bad | Direction |
|--------|------|------|-----|-----------|
| ipTM | >= 0.7 | >= 0.5 | < 0.5 | higher is better |
| pTM | >= 0.8 | >= 0.7 | < 0.7 | higher is better |
| RMSD | <= 2.0 | <= 2.5 | > 2.5 | lower is better |
| PAE | <= 3.0 | <= 5.0 | > 5.0 | lower is better |
| max CA deviation | <= 0.5 | <= 1.0 | > 1.0 | lower is better |
| chain breaks | 0 | 0 | > 0 | lower is better |

## Data flow

```
Lyceum S3                          Streamlit App
┌───────────────────────┐
│ tracker/state.json    │──────►  get_tracker()
│                       │         Disk-cached (5 min TTL)
│ designs/<tool>/<id>/  │──────►  _load_structure()
│   designed.cif        │         Disk-cached (1 hour TTL)
│   metrics.json        │
│                       │
│ output/boltzgen/      │──────►  Sync Raw Designs button
│ output/rfdiffusion3/  │         (parses CSVs/JSONs into tracker)
└───────────────────────┘
```

All read-only data is cached to disk with `persist="disk"`, so the app serves the last-known-good state instantly on cold start (Streamlit Cloud sleeps after ~15 min idle). Fresh data is fetched from S3 when the TTL expires.

The "Refresh from S3" sidebar button clears all caches and forces a fresh fetch.

## Authentication

- **Read-only** (Dashboard, Designs, Design Detail) — public, no sign-in needed
- **Write actions** (New Run, Jobs, bulk actions, notes, evaluation) — Google OAuth with email allowlist

Auth is configured in Streamlit secrets:

```toml
[google]
client_id = "..."       # From Google Cloud Console OAuth 2.0 credentials
client_secret = "..."

[auth]
cookie_key = "..."      # Random string for session cookie signing
redirect_uri = "..."    # Your Streamlit app URL
allowed_emails = "a@example.com, b@example.com"
```

## Local development

```bash
source .venv/bin/activate
cd dashboard

# Copy secrets template and fill in values
cp .streamlit/secrets.toml.template .streamlit/secrets.toml
# Edit .streamlit/secrets.toml with your Lyceum + Google credentials

streamlit run app.py
```

The app needs Lyceum credentials to load data. Without them, read-only pages show an empty state.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Main Streamlit app (5 pages, ~1050 lines) |
| `client.py` | Lyceum API client (auth, S3, job submission, token refresh) |
| `tracker.py` | S3-backed campaign state (`tracker/state.json`) |
| `loaders.py` | Parse BoltzGen CSVs and RFD3 JSONs, metric thresholds |
| `requirements.txt` | Python dependencies |
| `.streamlit/secrets.toml.template` | Secrets template (not committed) |
