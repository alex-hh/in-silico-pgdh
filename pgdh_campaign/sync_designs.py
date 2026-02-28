#!/usr/bin/env python3
"""Collect, standardise, and rank PGDH binder designs (no GPU).

This is the ONLY script that writes to the `designs/` source-of-truth
directory on Lyceum S3. All other code reads from it.

Design tools can write their raw outputs anywhere they like (output/boltzgen/,
a local directory, etc.). This script:

1. Collects raw outputs from all registered tool adapters
2. Copies the designer's predicted structures into designs/<tool>/<id>/
3. Attaches any existing refolding, validation, and scoring results
4. Ranks by composite score
5. Writes designs/index.json and syncs tracker/state.json

Usage:
    source .venv/bin/activate
    python pgdh_campaign/sync_designs.py

This script is also importable — the Streamlit dashboard and
evaluate_designs.py both call `sync_all()` directly.

S3 Data Architecture:

1. output/           Raw tool outputs (written by Lyceum GPU jobs)
2. designs/          ALL designs — source of truth (written ONLY by sync_designs.py)
3. tracker/state.json  Jobs + notes (synced by sync_designs.py, written by dashboard)

Two commands:
  python pgdh_campaign/sync_designs.py          # Collect + rank (no GPU, fast)
  python pgdh_campaign/evaluate_designs.py      # Submit GPU jobs (--refold/--validate/--score)

After design jobs:   run sync_designs.py
After eval jobs:     run sync_designs.py again (picks up new results)
"""

import csv
import gzip
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "projects" / "biolyceum" / "src" / "utils"))
from client import LyceumClient


# ── Helpers ──────────────────────────────────────────────────────────────

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
    if "helix_hairpin_inpaint" in k:
        return "helix_hairpin_inpaint"
    return "unknown"


def _strategy_short(strategy: str) -> str:
    return {"active_site": "s1", "dimer_interface": "s2", "surface": "s3"}.get(strategy, strategy)


def _extract_sequence_from_cif_bytes(cif_bytes: bytes, chain_id: str = "A") -> str:
    """Extract protein sequence for a chain from CIF file contents (bytes).

    Handles both plain .cif and gzip-compressed .cif.gz data.
    Adapted from generate_pages.py extract_sequence().
    """
    # Decompress if gzipped
    try:
        text = gzip.decompress(cif_bytes).decode()
    except (gzip.BadGzipFile, OSError):
        text = cif_bytes.decode()

    three_to_one = {
        "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
        "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
        "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
        "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    }
    residues = {}
    in_atom_site = False
    headers = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("_atom_site."):
            in_atom_site = True
            headers.append(line.split(".")[1])
            continue
        if in_atom_site and not line.startswith("_") and not line.startswith("#") and line:
            fields = line.split()
            if len(fields) < len(headers):
                in_atom_site = False
                continue
            row = dict(zip(headers, fields))
            chain = row.get("label_asym_id", row.get("auth_asym_id", ""))
            if chain != chain_id:
                continue
            if row.get("group_PDB") != "ATOM":
                continue
            resname = row.get("label_comp_id", "")
            resnum = int(row.get("label_seq_id", 0))
            if resname in three_to_one and resnum not in residues:
                residues[resnum] = three_to_one[resname]
        elif in_atom_site and (line.startswith("#") or line == ""):
            if residues:
                break
    if not residues:
        return ""
    return "".join(residues[k] for k in sorted(residues))


def _detect_binder_chain(metadata: dict) -> str:
    """Determine which CIF chain is the designed binder from RFD3 JSON metadata.

    For multi-chain binder designs: diffused_index_map maps input target residues
    (e.g. A0-A265) to one output chain — the binder is the OTHER chain.

    For single-chain inpainting (segment_replace): both input keys and output values
    reference the same chain — the binder IS that chain.

    Falls back to chain A if detection fails.
    """
    dim = metadata.get("diffused_index_map", {})
    if not dim:
        return "A"

    # Extract chain letters from input keys (e.g. "A96" -> "A") and output values (e.g. "B42" -> "B")
    input_chains = set()
    output_chains = set()
    for input_key, output_pos in dim.items():
        if isinstance(input_key, str) and input_key:
            input_chains.add(input_key[0])
        if isinstance(output_pos, str) and output_pos:
            output_chains.add(output_pos[0])

    # Single-chain inpainting: input and output map to the same chain(s)
    # The entire output IS the binder (with inpainted segment)
    if input_chains == output_chains:
        return sorted(output_chains)[0] if output_chains else "A"

    # Multi-chain: output chains are the target; binder is the other chain
    if len(output_chains) == 1:
        target_chain = output_chains.pop()
        return "B" if target_chain == "A" else "A"

    return "A"  # fallback


# ══════════════════════════════════════════════════════════════════════════
# TOOL ADAPTERS
#
# Each adapter scans its output/ prefix on S3 and returns a list of
# standardised design dicts. To add a new tool, write an adapter function
# and register it in TOOL_ADAPTERS.
# ══════════════════════════════════════════════════════════════════════════

def parse_boltzgen_outputs(client: LyceumClient, prefix: str = "output/boltzgen/") -> list[dict]:
    """Parse BoltzGen CSV metrics + find matching CIF structures."""
    csv_files = [f for f in client.list_files(prefix) if f.endswith(".csv")]
    if not csv_files:
        return []

    designs = []
    for csv_key in csv_files:
        print(f"  Parsing {csv_key}")
        data = client.download_bytes(csv_key).decode()
        strategy = _detect_strategy(csv_key)
        s_short = _strategy_short(strategy)
        csv_dir = csv_key.rsplit("/", 1)[0]

        reader = csv.DictReader(io.StringIO(data))
        for row in reader:
            raw_id = row.get("id", row.get("design_id", ""))
            if not raw_id:
                continue

            rank = row.get("final_rank", "")
            design_id = f"boltzgen_{s_short}_{raw_id}"
            seq = row.get("sequence", row.get("binder_sequence", ""))

            # Map tool-specific columns -> standard metric names
            metric_map = {
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
            metrics = {}
            for csv_col, std_name in metric_map.items():
                val = row.get(csv_col, "")
                if val:
                    try:
                        metrics[std_name] = float(val)
                    except ValueError:
                        metrics[std_name] = val
            metrics["source"] = "boltzgen"

            # Find matching CIF
            cif_source = None
            for cif_dir in [f"{csv_dir}/final_ranked_designs/", f"{csv_dir}/final_30_designs/", csv_dir + "/"]:
                cif_candidates = [f for f in client.list_files(cif_dir) if f.endswith(".cif") and raw_id in f]
                if cif_candidates:
                    cif_source = cif_candidates[0]
                    break

            if not seq:
                print(f"    Skipping {design_id}: no sequence")
                continue

            designs.append({
                "design_id": design_id,
                "tool": "boltzgen",
                "strategy": strategy,
                "status": "designed",
                "evaluation_stage": "raw",
                "created_at": _now(),
                "sequence": seq,
                "num_residues": len(seq),
                "rank": rank,
                "source_files": {"metrics_csv": csv_key, "structure": cif_source},
                "design_metrics": metrics,
                "validation": None,
                "scoring": None,
                "composite_score": None,
            })
            print(f"    -> {design_id}")

    return designs


def parse_rfd3_outputs(client: LyceumClient, prefix: str = "output/rfdiffusion3/") -> list[dict]:
    """Parse RFdiffusion3 JSON metadata + find matching CIF structures."""
    json_files = [f for f in client.list_files(prefix) if f.endswith(".json")]
    if not json_files:
        return []

    designs = []
    for json_key in json_files:
        print(f"  Parsing {json_key}")
        try:
            data = json.loads(client.download_bytes(json_key).decode())
        except Exception as e:
            print(f"    Skipping (parse error): {e}")
            continue

        strategy = _detect_strategy(json_key)
        stem = json_key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        design_id = f"rfd3_{_strategy_short(strategy)}_{stem}"

        metrics_raw = data.get("metrics", {})
        spec = data.get("specification", {})
        num_diffused = len(data.get("diffused_index_map", {}))
        hotspots = list(spec.get("select_hotspots", {}).keys())

        metrics = {}
        rename = {
            "helix_fraction": "helix", "sheet_fraction": "sheet", "loop_fraction": "loop",
            "radius_of_gyration": "radius_of_gyration", "max_ca_deviation": "max_ca_deviation",
            "n_chainbreaks": "n_chainbreaks", "num_ss_elements": "num_ss_elements",
            "alanine_content": "alanine_content", "glycine_content": "glycine_content",
        }
        for raw_name, std_name in rename.items():
            val = metrics_raw.get(raw_name)
            if val is not None:
                metrics[std_name] = val
        metrics["source"] = "rfdiffusion3"

        # Find matching CIF
        json_dir = json_key.rsplit("/", 1)[0] + "/"
        cif_source = None
        for f in client.list_files(json_dir):
            if stem in f and (f.endswith(".cif") or f.endswith(".cif.gz")):
                cif_source = f
                break

        # Extract designed binder sequence from CIF
        sequence = ""
        if cif_source:
            try:
                cif_bytes = client.download_bytes(cif_source)
                binder_chain = _detect_binder_chain(data)
                sequence = _extract_sequence_from_cif_bytes(cif_bytes, chain_id=binder_chain)
                if sequence:
                    print(f"    Extracted {len(sequence)} AA sequence from chain {binder_chain}")
                else:
                    print(f"    Warning: no sequence found in chain {binder_chain}")
            except Exception as e:
                print(f"    Warning: could not extract sequence from CIF: {e}")

        if not sequence:
            print(f"    Skipping {design_id}: no sequence (CIF extraction failed)")
            continue

        designs.append({
            "design_id": design_id,
            "tool": "rfdiffusion3",
            "strategy": strategy,
            "status": "designed",
            "evaluation_stage": "raw",
            "created_at": _now(),
            "sequence": sequence,
            "num_residues": len(sequence),
            "hotspots": ", ".join(hotspots),
            "source_files": {"metadata_json": json_key, "structure": cif_source},
            "design_metrics": metrics,
            "validation": None,
            "scoring": None,
            "composite_score": None,
        })
        print(f"    -> {design_id}")

    return designs


# Register all tool adapters here.
# To add a new design tool: write a parse_<tool>_outputs function and add it.
TOOL_ADAPTERS = {
    "boltzgen": {"fn": parse_boltzgen_outputs, "prefix": "output/boltzgen/"},
    "rfdiffusion3": {"fn": parse_rfd3_outputs, "prefix": "output/rfdiffusion3/"},
    # Future tools:
    # "bindcraft": {"fn": parse_bindcraft_outputs, "prefix": "output/bindcraft/"},
}


# ══════════════════════════════════════════════════════════════════════════
# STEP 1: COLLECT — scan all tool outputs
# ══════════════════════════════════════════════════════════════════════════

def collect_designs(client: LyceumClient) -> list[dict]:
    """Run all tool adapters and merge with existing designs on S3."""
    all_designs = []

    for tool_name, adapter in TOOL_ADAPTERS.items():
        print(f"\n=== {tool_name} ===")
        designs = adapter["fn"](client, adapter["prefix"])
        print(f"  {len(designs)} designs found")
        all_designs.extend(designs)

    # Merge with existing designs/ on S3 (preserve validation/scoring data)
    existing = _load_existing_designs(client)
    merged = _merge_designs(all_designs, existing)

    # Mark all collected designs as at least "collected"
    for d in merged:
        if d.get("evaluation_stage", "raw") == "raw":
            d["evaluation_stage"] = "collected"

    return merged


def _load_existing_designs(client: LyceumClient) -> dict[str, dict]:
    """Load existing per-design metrics.json files from designs/ on S3."""
    existing = {}
    try:
        index_data = client.download_bytes("designs/index.json")
        index = json.loads(index_data.decode())
        for entry in index.get("designs", []):
            did = entry["design_id"]
            tool = entry.get("tool", "unknown")
            try:
                data = client.download_bytes(f"designs/{tool}/{did}/metrics.json")
                existing[did] = json.loads(data.decode())
            except Exception:
                pass
    except Exception:
        pass
    return existing


def _merge_designs(new: list[dict], existing: dict[str, dict]) -> list[dict]:
    """Merge new scan results with existing data, preserving validation/scoring."""
    merged = {}
    for d in new:
        did = d["design_id"]
        if did in existing:
            old = existing[did]
            # Keep existing eval results if new doesn't have them
            d["validation"] = d.get("validation") or old.get("validation")
            d["scoring"] = d.get("scoring") or old.get("scoring")
            d["refolding"] = d.get("refolding") or old.get("refolding")
            # Preserve status if it was advanced beyond "designed"
            old_status = old.get("status", "designed")
            if old_status != "designed" and d.get("status") == "designed":
                d["status"] = old_status
        merged[did] = d

    # Keep existing designs not found in new scan (e.g. tool output was deleted from S3)
    for did, old in existing.items():
        if did not in merged:
            merged[did] = old

    # Migrate old refolding field names (rmsd -> boltzgen_rmsd, etc.)
    for d in merged.values():
        refold = d.get("refolding")
        if refold and "rmsd" in refold and "boltzgen_rmsd" not in refold:
            refold["boltzgen_rmsd"] = refold.pop("rmsd")
            if "plddt" in refold:
                refold["boltzgen_plddt"] = refold.pop("plddt")
            if "iptm" in refold:
                refold["boltzgen_iptm"] = refold.pop("iptm")

    return list(merged.values())


# ══════════════════════════════════════════════════════════════════════════
# STEP 2: ATTACH EXISTING SCORES — check output/ipsae/ and output/boltz2/
# ══════════════════════════════════════════════════════════════════════════

def attach_ipsae_scores(client: LyceumClient, designs: list[dict]) -> int:
    """Scan output/ipsae/ for existing scoring results and attach them."""
    score_files = [f for f in client.list_files("output/ipsae/") if f.endswith(".txt")]
    if not score_files:
        return 0

    updated = 0
    for sf in score_files:
        try:
            content = client.download_bytes(sf).decode()
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

                for d in designs:
                    if d.get("scoring"):
                        continue
                    if d["design_id"] in name or name in d["design_id"]:
                        d["scoring"] = {"source": "ipsae", "scored_at": _now(), **scores}
                        d["status"] = "scored" if d["status"] == "designed" else d["status"]
                        d["evaluation_stage"] = "scored"
                        updated += 1
                        break
        except Exception as e:
            print(f"    Warning: could not parse {sf}: {e}")

    return updated


def attach_boltz2_validation(client: LyceumClient, designs: list[dict]) -> int:
    """Scan output/boltz2/ for existing validation results and attach them."""
    json_files = [f for f in client.list_files("output/boltz2/") if f.endswith(".json")]
    if not json_files:
        return 0

    updated = 0
    for jf in json_files:
        try:
            data = json.loads(client.download_bytes(jf).decode())
            # Boltz-2 output JSON contains confidence metrics
            iptm = data.get("confidence_score", {}).get("iptm") or data.get("iptm")
            ptm = data.get("confidence_score", {}).get("ptm") or data.get("ptm")
            plddt = data.get("confidence_score", {}).get("plddt") or data.get("plddt")

            stem = jf.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for d in designs:
                if d.get("validation"):
                    continue
                if d["design_id"] in stem or stem in d["design_id"]:
                    d["validation"] = {
                        "source": "boltz2",
                        "validated_at": _now(),
                        "iptm": iptm,
                        "ptm": ptm,
                        "plddt": plddt,
                    }
                    if d["status"] == "designed":
                        d["status"] = "validated"
                    d["evaluation_stage"] = "validated"
                    updated += 1
                    break
        except Exception as e:
            print(f"    Warning: could not parse {jf}: {e}")

    return updated


def _promote_boltzgen_self_consistency(designs: list[dict]) -> int:
    """Promote BoltzGen self-consistency metrics into the refolding field.

    BoltzGen already computes filter_rmsd during design. For these designs,
    we promote the existing metrics into the refolding field so the composite
    score picks them up — no GPU needed.

    Returns count of promoted designs.
    """
    promoted = 0
    for d in designs:
        if d.get("refolding"):
            continue
        if d.get("tool") != "boltzgen":
            continue

        dm = d.get("design_metrics") or {}
        filter_rmsd = dm.get("filter_rmsd")
        if filter_rmsd is None:
            continue

        try:
            rmsd_val = float(filter_rmsd)
        except (ValueError, TypeError):
            continue

        d["refolding"] = {
            "source": "boltzgen_self_consistency",
            "boltzgen_rmsd": rmsd_val,
            "boltzgen_plddt": dm.get("plddt"),
            "boltzgen_iptm": dm.get("iptm"),
        }

        if d.get("evaluation_stage") in ("raw", "collected"):
            d["evaluation_stage"] = "validated"
        if d.get("status") == "designed":
            d["status"] = "validated"

        promoted += 1

    return promoted


def attach_refolding_results(client: LyceumClient, designs: list[dict]) -> int:
    """Scan output/refolding/ on S3 for completed BoltzGen folding results and attach them.

    Also promotes BoltzGen self-consistency metrics (filter_rmsd) into the refolding
    field for BoltzGen designs that don't have explicit refolding results.

    For each design with results in output/refolding/<design_id>/, finds the refolded
    CIF and any metrics, then attaches refolding data including RMSD if computable.
    """
    # First: promote BoltzGen self-consistency (free, no S3 scan needed)
    n_promoted = _promote_boltzgen_self_consistency(designs)
    if n_promoted:
        print(f"    Promoted {n_promoted} BoltzGen designs (self-consistency -> refolding)")

    refolding_dirs = client.list_files("output/refolding/")
    if not refolding_dirs:
        return n_promoted

    # Group files by design_id (first path component after output/refolding/)
    by_design: dict[str, list[str]] = {}
    for f in refolding_dirs:
        # output/refolding/<design_id>/...
        parts = f.split("/")
        if len(parts) >= 4:
            did = parts[2]
            by_design.setdefault(did, []).append(f)

    updated = 0
    for d in designs:
        if d.get("refolding"):
            continue  # Already has refolding data

        did = d["design_id"]
        if did not in by_design:
            continue

        files = by_design[did]

        # Find refolded CIF
        cif_files = [f for f in files if f.endswith(".cif")]
        if not cif_files:
            continue

        refolded_cif = cif_files[0]

        # Try to find BoltzGen's metrics CSV for RMSD / pLDDT
        csv_files = [f for f in files if f.endswith(".csv")]
        rmsd = None
        plddt = None
        iptm = None

        if csv_files:
            try:
                csv_data = client.download_bytes(csv_files[0]).decode()
                reader = csv.DictReader(io.StringIO(csv_data))
                for row in reader:
                    # BoltzGen metrics columns
                    if row.get("filter_rmsd"):
                        try:
                            rmsd = float(row["filter_rmsd"])
                        except ValueError:
                            pass
                    if row.get("design_plddt"):
                        try:
                            plddt = float(row["design_plddt"])
                        except ValueError:
                            pass
                    if row.get("design_to_target_iptm"):
                        try:
                            iptm = float(row["design_to_target_iptm"])
                        except ValueError:
                            pass
                    break  # Only need first row for this design
            except Exception as e:
                print(f"    Warning: could not parse refolding CSV for {did}: {e}")

        # Also check for JSON confidence files (BoltzGen sometimes writes these)
        json_files = [f for f in files if f.endswith(".json") and "confidence" in f.lower()]
        if json_files and plddt is None:
            try:
                jdata = json.loads(client.download_bytes(json_files[0]).decode())
                plddt = jdata.get("plddt") or jdata.get("confidence_score", {}).get("plddt")
            except Exception:
                pass

        d["refolding"] = {
            "source": "boltzgen_folding",
            "refolded_at": _now(),
            "boltzgen_rmsd": rmsd,
            "boltzgen_plddt": plddt,
            "boltzgen_iptm": iptm,
        }
        d["source_files"] = d.get("source_files") or {}
        d["source_files"]["refolded_structure"] = refolded_cif

        # Advance evaluation stage if not already beyond
        if d.get("evaluation_stage") in ("raw", "collected"):
            d["evaluation_stage"] = "validated"
        if d.get("status") == "designed":
            d["status"] = "validated"
        updated += 1
        print(f"    Attached refolding for {did}: boltzgen_rmsd={rmsd}, boltzgen_plddt={plddt}")

    return updated + n_promoted


# ══════════════════════════════════════════════════════════════════════════
# STEP 3: RANK — composite score
# ══════════════════════════════════════════════════════════════════════════

def compute_composite_scores(designs: list[dict]) -> list[dict]:
    """Compute composite ranking score and sort designs."""
    for d in designs:
        dm = d.get("design_metrics", {})
        val = d.get("validation") or {}
        scr = d.get("scoring") or {}

        score = 0.0
        weight_sum = 0.0

        # Design-time metrics (available for all designs)
        iptm = dm.get("iptm")
        if iptm is not None:
            try:
                score += 0.25 * float(iptm)
                weight_sum += 0.25
            except (ValueError, TypeError):
                pass

        ptm = dm.get("ptm")
        if ptm is not None:
            try:
                score += 0.10 * float(ptm)
                weight_sum += 0.10
            except (ValueError, TypeError):
                pass

        # Design-time RMSD (lower is better — invert: 1 - rmsd/5, clamped to [0,1])
        rmsd = dm.get("filter_rmsd")
        if rmsd is not None:
            try:
                score += 0.05 * max(0, 1 - float(rmsd) / 5.0)
                weight_sum += 0.05
            except (ValueError, TypeError):
                pass

        # Designability RMSD from refolding (lower is better, key metric)
        refold = d.get("refolding") or {}
        refold_rmsd = refold.get("boltzgen_rmsd")
        if refold_rmsd is not None:
            try:
                score += 0.15 * max(0, 1 - float(refold_rmsd) / 5.0)
                weight_sum += 0.15
            except (ValueError, TypeError):
                pass

        # Validation metrics (complex prediction with target)
        val_iptm = val.get("iptm")
        if val_iptm is not None:
            try:
                score += 0.20 * float(val_iptm)
                weight_sum += 0.20
            except (ValueError, TypeError):
                pass

        val_plddt = val.get("plddt")
        if val_plddt is not None:
            try:
                score += 0.10 * (float(val_plddt) / 100.0)
                weight_sum += 0.10
            except (ValueError, TypeError):
                pass

        # Scoring metrics
        ipsae = scr.get("ipsae")
        if ipsae is not None:
            try:
                score += 0.25 * float(ipsae)
                weight_sum += 0.25
            except (ValueError, TypeError):
                pass

        # Normalise to available weight
        d["composite_score"] = round(score / weight_sum, 4) if weight_sum > 0 else None

    # Sort by composite score (highest first), None last
    designs.sort(key=lambda d: (d["composite_score"] is not None, d["composite_score"] or 0), reverse=True)

    # Assign ranks
    for i, d in enumerate(designs):
        d["rank"] = i + 1

    return designs


# ══════════════════════════════════════════════════════════════════════════
# STEP 4: WRITE — upload standardised files to S3
# ══════════════════════════════════════════════════════════════════════════

def write_designs_to_s3(client: LyceumClient, designs: list[dict]):
    """Write per-design files to designs/ source of truth on S3.

    For each design, writes:
      designs/<tool>/<id>/metrics.json    — all metrics, scores, metadata
      designs/<tool>/<id>/designed.cif    — designer's predicted structure (copied from raw output)
      designs/<tool>/<id>/refolded.cif    — Boltz-2 refolded structure (if available)
    """
    for d in designs:
        tool = d["tool"]
        did = d["design_id"]
        dest_prefix = f"designs/{tool}/{did}/"

        # Write metrics.json
        client.upload_bytes(
            json.dumps(d, indent=2).encode(),
            f"{dest_prefix}metrics.json",
        )

        # Copy designer's predicted structure (the structure the design tool produced)
        cif_source = (d.get("source_files") or {}).get("structure")
        if cif_source:
            try:
                ext = ".cif.gz" if cif_source.endswith(".cif.gz") else ".cif"
                dest_key = f"{dest_prefix}designed{ext}"
                existing = client.list_files(dest_key)
                if not existing:
                    cif_data = client.download_bytes(cif_source)
                    client.upload_bytes(cif_data, dest_key)
            except Exception:
                pass

        # Copy refolded structure if it exists (from Boltz-2 refolding)
        refolded_source = (d.get("source_files") or {}).get("refolded_structure")
        if refolded_source:
            try:
                dest_key = f"{dest_prefix}refolded.cif"
                existing = client.list_files(dest_key)
                if not existing:
                    cif_data = client.download_bytes(refolded_source)
                    client.upload_bytes(cif_data, dest_key)
            except Exception:
                pass


def write_index(client: LyceumClient, designs: list[dict]):
    """Write designs/index.json — master ranked index."""
    by_tool = {}
    by_strategy = {}
    by_status = {}
    entries = []

    for d in designs:
        tool = d.get("tool", "unknown")
        strategy = d.get("strategy", "unknown")
        status = d.get("status", "designed")

        by_tool[tool] = by_tool.get(tool, 0) + 1
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1

        dm = d.get("design_metrics", {})
        scr = d.get("scoring") or {}

        refold = d.get("refolding") or {}

        entries.append({
            "design_id": d["design_id"],
            "tool": tool,
            "strategy": strategy,
            "status": status,
            "rank": d.get("rank"),
            "composite_score": d.get("composite_score"),
            "num_residues": d.get("num_residues", 0),
            "has_refolding": d.get("refolding") is not None,
            "has_validation": d.get("validation") is not None,
            "has_scoring": d.get("scoring") is not None,
            "iptm": dm.get("iptm"),
            "ptm": dm.get("ptm"),
            "filter_rmsd": dm.get("filter_rmsd"),
            "refold_rmsd": refold.get("boltzgen_rmsd"),
            "ipsae": scr.get("ipsae"),
        })

    index = {
        "campaign": "pgdh_2gdz",
        "updated_at": _now(),
        "total_designs": len(designs),
        "by_tool": by_tool,
        "by_strategy": by_strategy,
        "by_status": by_status,
        "designs": entries,
    }

    client.upload_bytes(json.dumps(index, indent=2).encode(), "designs/index.json")
    print(f"\nWrote designs/index.json ({len(designs)} designs)")
    print(f"  by_tool: {by_tool}")
    print(f"  by_strategy: {by_strategy}")
    print(f"  by_status: {by_status}")


def sync_tracker(client: LyceumClient, designs: list[dict]):
    """Sync tracker/state.json with evaluated designs."""
    try:
        state = json.loads(client.download_bytes("tracker/state.json").decode())
    except Exception:
        state = {"campaign": "pgdh_2gdz", "updated_at": _now(), "designs": [], "jobs": []}

    existing_ids = {d["id"] for d in state.get("designs", [])}
    existing_by_id = {d["id"]: d for d in state.get("designs", [])}
    added = 0
    updated = 0

    for d in designs:
        did = d["design_id"]
        tracker_entry = {
            "id": did,
            "tool": d["tool"],
            "strategy": d.get("strategy", ""),
            "status": d.get("status", "designed"),
            "evaluation_stage": d.get("evaluation_stage", "collected"),
            "sequence": d.get("sequence", ""),
            "num_residues": d.get("num_residues", 0),
            "metrics": d.get("design_metrics", {}),
            "composite_score": d.get("composite_score"),
            "rank": d.get("rank"),
            "notes": existing_by_id.get(did, {}).get("notes", ""),
            "created_at": d.get("created_at", _now()),
        }
        if did in existing_ids:
            # Update existing entry (preserve notes)
            for i, existing in enumerate(state["designs"]):
                if existing["id"] == did:
                    tracker_entry["notes"] = existing.get("notes", "")
                    state["designs"][i] = tracker_entry
                    updated += 1
                    break
        else:
            state["designs"].append(tracker_entry)
            added += 1

    state["updated_at"] = _now()
    client.upload_bytes(json.dumps(state, indent=2).encode(), "tracker/state.json")
    print(f"Synced tracker/state.json (+{added} new, ~{updated} updated)")


# ══════════════════════════════════════════════════════════════════════════
# MAIN — sync_all entry point
# ══════════════════════════════════════════════════════════════════════════

def sync_all(client: LyceumClient = None, extra_designs: list[dict] | None = None) -> list[dict]:
    """Collect, standardise, rank, and write all designs. Returns ranked list.

    Callable from CLI or imported by evaluate_designs.py and the Streamlit app.

    Args:
        client: LyceumClient instance (created if None).
        extra_designs: Additional designs to inject (e.g. custom FASTA uploads).
    """
    if client is None:
        client = LyceumClient()

    print("=== PGDH Design Sync Pipeline ===\n")

    # Step 1: Collect from all tools
    print("--- Step 1: Collect designs ---")
    designs = collect_designs(client)
    if extra_designs:
        print(f"  + {len(extra_designs)} extra designs injected")
        designs.extend(extra_designs)
    print(f"\n  Total: {len(designs)} designs collected\n")

    # Step 2: Attach existing validation/scoring/refolding results
    print("--- Step 2: Attach existing scores ---")
    n_val = attach_boltz2_validation(client, designs)
    n_refold = attach_refolding_results(client, designs)
    n_scr = attach_ipsae_scores(client, designs)
    print(f"  Attached {n_val} validations, {n_refold} refoldings, {n_scr} scores\n")

    # Step 3: Rank
    print("--- Step 3: Rank ---")
    designs = compute_composite_scores(designs)
    top = [d for d in designs[:5] if d.get("composite_score")]
    if top:
        print("  Top 5:")
        for d in top:
            print(f"    #{d['rank']} {d['design_id']}: {d['composite_score']:.4f}")
    print()

    # Step 4: Write to S3 (copy designer structures + refolded structures + metrics)
    print("--- Step 4: Write to S3 ---")
    write_designs_to_s3(client, designs)
    write_index(client, designs)
    sync_tracker(client, designs)

    print(f"\nDone. {len(designs)} designs synced and ranked.")
    return designs


def main():
    sync_all()


if __name__ == "__main__":
    main()
