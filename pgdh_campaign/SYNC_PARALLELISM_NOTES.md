# Sync Pipeline Parallelism Notes

## Current Bottleneck

`sync_designs.py` makes ~50-100 serial S3 API calls per run via `LyceumClient`,
which routes through the Lyceum HTTP API (`/api/v2/external/storage/credentials`
→ boto3). Each call has ~0.5-2s latency. Total runtime: 30-90s depending on
design count.

## Hotspots (most S3 calls)

1. **`collect_designs()`** — lists + downloads CSVs for each tool prefix.
   BoltzGen alone hits ~10 CSV files across subdirs.
2. **CIF lookup** — per-design `list_files()` to find matching CIF in
   `final_ranked_designs/` subdirs. N designs × M subdirs = many calls.
3. **`attach_*` functions** — each scans an `output/` prefix, downloads
   JSONs/CSVs/NPZs one at a time.
4. **`write_designs_to_s3()`** — uploads metrics.json per design, checks
   for existing CIFs before copying.

## Proposed Approach: Direct boto3 S3

`LyceumClient._ensure_s3()` already creates a boto3 client internally.
Extract that and use it directly for bulk reads:

```python
# In sync_designs.py, get boto3 client once:
client._ensure_s3()
s3 = client._s3_client
bucket = client._s3_bucket

# Use boto3 list_objects_v2 with pagination for bulk listing:
paginator = s3.get_paginator('list_objects_v2')
all_files = []
for page in paginator.paginate(Bucket=bucket, Prefix='output/boltzgen/'):
    all_files.extend([obj['Key'] for obj in page.get('Contents', [])])

# Single list call replaces dozens of individual list_files() calls.
```

### Key optimisations (in priority order):

1. **Single prefix listing**: List `output/` once at start, cache the full
   file tree in memory. All subsequent "list_files" become dict lookups.
   This alone eliminates ~50% of API calls.

2. **Parallel downloads with ThreadPoolExecutor**: Download CSVs/NPZs in
   parallel (4-8 threads). boto3 is thread-safe for reads.
   ```python
   from concurrent.futures import ThreadPoolExecutor
   with ThreadPoolExecutor(max_workers=4) as pool:
       results = pool.map(lambda key: (key, s3.get_object(...)), keys)
   ```

3. **Batch uploads**: Collect all writes, upload in parallel at the end.

### What NOT to parallelise:

- Job submission (Lyceum API is rate-limited, 1 job at a time)
- The ranking/scoring logic (CPU-bound, already fast)
- Auth/credential refresh (single-threaded)

## Impact Estimate

| Phase | Current | After |
|-------|---------|-------|
| Collect (list + download CSVs) | ~20s | ~3s |
| CIF lookup | ~15s | ~0s (cached listing) |
| Attach scores | ~10s | ~2s |
| Write to S3 | ~10s | ~3s |
| **Total** | **~55s** | **~8s** |

## Implementation Plan

1. Add `_list_all(prefix)` method to LyceumClient that does a single
   paginated listing and caches the result
2. Add `_download_many(keys)` method using ThreadPoolExecutor(4)
3. Refactor `collect_designs()` to use cached listing for CIF lookups
4. Refactor `attach_*` functions to batch-download results
5. Keep `write_designs_to_s3()` serial for safety (writes are less frequent)
