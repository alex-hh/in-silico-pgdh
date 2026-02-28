# /design-round — Complete Round Lifecycle

Run a full design round end-to-end: design, sync, evaluate, publish.

## Instructions

Read the current round number N from CLAUDE.md (`Current round: N`).

Execute these phases in order. Wait for each phase to complete before starting the next.

### Phase 1: DESIGN

Submit design jobs to Lyceum using the appropriate tool skills (boltzgen-pgdh, pgdh_rfdiffusion3).

- Output dir: `output/{tool}/r{N}/{strategy}/`
- Submission name: `-f "pgdh_{tool}_r{N}_{strategy}"`
- Download results immediately after each job completes (storage not guaranteed)

Choose strategies based on the campaign plan in `pgdh_campaign/CAMPAIGN_PLAN.md` and results from previous rounds.

### Phase 2: SYNC

```bash
source .venv/bin/activate
python pgdh_campaign/sync_designs.py
```

Verify output shows `by_round` containing round N with the expected design count.

### Phase 3: CPU EVAL (fast, no GPU wait)

```bash
python pgdh_campaign/evaluate_designs.py --fast --interface --round N
```

This does two things:
- `--fast`: Promotes BoltzGen self-consistency metrics (free, in-memory)
- `--interface`: Submits PyRosetta interface scoring (CPU job)
- `--round N`: Only targets designs from this round

Wait for the PyRosetta job to complete, then re-sync:

```bash
python pgdh_campaign/sync_designs.py
```

### Phase 4: GPU EVAL (optional, slower)

Only if CPU eval results look promising (designs with RMSD < 2.5A):

```bash
python pgdh_campaign/evaluate_designs.py --slow --auto --round N
```

Submits Boltz-2 cross-validation for qualifying designs. Wait for job, then re-sync:

```bash
python pgdh_campaign/sync_designs.py
```

### Phase 5: ROUND SUMMARY

Write a round summary document at `pgdh_campaign/rounds/r{N}_summary.md` with:

- **Round number and date**
- **Strategies used**: which tools, which binding strategies (active site, dimer interface, surface), key parameters
- **Design count**: how many designs per tool/strategy
- **Nature of designs**: binder lengths, target hotspots, any novel approaches tried
- **Results overview**: pass rates at each eval stage, best metrics (RMSD, ipTM, interface dG)
- **Key observations**: what worked, what didn't, surprises
- **Recommendations for next round**: what to change, scale up, or abandon

Create the `pgdh_campaign/rounds/` directory if it doesn't exist.

### Phase 6: PUBLISH

```bash
python pgdh_campaign/generate_pages.py
git add docs/ pgdh_campaign/rounds/ && git commit -m "Round N results" && git push
```

Verify the pages viewer shows:
- Round badge (green "R{N}")
- Round column in the table
- Round filter dropdown

### Phase 7: ADVANCE

Update CLAUDE.md: change `Current round: N` to `Current round: N+1`.

Review results and plan next round strategy adjustments based on the round summary.

## Checklist

- [ ] Design jobs submitted with correct output dirs and names
- [ ] Results downloaded immediately
- [ ] sync_designs.py shows round N designs in by_round
- [ ] CPU eval (--fast --interface --round N) runs only on round N
- [ ] PyRosetta results attached after re-sync
- [ ] Round summary written to pgdh_campaign/rounds/r{N}_summary.md
- [ ] Pages generated and pushed
- [ ] CLAUDE.md updated to next round
