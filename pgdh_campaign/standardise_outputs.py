#!/usr/bin/env python3
"""Standardise tool outputs into the designs/ source-of-truth directory on Lyceum S3.

Scans output/boltzgen/ and output/rfdiffusion3/ on S3, parses metrics,
copies structures, and writes standardised metrics.json per design.
Also updates designs/index.json and tracker/state.json.

Usage:
    source .venv/bin/activate
    python pgdh_campaign/standardise_outputs.py
"""

import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root so we can import the client
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "projects" / "biolyceum" / "src" / "utils"))
from client import LyceumClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_strategy(key: str) -> str:
    k = key.lower()
    if "active_site" in k or "s1" in k:
        return "active_site"
    if "dimer" in k or "s2" in k:
        return "dimer_interface"
    if "surface" in k or "s3" in k:
        return "surface"
    return "unknown"


def _strategy_short(strategy: str) -> str:
    return {"active_site": "s1", "dimer_interface": "s2", "surface": "s3"}.get(strategy, strategy)


# ── BoltzGen ─────────────────────────────────────────────────────────────

def standardise_boltzgen(client: LyceumClient) -> list[dict]:
    """Parse BoltzGen outputs from S3 and write standardised designs."""
    designs = []
    csv_files = [f for f in client.list_files("output/boltzgen/") if f.endswith(".csv")]

    for csv_key in csv_files:
        print(f"  Parsing {csv_key}")
        data = client.download_bytes(csv_key).decode()
        strategy = _detect_strategy(csv_key)
        s_short = _strategy_short(strategy)

        reader = csv.DictReader(io.StringIO(data))
        for row in reader:
            raw_id = row.get("id", row.get("design_id", ""))
            if not raw_id:
                continue

            rank = row.get("final_rank", "")
            design_id = f"boltzgen_{s_short}_{raw_id}" if raw_id else f"boltzgen_{s_short}_rank{rank}"
            seq = row.get("sequence", row.get("binder_sequence", ""))

            metrics = {}
            metric_fields = {
                "design_to_target_iptm": "iptm",
                "min_design_to_target_pae": "min_pae",
                "design_ptm": "ptm",
                "filter_rmsd": "filter_rmsd",
                "plip_hbonds_refolded": "plip_hbonds",
                "delta_sasa_refolded": "delta_sasa",
                "helix": "helix",
                "sheet": "sheet",
                "loop": "loop",
                "num_design": "num_designed",
                "num_filters_passed": "filters_passed",
                "quality_score": "quality_score",
            }
            for csv_col, std_name in metric_fields.items():
                val = row.get(csv_col, "")
                if val:
                    try:
                        metrics[std_name] = float(val)
                    except ValueError:
                        metrics[std_name] = val

            # Find matching CIF on S3
            csv_dir = csv_key.rsplit("/", 1)[0]
            cif_source = None
            possible_cif_dirs = [
                f"{csv_dir}/final_ranked_designs/",
                f"{csv_dir}/final_30_designs/",
                csv_dir + "/",
            ]
            for cif_dir in possible_cif_dirs:
                cif_candidates = [f for f in client.list_files(cif_dir) if f.endswith(".cif") and raw_id in f]
                if cif_candidates:
                    cif_source = cif_candidates[0]
                    break

            design_entry = {
                "design_id": design_id,
                "tool": "boltzgen",
                "strategy": strategy,
                "status": "designed",
                "created_at": _now(),
                "sequence": seq,
                "num_residues": len(seq) if seq else 0,
                "source_files": {
                    "metrics_csv": csv_key,
                    "structure": cif_source,
                },
                "metrics": metrics,
                "validation": None,
                "scoring": None,
            }

            # Write standardised metrics.json
            dest_prefix = f"designs/boltzgen/{design_id}/"
            client.upload_bytes(
                json.dumps(design_entry, indent=2).encode(),
                f"{dest_prefix}metrics.json",
            )

            # Copy structure if found
            if cif_source:
                try:
                    cif_data = client.download_bytes(cif_source)
                    client.upload_bytes(cif_data, f"{dest_prefix}structure.cif")
                except Exception as e:
                    print(f"    Warning: could not copy CIF for {design_id}: {e}")

            designs.append(design_entry)
            print(f"    → {design_id}")

    return designs


# ── RFdiffusion3 ─────────────────────────────────────────────────────────

def standardise_rfd3(client: LyceumClient) -> list[dict]:
    """Parse RFdiffusion3 outputs from S3 and write standardised designs."""
    designs = []
    json_files = [f for f in client.list_files("output/rfdiffusion3/") if f.endswith(".json")]

    for json_key in json_files:
        print(f"  Parsing {json_key}")
        try:
            data = json.loads(client.download_bytes(json_key).decode())
        except Exception as e:
            print(f"    Skipping (parse error): {e}")
            continue

        strategy = _detect_strategy(json_key)
        stem = json_key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        design_id = f"rfd3_{strategy}_{stem}"

        metrics_raw = data.get("metrics", {})
        spec = data.get("specification", {})
        num_diffused = len(data.get("diffused_index_map", {}))
        hotspots = list(spec.get("select_hotspots", {}).keys())

        metrics = {
            "helix": metrics_raw.get("helix_fraction"),
            "sheet": metrics_raw.get("sheet_fraction"),
            "loop": metrics_raw.get("loop_fraction"),
            "radius_of_gyration": metrics_raw.get("radius_of_gyration"),
            "max_ca_deviation": metrics_raw.get("max_ca_deviation"),
            "n_chainbreaks": metrics_raw.get("n_chainbreaks"),
            "num_ss_elements": metrics_raw.get("num_ss_elements"),
            "alanine_content": metrics_raw.get("alanine_content"),
            "glycine_content": metrics_raw.get("glycine_content"),
        }
        # Remove None values
        metrics = {k: v for k, v in metrics.items() if v is not None}

        # Find matching CIF (may be .cif or .cif.gz)
        json_dir = json_key.rsplit("/", 1)[0] + "/"
        cif_source = None
        for f in client.list_files(json_dir):
            if stem in f and (f.endswith(".cif") or f.endswith(".cif.gz")):
                cif_source = f
                break

        design_entry = {
            "design_id": design_id,
            "tool": "rfdiffusion3",
            "strategy": strategy,
            "status": "designed",
            "created_at": _now(),
            "sequence": "",  # RFD3 is backbone-only
            "num_residues": num_diffused,
            "hotspots": ", ".join(hotspots),
            "source_files": {
                "metadata_json": json_key,
                "structure": cif_source,
            },
            "metrics": metrics,
            "validation": None,
            "scoring": None,
        }

        # Write standardised metrics.json
        dest_prefix = f"designs/rfdiffusion3/{design_id}/"
        client.upload_bytes(
            json.dumps(design_entry, indent=2).encode(),
            f"{dest_prefix}metrics.json",
        )

        # Copy structure
        if cif_source:
            try:
                cif_data = client.download_bytes(cif_source)
                ext = ".cif.gz" if cif_source.endswith(".cif.gz") else ".cif"
                client.upload_bytes(cif_data, f"{dest_prefix}structure{ext}")
            except Exception as e:
                print(f"    Warning: could not copy CIF for {design_id}: {e}")

        designs.append(design_entry)
        print(f"    → {design_id}")

    return designs


# ── Validation & Scoring ─────────────────────────────────────────────────

def standardise_ipsae(client: LyceumClient, existing_designs: list[dict]) -> int:
    """Check output/ipsae/ for scoring results and attach to existing designs."""
    score_files = [f for f in client.list_files("output/ipsae/") if f.endswith(".txt")]
    if not score_files:
        return 0

    updated = 0
    for sf in score_files:
        try:
            content = client.download_bytes(sf).decode()
            # Parse ipSAE output (tab-separated: name, ipsae, pdockq, pdockq2, lis)
            for line in content.strip().split("\n"):
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                name = parts[0]
                scores = {}
                if len(parts) > 1:
                    scores["ipsae"] = float(parts[1])
                if len(parts) > 2:
                    scores["pdockq"] = float(parts[2])
                if len(parts) > 3:
                    scores["pdockq2"] = float(parts[3])
                if len(parts) > 4:
                    scores["lis"] = float(parts[4])

                # Match to a design
                for d in existing_designs:
                    if d["design_id"] in name or name in d["design_id"]:
                        d["scoring"] = {
                            "tool": "ipsae",
                            **scores,
                            "scored_at": _now(),
                        }
                        # Re-upload updated metrics.json
                        tool = d["tool"]
                        did = d["design_id"]
                        client.upload_bytes(
                            json.dumps(d, indent=2).encode(),
                            f"designs/{tool}/{did}/metrics.json",
                        )
                        updated += 1
                        break
        except Exception as e:
            print(f"    Warning: could not parse {sf}: {e}")

    return updated


# ── Master Index ─────────────────────────────────────────────────────────

def write_index(client: LyceumClient, designs: list[dict]):
    """Write designs/index.json — the master design index."""
    index = {
        "campaign": "pgdh_2gdz",
        "updated_at": _now(),
        "total_designs": len(designs),
        "by_tool": {},
        "by_strategy": {},
        "designs": [],
    }

    for d in designs:
        tool = d.get("tool", "unknown")
        strategy = d.get("strategy", "unknown")
        index["by_tool"][tool] = index["by_tool"].get(tool, 0) + 1
        index["by_strategy"][strategy] = index["by_strategy"].get(strategy, 0) + 1
        index["designs"].append({
            "design_id": d["design_id"],
            "tool": tool,
            "strategy": strategy,
            "status": d.get("status", "designed"),
            "num_residues": d.get("num_residues", 0),
            "has_sequence": bool(d.get("sequence")),
            "has_validation": d.get("validation") is not None,
            "has_scoring": d.get("scoring") is not None,
            "iptm": d.get("metrics", {}).get("iptm"),
            "ptm": d.get("metrics", {}).get("ptm"),
            "ipsae": (d.get("scoring") or {}).get("ipsae"),
        })

    client.upload_bytes(
        json.dumps(index, indent=2).encode(),
        "designs/index.json",
    )
    print(f"\nWrote designs/index.json ({len(designs)} designs)")


# ── Tracker Sync ─────────────────────────────────────────────────────────

def sync_tracker(client: LyceumClient, designs: list[dict]):
    """Update tracker/state.json with standardised designs."""
    try:
        state = json.loads(client.download_bytes("tracker/state.json").decode())
    except Exception:
        state = {"campaign": "pgdh_2gdz", "updated_at": _now(), "designs": [], "jobs": []}

    existing_ids = {d["id"] for d in state.get("designs", [])}
    added = 0
    for d in designs:
        if d["design_id"] not in existing_ids:
            state["designs"].append({
                "id": d["design_id"],
                "tool": d["tool"],
                "strategy": d["strategy"],
                "status": d.get("status", "designed"),
                "sequence": d.get("sequence", ""),
                "num_residues": d.get("num_residues", 0),
                "metrics": d.get("metrics", {}),
                "notes": "",
                "created_at": d.get("created_at", _now()),
            })
            added += 1

    state["updated_at"] = _now()
    client.upload_bytes(
        json.dumps(state, indent=2).encode(),
        "tracker/state.json",
    )
    print(f"Synced tracker/state.json (+{added} new designs)")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("Standardising PGDH design outputs...\n")
    client = LyceumClient()

    print("=== BoltzGen ===")
    boltzgen_designs = standardise_boltzgen(client)
    print(f"  {len(boltzgen_designs)} BoltzGen designs\n")

    print("=== RFdiffusion3 ===")
    rfd3_designs = standardise_rfd3(client)
    print(f"  {len(rfd3_designs)} RFD3 designs\n")

    all_designs = boltzgen_designs + rfd3_designs

    print("=== ipSAE Scoring ===")
    n_scored = standardise_ipsae(client, all_designs)
    print(f"  {n_scored} designs scored\n")

    print("=== Writing index ===")
    write_index(client, all_designs)

    print("\n=== Syncing tracker ===")
    sync_tracker(client, all_designs)

    print(f"\nDone. {len(all_designs)} total designs standardised.")


if __name__ == "__main__":
    main()
