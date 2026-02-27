# Task 4: Port First Entrypoint — ESM2 Predict Masked

## Status: Pending

## Objective
Port `modal_esm2_predict_masked.py` to Lyceum as the first proof-of-concept entrypoint.

## Why ESM2 First
- Simplest entrypoint (138 lines)
- Optional GPU (can test on CPU first)
- Pip-only dependencies (no conda, no compiled binaries)
- Clear input/output format (FASTA → TSV + optional PNGs)
- Python execution mode (no Docker needed)

## Source
`resources/biomodals/modal_esm2_predict_masked.py`

## Files to Create

### `src/lyceum_esm2.py`

Extract business logic from the `@app.function()` body:

```
Input: FASTA file at /lyceum/storage/input/<name>.faa
  Format: >label\nMA<mask>GMT\n (use <mask> token for positions to predict)

Output: Written to /lyceum/storage/output/<run_name>/
  - predictions.tsv (columns: seq_n, label, aa, prob)
  - Optional: contact map PNGs

Arguments (via argparse):
  --input: path to input FASTA (default: /lyceum/storage/input/input.faa)
  --output-dir: output directory (default: /lyceum/storage/output/esm2/)
  --make-figures: generate contact map PNGs (default: false)
```

**Business logic to preserve:**
1. Parse FASTA, split by `>`
2. Load ESM2-650M model (`esm.pretrained.esm2_t33_650M_UR50D()`)
3. Batch convert sequences, run model with `repr_layers=[33]`
4. For each sequence: find `<mask>` token, extract logits, apply softmax
5. Get top-5 predictions + all amino acids sorted by probability
6. Write results to TSV (columns: seq_n, label, aa, prob)
7. Optionally generate contact map PNGs

**Model download:** ESM2 weights auto-download on first use from torch hub. For Lyceum, they'll download to `/lyceum/storage/models/esm2/` (or torch hub cache) on first run. Consider a setup script or first-run caching.

### `src/requirements/esm2.txt`
```
torch==1.13.1
fair-esm
pandas
matplotlib
```

## Testing

### Interactive test
```bash
# Upload test input
lyceum storage upload test_input.faa --key input/test.faa

# Run on Lyceum
lyceum python run src/lyceum_esm2.py \
  -r src/requirements/esm2.txt \
  -m gpu \
  -- --input /lyceum/storage/input/test.faa --output-dir /lyceum/storage/output/esm2_test/

# Download results
lyceum storage download output/esm2_test/predictions.tsv ./output/
```

### Via client.py
```python
from utils.client import LyceumClient
client = LyceumClient()
client.run(
    script_path="src/lyceum_esm2.py",
    requirements="src/requirements/esm2.txt",
    input_files={"test_input.faa": "input/test.faa"},
    output_prefix="output/esm2_test/",
    machine="gpu"
)
```

### Verification
- Compare TSV output with biomodals output for same input
- Same amino acid predictions, same probabilities (within floating point tolerance)

## Acceptance Criteria
- [ ] `lyceum_esm2.py` runs successfully on Lyceum with GPU
- [ ] Predictions match biomodals output for identical input
- [ ] Works via both direct `lyceum python run` and `client.py`
- [ ] Model weights download and cache correctly

## Dependencies on Other Tasks
- Task 2 (Lyceum access verified)
- Task 3 (client.py written — needed for client.py test, but not for direct CLI test)
