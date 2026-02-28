#!/usr/bin/env python3
"""Submit GPU evaluation jobs for PGDH binder designs.

This script handles GPU-intensive evaluation steps: BoltzGen refolding,
Boltz-2 cross-validation, and ipSAE scoring. It does NOT collect or rank
designs — that's done by sync_designs.py.

Two evaluation modes:

  --fast   Designability check via BoltzGen refolding. Cheap, run for all designs.
           BoltzGen designs get free promotion (filter_rmsd already computed).
           RFD3 designs get a BoltzGen folding job submitted.

  --slow   Full Boltz-2 cross-validation. Expensive, manually triggered.
           Pass design IDs to validate specific designs, or --auto to
           auto-select designs with refolding RMSD < 2.5A and no validation.

Usage:
    source .venv/bin/activate

    # Fast: BoltzGen refolding for all new designs
    python pgdh_campaign/evaluate_designs.py --fast

    # Slow: Boltz-2 cross-validation for specific designs
    python pgdh_campaign/evaluate_designs.py --slow design_id_1 design_id_2

    # Slow: auto-select designs with good refolding (RMSD < 2.5A)
    python pgdh_campaign/evaluate_designs.py --slow --auto

    # ipSAE scoring (can combine with either)
    python pgdh_campaign/evaluate_designs.py --fast --score
    python pgdh_campaign/evaluate_designs.py --score

    # PyRosetta interface scoring (CPU, no GPU needed)
    python pgdh_campaign/evaluate_designs.py --interface

After jobs complete, run sync_designs.py to pick up results:
    python pgdh_campaign/sync_designs.py

S3 Data Architecture:

1. output/           Raw tool outputs (written by Lyceum GPU jobs)
2. designs/          ALL designs — source of truth (written ONLY by sync_designs.py)

Two commands:
  python pgdh_campaign/sync_designs.py          # Collect + rank (no GPU, fast)
  python pgdh_campaign/evaluate_designs.py      # Submit GPU jobs (--fast/--slow/--score)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "projects" / "biolyceum" / "src" / "utils"))
from client import LyceumClient

from sync_designs import sync_all


# ── Constants ─────────────────────────────────────────────────────────────

PGDH_SEQUENCE = (
    "AHMVNGKVALVTGAAQGIGRAFAEALLLKGAKVALVDWNLEAGVQCKAALHEQFEPQKTLFIQCDVADQQQLRD"
    "TFRKVVDHFGRLDILVNNAGVNNEKNWEKTLQINLVSVISGTYLGLDYMSKQNGGEGGIIINMSSLAGLMPVAQ"
    "QPVYCASKHGIVGFTRSAALAANLMNSGVRLNAICPGFVNTAILESIEKEENMGQYIEYKDHIKDMIKYYGILD"
    "PPLIANGLITLIEDDALNGAIMKITTSKGIHFQDYGSKENLYFQ"
)

FAST_EVAL_RMSD_THRESHOLD = 2.5  # Angstroms — designs below this auto-qualify for slow eval


# ══════════════════════════════════════════════════════════════════════════
# PROMOTE — BoltzGen self-consistency metrics into refolding field
# ══════════════════════════════════════════════════════════════════════════

def promote_boltzgen_refolding(designs: list[dict]) -> int:
    """Promote BoltzGen self-consistency metrics into the refolding field.

    BoltzGen already computes filter_rmsd during design (built-in self-consistency
    check). For these designs, we promote the existing metrics into the refolding
    field so the composite score picks them up — no GPU needed.

    This is an in-memory promotion. sync_designs.py will persist it when writing
    metrics.json.

    Returns:
        Count of designs promoted.
    """
    promoted = 0
    for d in designs:
        if d.get("refolding"):
            continue  # Already has refolding data
        if d.get("tool") != "boltzgen":
            continue  # Only applies to BoltzGen designs

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

        # Advance evaluation stage
        if d.get("evaluation_stage") in ("raw", "collected"):
            d["evaluation_stage"] = "validated"
        if d.get("status") == "designed":
            d["status"] = "validated"

        promoted += 1

    return promoted


# ══════════════════════════════════════════════════════════════════════════
# REFOLD — BoltzGen folding mode for designability (--fast for RFD3)
# ══════════════════════════════════════════════════════════════════════════

def _generate_refold_yaml(design_id: str, sequence: str, target_cif: str = "2GDZ.cif") -> str:
    """Generate a BoltzGen YAML for refolding a binder sequence against the PGDH target."""
    return (
        f"# Refolding YAML for {design_id}\n"
        f"entities:\n"
        f"  - protein:\n"
        f"      id: B\n"
        f"      sequence: {sequence}\n"
        f"  - file:\n"
        f"      path: {target_cif}\n"
        f"      include:\n"
        f"        - chain:\n"
        f"            id: A\n"
    )


def submit_refold_jobs(client: LyceumClient, designs: list[dict]) -> list[tuple[str, str]]:
    """Submit a single BoltzGen batch job to refold all designs needing refolding.

    Uploads all refolding YAMLs, generates a batch script that processes each
    sequentially, and submits one Docker job.

    Passes --write_full_pae via --extra-args so PAE files are saved for
    downstream ipSAE scoring.

    Skips BoltzGen designs (they get free promotion via promote_boltzgen_refolding).

    Returns:
        List of (batch_id, execution_id) pairs for tracking.
    """
    candidates = [
        d for d in designs
        if d.get("sequence")
        and not d.get("refolding")
        # BoltzGen designs get promoted, not refolded
        and d.get("tool") != "boltzgen"
    ]
    if not candidates:
        print("  No designs need refolding (all refolded or promoted)")
        return []

    # Ensure PGDH target CIF is available on S3 for the refolding YAMLs
    target_key = "input/boltzgen/2GDZ.cif"
    existing_target = client.list_files(target_key)
    if not existing_target:
        print(f"  WARNING: {target_key} not found on S3. Upload it before jobs run.")

    print(f"  {len(candidates)} designs to refold")

    # Upload all refolding YAMLs
    design_ids = []
    for d in candidates:
        design_id = d["design_id"]
        yaml_content = _generate_refold_yaml(design_id, d["sequence"])
        yaml_key = f"input/boltzgen/refold_{design_id}.yaml"
        client.upload_bytes(yaml_content.encode(), yaml_key)
        design_ids.append(design_id)

    # Generate and upload a batch script that processes each YAML
    batch_lines = ["#!/bin/bash", "set -e", ""]
    for design_id in design_ids:
        batch_lines.append(f'echo "=== Refolding {design_id} ==="')
        batch_lines.append(
            f"bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh"
            f" --input-yaml /root/boltzgen_work/refold_{design_id}.yaml"
            f" --output-dir /mnt/s3/output/refolding/{design_id}/"
            f" --steps folding"
            f" --cache /mnt/s3/models/boltzgen"
        )
        batch_lines.append("")
    batch_lines.append('echo "=== Batch refolding complete ==="')

    batch_script = "\n".join(batch_lines)
    client.upload_bytes(batch_script.encode(), "input/boltzgen/batch_refold.sh")

    # ~30s per design for folding, plus ~120s setup overhead
    timeout = 120 + len(candidates) * 40
    cmd = "bash /mnt/s3/input/boltzgen/batch_refold.sh"

    try:
        exec_id, _ = client.submit_docker_job(
            docker_image="pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime",
            command=cmd,
            execution_type="gpu.a100",
            timeout=timeout,
        )
        print(f"  Submitted batch refolding job: {exec_id}")
        print(f"  {len(candidates)} designs, timeout={timeout}s")
        return [("batch_refold", exec_id)]
    except Exception as e:
        print(f"  Failed to submit batch job: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════
# VALIDATE — Boltz-2 cross-validation (--slow)
# ══════════════════════════════════════════════════════════════════════════

def _generate_boltz2_yaml(design_id: str, binder_sequence: str) -> str:
    """Generate a Boltz-2 YAML for predicting the binder+PGDH complex.

    NOTE: Do NOT use the msa: field with pre-computed CSVs — Boltz-2 loses
    TaxID/pairing info from cached MSAs, degrading ipTM by ~40%
    (see github.com/jwohlwend/boltz/issues/627). Always use --use-msa-server.
    """
    return (
        f"# Boltz-2 validation for {design_id}\n"
        f"sequences:\n"
        f"  - protein:\n"
        f"      id: A\n"
        f"      sequence: {PGDH_SEQUENCE}\n"
        f"  - protein:\n"
        f"      id: B\n"
        f"      sequence: {binder_sequence}\n"
    )


def submit_boltz2_validation_jobs(client: LyceumClient, designs: list[dict]) -> list[tuple[str, str]]:
    """Submit a single Boltz-2 batch job to validate all designs needing validation.

    Uploads all validation YAMLs, generates a batch script that processes each
    sequentially, and submits one Docker job.

    Returns:
        List of (batch_id, execution_id) pairs for tracking.
    """
    candidates = [
        d for d in designs
        if d.get("sequence")
        and not d.get("validation")
    ]
    if not candidates:
        print("  No designs need Boltz-2 validation (all validated or missing sequence)")
        return []

    print(f"  {len(candidates)} designs to validate with Boltz-2")

    # Upload all validation YAMLs
    design_ids = []
    for d in candidates:
        design_id = d["design_id"]
        yaml_content = _generate_boltz2_yaml(design_id, d["sequence"])
        yaml_key = f"input/boltz2/validate_{design_id}.yaml"
        client.upload_bytes(yaml_content.encode(), yaml_key)
        design_ids.append(design_id)

    # Lyceum max timeout is 600s but jobs run to completion regardless.
    # Split into 2-3 batch jobs to avoid queueing issues and allow parallelism.
    MAX_JOBS = 3
    chunk_size = max(1, (len(candidates) + MAX_JOBS - 1) // MAX_JOBS)
    chunks = [candidates[i:i + chunk_size] for i in range(0, len(candidates), chunk_size)]

    results = []
    for ci, chunk in enumerate(chunks):
        chunk_ids = [d["design_id"] for d in chunk]
        batch_lines = ["#!/bin/bash", "set -e", ""]
        for design_id in chunk_ids:
            batch_lines.append(f'echo "=== Validating {design_id} ==="')
            batch_lines.append(
                f"bash /mnt/s3/scripts/boltz2/run_boltz2.sh"
                f" --input-yaml /root/boltz2_work/validate_{design_id}.yaml"
                f" --output-dir /mnt/s3/output/boltz2/{design_id}"
                f" --recycling-steps 10"
                f" --diffusion-samples 5"
                f" --cache /mnt/s3/models/boltz2"
                f" --use-msa-server"
                f" --write-full-pae"
            )
            batch_lines.append("")
        batch_lines.append('echo "=== Batch validation complete ==="')

        script_key = f"input/boltz2/batch_validate_{ci}.sh"
        client.upload_bytes("\n".join(batch_lines).encode(), script_key)

        try:
            exec_id, _ = client.submit_docker_job(
                docker_image="pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime",
                command=f"bash /mnt/s3/{script_key}",
                execution_type="gpu.a100",
                timeout=600,
            )
            print(f"  Batch {ci+1}/{len(chunks)}: {len(chunk)} designs -> {exec_id}")
            results.append((f"batch_validate_{ci}", exec_id))
        except Exception as e:
            print(f"  Failed to submit batch {ci+1}: {e}")

    return results


# ══════════════════════════════════════════════════════════════════════════
# SCORE — submit ipSAE jobs (--score)
# ══════════════════════════════════════════════════════════════════════════

def submit_scoring_jobs(client: LyceumClient, designs: list[dict]) -> int:
    """Submit ipSAE scoring for validated designs without scores."""
    candidates = [
        d for d in designs
        if d.get("validation") and not d.get("scoring")
    ]
    if not candidates:
        print("  No designs need scoring (all scored or not yet validated)")
        return 0

    print(f"  {len(candidates)} designs to score")
    print(f"  NOTE: ipSAE job submission not yet implemented in this script.")
    print(f"  Use the /pgdh_ipsae skill to submit manually.")
    return 0


# ══════════════════════════════════════════════════════════════════════════
# INTERFACE — PyRosetta interface scoring (--interface)
# ══════════════════════════════════════════════════════════════════════════

def submit_pyrosetta_jobs(client: LyceumClient, designs: list[dict]) -> list[tuple[str, str]]:
    """Submit PyRosetta interface scoring jobs for designs with structures on S3.

    Looks for designer-predicted complex CIFs in designs/<tool>/<id>/designed.cif
    (or the raw output location). Uploads them to input/pyrosetta/ and submits
    a batch CPU Docker job.

    Returns:
        List of (design_id, execution_id) pairs for tracking.
    """
    candidates = [
        d for d in designs
        if not d.get("interface_metrics")
    ]
    if not candidates:
        print("  No designs need interface scoring (all scored)")
        return []

    # Find CIF files on S3 for each candidate
    to_score = []
    for d in candidates:
        did = d["design_id"]
        tool = d.get("tool", "unknown")

        # Check designs/<tool>/<did>/designed.cif (the standardised location)
        cif_key = f"designs/{tool}/{did}/designed.cif"
        existing = client.list_files(cif_key)
        if existing:
            to_score.append((did, cif_key))
            continue

        # Check for .cif.gz
        cif_gz_key = f"designs/{tool}/{did}/designed.cif.gz"
        existing = client.list_files(cif_gz_key)
        if existing:
            to_score.append((did, cif_gz_key))
            continue

        # Check raw output location from source_files
        sf = d.get("source_files") or {}
        raw_cif = sf.get("structure")
        if raw_cif:
            existing = client.list_files(raw_cif)
            if existing:
                to_score.append((did, raw_cif))
                continue

    if not to_score:
        print("  No designs have CIF structures on S3 for interface scoring")
        return []

    print(f"  {len(to_score)} designs with structures available for interface scoring")

    # Copy CIFs to input/pyrosetta/ with design_id as filename
    for did, cif_key in to_score:
        # Download from source and re-upload to input/pyrosetta/
        try:
            cif_data = client.download_bytes(cif_key)

            # Handle gzipped CIFs
            if cif_key.endswith(".cif.gz"):
                import gzip
                cif_data = gzip.decompress(cif_data)

            dest_key = f"input/pyrosetta/{did}.cif"
            client.upload_bytes(cif_data, dest_key)
        except Exception as e:
            print(f"    Warning: could not copy CIF for {did}: {e}")

    # Submit single batch Docker job (CPU)
    cmd = (
        "bash /mnt/s3/scripts/pyrosetta/run_pyrosetta.sh"
        " --input-dir /root/pyrosetta_work"
        " --output-dir /mnt/s3/output/pyrosetta"
    )

    submitted = []
    try:
        exec_id, _ = client.submit_docker_job(
            docker_image="python:3.11-slim",
            command=cmd,
            execution_type="cpu",
            timeout=600,  # Max allowed by Lyceum; for large batches, split into chunks
        )
        submitted.append(("batch_pyrosetta", exec_id))
        print(f"  Submitted PyRosetta batch scoring job: {exec_id}")
        print(f"  Scoring {len(to_score)} designs (CPU, ~1-2 min each)")
    except Exception as e:
        print(f"  Failed to submit PyRosetta job: {e}")

    return submitted


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def run_evaluation(client: LyceumClient = None, fast: bool = False,
                   slow: bool = False, slow_ids: list[str] | None = None,
                   auto_slow: bool = False, score: bool = False,
                   interface: bool = False,
                   round_num: int | None = None,
                   force: bool = False,
                   extra_designs: list[dict] | None = None) -> list[dict]:
    """Run sync + submit evaluation jobs. Returns ranked designs.

    Calls sync_all() first, which filters out archived designs (see archived_designs.txt).

    Args:
        client: LyceumClient instance (created if None).
        fast: If True, promote BoltzGen self-consistency + submit refolding for RFD3.
        slow: If True, submit Boltz-2 cross-validation jobs.
        slow_ids: Specific design IDs to validate (with --slow).
        auto_slow: If True, auto-select designs with refolding RMSD < threshold.
        score: If True, submit ipSAE scoring jobs.
        interface: If True, submit PyRosetta interface scoring jobs (CPU).
        round_num: If set, only evaluate designs from this round number.
        extra_designs: Additional designs to inject (e.g. custom FASTA uploads).
    """
    if client is None:
        client = LyceumClient()

    # Step 1: Sync all designs (collect, attach scores, rank, write to S3)
    designs = sync_all(client=client, extra_designs=extra_designs, force=force)

    # Step 1.5: Filter by round if specified
    if round_num is not None:
        before = len(designs)
        designs = [d for d in designs if d.get("round") == round_num]
        print(f"  Filtered to round {round_num}: {len(designs)} designs (of {before} total)")

    # Step 2: Submit GPU jobs based on flags
    if fast:
        print("--- Fast eval: BoltzGen designability check ---")
        n_promoted = promote_boltzgen_refolding(designs)
        print(f"  Promoted {n_promoted} BoltzGen designs (self-consistency -> refolding)")
        submit_refold_jobs(client, designs)
        print()

    if slow:
        print("--- Slow eval: Boltz-2 cross-validation ---")
        if slow_ids:
            # Filter to specified design IDs
            id_set = set(slow_ids)
            filtered = [d for d in designs if d["design_id"] in id_set]
            missing = id_set - {d["design_id"] for d in filtered}
            if missing:
                print(f"  WARNING: design IDs not found: {', '.join(sorted(missing))}")
            submit_boltz2_validation_jobs(client, filtered)
        elif auto_slow:
            # Auto-select: designs with good refolding RMSD and no validation yet
            auto_candidates = []
            for d in designs:
                if d.get("validation"):
                    continue
                refold = d.get("refolding") or {}
                rmsd = refold.get("boltzgen_rmsd")
                if rmsd is not None:
                    try:
                        if float(rmsd) < FAST_EVAL_RMSD_THRESHOLD:
                            auto_candidates.append(d)
                    except (ValueError, TypeError):
                        pass
            print(f"  Auto-selected {len(auto_candidates)} designs with refolding RMSD < {FAST_EVAL_RMSD_THRESHOLD}A")
            submit_boltz2_validation_jobs(client, auto_candidates)
        else:
            # No IDs and no --auto: validate all unvalidated
            submit_boltz2_validation_jobs(client, designs)
        print()

    if score:
        print("--- Submit scoring jobs (ipSAE) ---")
        submit_scoring_jobs(client, designs)
        print()

    if interface:
        print("--- Submit PyRosetta interface scoring (CPU) ---")
        submit_pyrosetta_jobs(client, designs)
        print()

    if fast or slow or score or interface:
        print("Jobs submitted. Run sync_designs.py again after jobs complete to pick up results.")

    return designs


def main():
    parser = argparse.ArgumentParser(
        description="Submit GPU evaluation jobs for PGDH binder designs",
        epilog=(
            "Examples:\n"
            "  python pgdh_campaign/evaluate_designs.py --fast\n"
            "  python pgdh_campaign/evaluate_designs.py --slow design_id_1 design_id_2\n"
            "  python pgdh_campaign/evaluate_designs.py --slow --auto\n"
            "  python pgdh_campaign/evaluate_designs.py --fast --score\n"
            "\n"
            "After jobs complete, run sync_designs.py to pick up results:\n"
            "  python pgdh_campaign/sync_designs.py"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fast", action="store_true",
                        help="Fast eval: promote BoltzGen self-consistency + submit BoltzGen refolding for RFD3 designs")
    parser.add_argument("--slow", nargs="*", default=None, metavar="DESIGN_ID",
                        help="Slow eval: submit Boltz-2 cross-validation. Optionally pass design IDs to validate specific designs.")
    parser.add_argument("--auto", action="store_true",
                        help=f"With --slow: auto-select designs with refolding RMSD < {FAST_EVAL_RMSD_THRESHOLD}A")
    parser.add_argument("--score", action="store_true",
                        help="Submit ipSAE scoring jobs for validated designs")
    parser.add_argument("--interface", action="store_true",
                        help="Submit PyRosetta interface scoring (CPU) for designs with structures")
    parser.add_argument("--round", type=int, default=None,
                        help="Only evaluate designs from this round (filters by round number)")
    parser.add_argument("--force", action="store_true",
                        help="Force re-promote refolding data (overwrite existing)")
    args = parser.parse_args()

    slow = args.slow is not None  # --slow was passed (even without IDs)
    slow_ids = args.slow if args.slow else None  # List of IDs, or None

    if args.auto and not slow:
        print("Error: --auto requires --slow")
        sys.exit(1)

    if not (args.fast or slow or args.score or args.interface):
        print("No flags specified. Use --fast, --slow, --score, and/or --interface.")
        print("To just sync designs (no GPU), use: python pgdh_campaign/sync_designs.py")
        parser.print_help()
        sys.exit(1)

    run_evaluation(fast=args.fast, slow=slow, slow_ids=slow_ids,
                   auto_slow=args.auto, score=args.score, interface=args.interface,
                   round_num=getattr(args, 'round'), force=args.force)


if __name__ == "__main__":
    main()
