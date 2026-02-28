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

After jobs complete, run sync_designs.py to pick up results:
    python pgdh_campaign/sync_designs.py

S3 Data Architecture:

1. output/           Raw tool outputs (written by Lyceum GPU jobs)
2. designs/          ALL designs — source of truth (written ONLY by sync_designs.py)
3. tracker/state.json  Jobs + notes (synced by sync_designs.py, written by dashboard)

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
    """Submit BoltzGen folding jobs for designs that have sequences but no refolding result.

    Skips BoltzGen designs (they get free promotion via promote_boltzgen_refolding).

    Returns:
        List of (design_id, execution_id) pairs for tracking.
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
    submitted = []
    for d in candidates:
        design_id = d["design_id"]
        sequence = d["sequence"]

        # Generate and upload refolding YAML
        yaml_content = _generate_refold_yaml(design_id, sequence)
        yaml_key = f"input/boltzgen/refold_{design_id}.yaml"
        client.upload_bytes(yaml_content.encode(), yaml_key)

        # Submit BoltzGen Docker job with --steps folding
        output_dir = f"output/refolding/{design_id}/"
        cmd = (
            f"bash /mnt/s3/scripts/boltzgen/run_boltzgen.sh"
            f" --input-yaml /root/boltzgen_work/refold_{design_id}.yaml"
            f" --output-dir /mnt/s3/{output_dir}"
            f" --steps folding"
            f" --cache /mnt/s3/models/boltzgen"
        )

        try:
            exec_id, _ = client.submit_docker_job(
                docker_image="pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime",
                command=cmd,
                execution_type="gpu.a100",
                timeout=300,
            )
            submitted.append((design_id, exec_id))
            print(f"    Submitted refolding: {design_id} -> {exec_id}")
        except Exception as e:
            print(f"    Failed to submit {design_id}: {e}")

    print(f"  Submitted {len(submitted)} refolding jobs")
    return submitted


# ══════════════════════════════════════════════════════════════════════════
# VALIDATE — Boltz-2 cross-validation (--slow)
# ══════════════════════════════════════════════════════════════════════════

def _generate_boltz2_yaml(design_id: str, binder_sequence: str) -> str:
    """Generate a Boltz-2 YAML for predicting the binder+PGDH complex."""
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
    """Submit Boltz-2 cross-validation jobs for designs that have sequences but no validation.

    Returns:
        List of (design_id, execution_id) pairs for tracking.
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
    submitted = []

    for d in candidates:
        design_id = d["design_id"]
        sequence = d["sequence"]

        # Generate and upload Boltz-2 YAML
        yaml_content = _generate_boltz2_yaml(design_id, sequence)
        yaml_key = f"input/boltz2/validate_{design_id}.yaml"
        client.upload_bytes(yaml_content.encode(), yaml_key)

        # Submit Boltz-2 Docker job
        cmd = (
            f"bash /mnt/s3/scripts/boltz2/run_boltz2.sh"
            f" --input-yaml /root/boltz2_work/validate_{design_id}.yaml"
            f" --output-dir /mnt/s3/output/boltz2/{design_id}"
            f" --recycling-steps 10"
            f" --diffusion-samples 5"
            f" --cache /mnt/s3/models/boltz2"
            f" --use-msa-server"
        )

        try:
            exec_id, _ = client.submit_docker_job(
                docker_image="pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime",
                command=cmd,
                execution_type="gpu.a100",
                timeout=600,
            )
            submitted.append((design_id, exec_id))
            print(f"    Submitted Boltz-2: {design_id} -> {exec_id}")
        except Exception as e:
            print(f"    Failed to submit {design_id}: {e}")

    print(f"  Submitted {len(submitted)} Boltz-2 validation jobs")
    return submitted


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
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def run_evaluation(client: LyceumClient = None, fast: bool = False,
                   slow: bool = False, slow_ids: list[str] | None = None,
                   auto_slow: bool = False, score: bool = False,
                   extra_designs: list[dict] | None = None) -> list[dict]:
    """Run sync + submit GPU evaluation jobs. Returns ranked designs.

    Callable from CLI or imported by the Streamlit app.

    Args:
        client: LyceumClient instance (created if None).
        fast: If True, promote BoltzGen self-consistency + submit refolding for RFD3.
        slow: If True, submit Boltz-2 cross-validation jobs.
        slow_ids: Specific design IDs to validate (with --slow).
        auto_slow: If True, auto-select designs with refolding RMSD < threshold.
        score: If True, submit ipSAE scoring jobs.
        extra_designs: Additional designs to inject (e.g. custom FASTA uploads).
    """
    if client is None:
        client = LyceumClient()

    # Step 1: Sync all designs (collect, attach scores, rank, write to S3)
    designs = sync_all(client=client, extra_designs=extra_designs)

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

    if fast or slow or score:
        print("GPU jobs submitted. Run sync_designs.py again after jobs complete to pick up results.")

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
    args = parser.parse_args()

    slow = args.slow is not None  # --slow was passed (even without IDs)
    slow_ids = args.slow if args.slow else None  # List of IDs, or None

    if args.auto and not slow:
        print("Error: --auto requires --slow")
        sys.exit(1)

    if not (args.fast or slow or args.score):
        print("No flags specified. Use --fast, --slow, and/or --score.")
        print("To just sync designs (no GPU), use: python pgdh_campaign/sync_designs.py")
        parser.print_help()
        sys.exit(1)

    run_evaluation(fast=args.fast, slow=slow, slow_ids=slow_ids,
                   auto_slow=args.auto, score=args.score)


if __name__ == "__main__":
    main()
