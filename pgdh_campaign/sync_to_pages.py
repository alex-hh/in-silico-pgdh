#!/usr/bin/env python3
"""Sync design data from Lyceum S3 to docs/data/ for GitHub Pages.

Downloads designs/index.json, per-design metrics.json, and CIF structures
from the S3 source of truth, then writes them to docs/data/ in a format
the static viewer can consume.

Usage:
    source .venv/bin/activate
    python pgdh_campaign/sync_to_pages.py

After running, commit and push docs/ to update the GitHub Pages site.
"""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "projects" / "biolyceum" / "src" / "utils"))
from client import LyceumClient

BASE = Path(__file__).resolve().parent
DOCS_DATA = BASE.parent / "docs" / "data"


def sync(client: LyceumClient):
    DOCS_DATA.mkdir(parents=True, exist_ok=True)

    # 1. Download index.json
    print("Downloading designs/index.json...")
    try:
        index_data = client.download_bytes("designs/index.json")
        index = json.loads(index_data)
    except Exception as e:
        print(f"  No index found ({e}). Run evaluate_designs.py first.")
        index = {"designs": []}

    (DOCS_DATA / "index.json").write_text(json.dumps(index, indent=2))
    print(f"  {len(index.get('designs', []))} designs in index")

    # 2. Download per-design metrics.json and CIF structures
    evaluated = []
    unevaluated = []

    for entry in index.get("designs", []):
        did = entry["design_id"]
        tool = entry.get("tool", "unknown")
        prefix = f"designs/{tool}/{did}/"

        # Download metrics.json
        design_dir = DOCS_DATA / tool / did
        design_dir.mkdir(parents=True, exist_ok=True)

        try:
            metrics_data = client.download_bytes(f"{prefix}metrics.json")
            metrics = json.loads(metrics_data)
            (design_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
        except Exception:
            metrics = entry  # fall back to index entry

        # Download designed.cif (try both plain and gzipped)
        cif_text = None
        for cif_name in ["designed.cif", "designed.cif.gz"]:
            try:
                cif_bytes = client.download_bytes(f"{prefix}{cif_name}")
                if cif_name.endswith(".gz"):
                    cif_bytes = gzip.decompress(cif_bytes)
                cif_text = cif_bytes.decode()
                (design_dir / "designed.cif").write_text(cif_text)
                break
            except Exception:
                continue

        # Download refolded.cif if it exists
        try:
            refolded = client.download_bytes(f"{prefix}refolded.cif")
            (design_dir / "refolded.cif").write_text(refolded.decode())
        except Exception:
            pass

        design_record = {
            **metrics,
            "design_id": did,
            "has_structure": cif_text is not None,
        }

        # Classify: evaluated = has validation or scoring or composite_score
        has_eval = (
            metrics.get("validation") is not None
            or metrics.get("scoring") is not None
            or metrics.get("refolding") is not None
            or (entry.get("composite_score") is not None and entry.get("has_validation"))
        )
        if has_eval:
            evaluated.append(design_record)
        else:
            unevaluated.append(design_record)

        status = "evaluated" if has_eval else "unevaluated"
        struct = "+" if cif_text else "-"
        print(f"  [{struct}] {did} ({status})")

    # 3. Also scan raw outputs for designs not yet in the index
    print("\nScanning raw outputs for unevaluated designs...")
    indexed_ids = {e["design_id"] for e in index.get("designs", [])}
    raw_count = _scan_raw_outputs(client, indexed_ids, unevaluated)
    print(f"  {raw_count} additional raw designs found")

    # 4. Write summary JSONs for the viewer
    (DOCS_DATA / "evaluated.json").write_text(json.dumps(evaluated, indent=2))
    (DOCS_DATA / "unevaluated.json").write_text(json.dumps(unevaluated, indent=2))

    print(f"\nSynced to {DOCS_DATA}/")
    print(f"  Evaluated:   {len(evaluated)}")
    print(f"  Unevaluated: {len(unevaluated)}")
    print(f"\nCommit and push docs/ to update GitHub Pages.")


def _scan_raw_outputs(client: LyceumClient, indexed_ids: set, unevaluated: list) -> int:
    """Find CIF files in output/ that aren't in the designs index yet."""
    count = 0
    for tool_prefix in ["output/boltzgen/", "output/rfdiffusion3/"]:
        try:
            files = client.list_files(tool_prefix)
        except Exception:
            continue

        cif_files = [f for f in files if f.endswith(".cif") or f.endswith(".cif.gz")]
        for cif_key in cif_files:
            stem = cif_key.rsplit("/", 1)[-1].replace(".cif.gz", "").replace(".cif", "")
            # Skip if this design is already indexed
            if any(stem in did for did in indexed_ids):
                continue

            tool = "boltzgen" if "boltzgen" in cif_key else "rfdiffusion3"
            did = f"{tool}_raw_{stem}"

            # Download the CIF
            design_dir = DOCS_DATA / tool / did
            design_dir.mkdir(parents=True, exist_ok=True)
            try:
                cif_bytes = client.download_bytes(cif_key)
                if cif_key.endswith(".gz"):
                    cif_bytes = gzip.decompress(cif_bytes)
                (design_dir / "designed.cif").write_text(cif_bytes.decode())

                unevaluated.append({
                    "design_id": did,
                    "tool": tool,
                    "strategy": "unknown",
                    "status": "raw",
                    "has_structure": True,
                    "source_files": {"structure": cif_key},
                })
                count += 1
                print(f"  [+] {did} (raw from {cif_key})")
            except Exception:
                continue

    return count


if __name__ == "__main__":
    client = LyceumClient()
    sync(client)
