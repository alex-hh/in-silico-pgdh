"""PyRosetta Interface Scoring — Lyceum version.

Scores binder–target protein complexes using PyRosetta interface analysis.
Computes binding energy, shape complementarity, buried SASA, H-bonds, packing,
secondary structure, and clashes — all CPU-only, no GPU needed.

Scoring functions extracted from BindCraft (bindcraft.functions).

Input:  /mnt/s3/input/pyrosetta/<design_id>.cif (or .pdb) — binder+target complex
Output: /mnt/s3/output/pyrosetta/<design_id>/interface_metrics.json

Usage on Lyceum (via Docker execution):
    See client.py run_pyrosetta_scoring() for the Docker submission pattern.
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import numpy as np

STORAGE = Path("/mnt/s3")
INPUT_DIR = STORAGE / "input" / "pyrosetta"
OUTPUT_DIR = STORAGE / "output" / "pyrosetta"


def init_pyrosetta():
    """Initialise PyRosetta with BindCraft-compatible flags."""
    import pyrosetta as pr

    # Find DAlphaBall binary
    dalphaball_path = os.environ.get("DALPHABALL_BIN", "/usr/local/bin/DAlphaBall.gcc")
    if not Path(dalphaball_path).exists():
        # Fallback: check common locations
        for candidate in ["/root/DAlphaBall.gcc", "/opt/DAlphaBall.gcc"]:
            if Path(candidate).exists():
                dalphaball_path = candidate
                break

    flags = (
        f"-ignore_unrecognized_res"
        f" -ignore_zero_occupancy"
        f" -mute all"
        f" -corrections::beta_nov16 true"
        f" -relax:default_repeats 1"
    )
    if Path(dalphaball_path).exists():
        flags += f" -holes:dalphaball {dalphaball_path}"
    else:
        print("WARNING: DAlphaBall not found — packstat will be unavailable")

    pr.init(flags)
    return pr


def _clean_cif_zero_coords(path):
    """Remove atoms with (0,0,0) coordinates from a CIF file.

    BoltzGen CIFs sometimes have sidechain atoms with placeholder zero
    coordinates, which cause PyRosetta to crash with 'zero-length vector' errors.
    Returns path to a cleaned temp file, or the original if no cleaning needed.
    """
    import tempfile

    with open(path) as f:
        lines = f.readlines()

    cleaned = []
    removed = 0
    for line in lines:
        if line.startswith("ATOM") or line.startswith("HETATM"):
            # CIF atom records have coords as whitespace-separated fields
            parts = line.split()
            # In mmCIF _atom_site format, coords are typically at indices ~10,11,12
            # but positions vary; check for triple-zero pattern
            try:
                # Find three consecutive "0" or "0.0" or "0.000" fields
                coord_fields = [p for p in parts if p.replace(".", "").replace("-", "").isdigit()]
                # Look for (0,0,0) pattern — check last numeric-looking fields
                if len(coord_fields) >= 3:
                    # Coords are usually fields [10], [11], [12] in mmCIF
                    for i in range(len(parts) - 2):
                        try:
                            x, y, z = float(parts[i]), float(parts[i+1]), float(parts[i+2])
                            if x == 0.0 and y == 0.0 and z == 0.0 and i >= 5:
                                removed += 1
                                line = None
                                break
                        except (ValueError, IndexError):
                            continue
            except Exception:
                pass
        if line is not None:
            cleaned.append(line)

    if removed == 0:
        return path

    print(f"  Removed {removed} atoms with (0,0,0) coordinates")
    tmp = tempfile.NamedTemporaryFile(suffix=".cif", delete=False, mode="w")
    tmp.writelines(cleaned)
    tmp.close()
    return tmp.name


def load_structure(pr, path):
    """Load a PDB or CIF file into a PyRosetta pose.

    Handles CIF files with zero-coordinate atoms (common in BoltzGen output)
    by cleaning them before loading.
    """
    path = str(path)

    # Clean zero-coord atoms from CIF files
    load_path = path
    if path.endswith(".cif"):
        load_path = _clean_cif_zero_coords(path)

    try:
        pose = pr.pose_from_file(load_path)
    except Exception as e:
        if not path.endswith(".cif"):
            raise
        # Fallback: convert CIF to PDB with biopython, then load
        print(f"  pose_from_file failed ({e}), converting via biopython...")
        from Bio.PDB import MMCIFParser, PDBIO
        import tempfile
        parser = MMCIFParser(QUIET=True)
        structure = parser.get_structure("complex", load_path)
        pdb_io = PDBIO()
        pdb_io.set_structure(structure)
        with tempfile.NamedTemporaryFile(suffix=".pdb", delete=False) as tmp:
            tmp_path = tmp.name
        pdb_io.save(tmp_path)
        pose = pr.pose_from_pdb(tmp_path)
        os.unlink(tmp_path)
    finally:
        # Clean up temp file if we created one
        if load_path != path and os.path.exists(load_path):
            os.unlink(load_path)

    return pose


def detect_binder_chain(pose):
    """Auto-detect binder chain as the shorter chain in the complex."""
    from pyrosetta.rosetta.core.pose import chain_end_res

    chains = {}
    for i in range(1, pose.num_chains() + 1):
        chain_id = pose.pdb_info().chain(pose.chain_begin(i))
        chain_len = pose.chain_end(i) - pose.chain_begin(i) + 1
        chains[chain_id] = chain_len

    if len(chains) < 2:
        raise ValueError(f"Expected multi-chain complex, got chains: {list(chains.keys())}")

    # Binder = shorter chain
    binder = min(chains, key=chains.get)
    target = max(chains, key=chains.get)
    print(f"  Detected chains: {chains} → binder={binder}, target={target}")
    return binder


def relax_structure(pr, pose):
    """FastRelax with 1 repeat — lightweight pre-scoring relaxation."""
    from pyrosetta.rosetta.core.kinematics import MoveMap
    from pyrosetta.rosetta.protocols.relax import FastRelax

    scorefxn = pr.get_fa_scorefxn()

    movemap = MoveMap()
    movemap.set_bb(True)
    movemap.set_chi(True)

    relax = FastRelax(scorefxn, 1)  # 1 repeat (fast)
    relax.set_movemap(movemap)
    relax.min_type("lbfgs_armijo_nonmonotone")

    print("  Running FastRelax (1 repeat)...")
    relax.apply(pose)
    return pose


def score_interface(pr, pose, binder_chain="B"):
    """Score protein–protein interface using InterfaceAnalyzerMover.

    Replicates BindCraft's score_interface() function.

    Returns:
        dict with all interface metrics.
    """
    from pyrosetta.rosetta.protocols.analysis import InterfaceAnalyzerMover
    from pyrosetta.rosetta.core.select.residue_selector import (
        ChainSelector,
        NeighborhoodResidueSelector,
        AndResidueSelector,
    )

    scorefxn = pr.get_fa_scorefxn()

    # Determine chain letters for interface string
    chains_in_pose = []
    for i in range(1, pose.num_chains() + 1):
        ch = pose.pdb_info().chain(pose.chain_begin(i))
        if ch not in chains_in_pose:
            chains_in_pose.append(ch)

    # Build interface string: binder vs everything else
    other_chains = [c for c in chains_in_pose if c != binder_chain]
    interface_str = "".join(other_chains) + "_" + binder_chain

    # InterfaceAnalyzerMover
    iam = InterfaceAnalyzerMover()
    iam.set_interface(interface_str)
    iam.set_scorefunction(scorefxn)
    iam.set_pack_separated(True)
    iam.set_compute_interface_energy(True)
    try:
        iam.set_pack_rounds(5)
        iam.set_packstat(True)
    except Exception:
        pass  # Some PyRosetta versions lack these

    iam.apply(pose)

    # Extract metrics
    data = iam.get_all_data()

    interface_dG = iam.get_interface_dG()
    interface_dSASA = iam.get_interface_delta_sasa()

    # Shape complementarity
    try:
        interface_sc = data.sc_value
    except AttributeError:
        interface_sc = 0.0

    # Packing
    try:
        interface_packstat = iam.get_interface_packstat()
    except Exception:
        interface_packstat = 0.0

    # Interface residues and contacts
    try:
        interface_nres = data.interface_nres[2]  # total interface residues
    except (AttributeError, IndexError):
        interface_nres = 0

    # H-bonds
    try:
        interface_hbonds = data.interface_hbonds
    except AttributeError:
        interface_hbonds = 0

    # Separated energies for binder score
    try:
        separated_energies = data.complexed_interface_score
        binder_score = separated_energies[2] if hasattr(separated_energies, '__getitem__') else 0.0
    except (AttributeError, IndexError):
        binder_score = 0.0

    # Compute binder score from chain energy
    try:
        binder_sel = ChainSelector(binder_chain)
        binder_vec = binder_sel.apply(pose)
        binder_score_alt = 0.0
        for i in range(1, pose.size() + 1):
            if binder_vec[i]:
                binder_score_alt += pose.energies().residue_total_energy(i)
        if binder_score == 0.0:
            binder_score = binder_score_alt
    except Exception:
        pass

    # SASA-based metrics
    interface_dG_SASA_ratio = interface_dG / interface_dSASA if interface_dSASA > 0 else 0.0

    # Interface fraction (% of binder surface buried)
    try:
        complexed_sasa = data.complexed_sasa
        separated_sasa = data.separated_sasa
        if separated_sasa > 0:
            interface_fraction = interface_dSASA / separated_sasa
        else:
            interface_fraction = 0.0
    except AttributeError:
        interface_fraction = 0.0

    # Interface hydrophobicity
    try:
        interface_hydrophobicity = data.interface_hydrophobic_fraction
    except AttributeError:
        interface_hydrophobicity = 0.0

    # Unsatisfied H-bonds
    try:
        from pyrosetta.rosetta.protocols.simple_filters import BuriedUnsatHbondFilter
        buhs_filter = BuriedUnsatHbondFilter()
        buhs_filter.set_jump_number(1)
        interface_delta_unsat_hbonds = buhs_filter.compute(pose)
    except Exception:
        interface_delta_unsat_hbonds = 0

    # Surface hydrophobicity of binder
    surface_hydrophobicity = calc_surface_hydrophobicity(pr, pose, binder_chain)

    metrics = {
        "interface_dG": round(float(interface_dG), 3),
        "interface_dSASA": round(float(interface_dSASA), 1),
        "interface_sc": round(float(interface_sc), 4),
        "interface_packstat": round(float(interface_packstat), 4),
        "interface_nres": int(interface_nres),
        "interface_hbonds": int(interface_hbonds),
        "interface_delta_unsat_hbonds": int(interface_delta_unsat_hbonds),
        "interface_dG_SASA_ratio": round(float(interface_dG_SASA_ratio), 5),
        "interface_fraction": round(float(interface_fraction), 4),
        "interface_hydrophobicity": round(float(interface_hydrophobicity), 4),
        "surface_hydrophobicity": round(float(surface_hydrophobicity), 4),
        "binder_score": round(float(binder_score), 3),
    }
    return metrics


def calc_surface_hydrophobicity(pr, pose, binder_chain):
    """Calculate hydrophobic fraction on binder surface using LayerSelector."""
    try:
        from pyrosetta.rosetta.core.select.residue_selector import (
            ChainSelector,
            AndResidueSelector,
            LayerSelector,
        )

        binder_sel = ChainSelector(binder_chain)
        surface_sel = LayerSelector()
        surface_sel.set_layers(False, False, True)  # surface only

        combined = AndResidueSelector(binder_sel, surface_sel)
        surface_vec = combined.apply(pose)

        hydrophobic_aas = set("AILMFWVP")
        n_surface = 0
        n_hydrophobic = 0
        for i in range(1, pose.size() + 1):
            if surface_vec[i]:
                n_surface += 1
                aa = pose.residue(i).name1()
                if aa in hydrophobic_aas:
                    n_hydrophobic += 1

        return n_hydrophobic / n_surface if n_surface > 0 else 0.0
    except Exception:
        return 0.0


def calc_ss_percentage(pr, pose, binder_chain):
    """Calculate secondary structure fractions using DSSP.

    Returns dict with helix/sheet/loop percentages for binder and interface.
    """
    from pyrosetta.rosetta.protocols.moves import DsspMover
    from pyrosetta.rosetta.core.select.residue_selector import (
        ChainSelector,
        NeighborhoodResidueSelector,
    )

    # Assign DSSP
    dssp = DsspMover()
    dssp.apply(pose)

    # Get binder residue indices
    binder_sel = ChainSelector(binder_chain)
    binder_vec = binder_sel.apply(pose)

    # Get interface residues (binder residues near target)
    nbr_sel = NeighborhoodResidueSelector(binder_sel, 8.0, False)
    interface_vec = nbr_sel.apply(pose)

    def count_ss(residue_mask):
        helix = sheet = loop = 0
        for i in range(1, pose.size() + 1):
            if not residue_mask[i]:
                continue
            ss = pose.secstruct(i)
            if ss == "H":
                helix += 1
            elif ss == "E":
                sheet += 1
            else:
                loop += 1
        total = helix + sheet + loop
        if total == 0:
            return 0.0, 0.0, 0.0
        return helix / total, sheet / total, loop / total

    binder_h, binder_e, binder_l = count_ss(binder_vec)

    # Interface SS: binder residues that are at the interface
    from pyrosetta.rosetta.core.select.residue_selector import AndResidueSelector
    interface_binder_sel = AndResidueSelector(binder_sel,
                                              NeighborhoodResidueSelector(
                                                  ChainSelector(binder_chain), 8.0, True))
    # Simpler: just use interface_vec AND binder_vec
    class CombinedMask:
        def __getitem__(self, i):
            return interface_vec[i] and binder_vec[i]
    interface_h, interface_e, interface_l = count_ss(CombinedMask())

    return {
        "binder_helix_fraction": round(binder_h, 4),
        "binder_sheet_fraction": round(binder_e, 4),
        "binder_loop_fraction": round(binder_l, 4),
        "interface_helix_fraction": round(interface_h, 4),
        "interface_sheet_fraction": round(interface_e, 4),
        "interface_loop_fraction": round(interface_l, 4),
    }


def calculate_clash_score(pr, pose):
    """Calculate steric clashes from fa_rep energy term.

    Counts residue pairs with high repulsive energy.
    """
    scorefxn = pr.get_fa_scorefxn()
    scorefxn(pose)  # Score the pose

    from pyrosetta.rosetta.core.scoring import ScoreType
    fa_rep = ScoreType.fa_rep

    clash_count = 0
    threshold = 10.0  # kcal/mol per residue — high fa_rep = clash

    for i in range(1, pose.size() + 1):
        rep_energy = pose.energies().residue_total_energies(i)[fa_rep]
        if rep_energy > threshold:
            clash_count += 1

    return clash_count


def score_single(pr, input_path, output_dir, binder_chain=None, do_relax=True):
    """Score a single complex structure.

    Args:
        pr: Initialised PyRosetta module.
        input_path: Path to CIF/PDB file.
        output_dir: Directory to write interface_metrics.json.
        binder_chain: Chain ID for binder (auto-detect if None).
        do_relax: Whether to FastRelax before scoring.

    Returns:
        dict of all metrics, or None on failure.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    design_id = input_path.stem
    print(f"\n{'='*60}")
    print(f"Scoring: {design_id}")
    print(f"{'='*60}")

    try:
        # Load structure
        print(f"  Loading {input_path.name}...")
        pose = load_structure(pr, input_path)
        print(f"  Loaded: {pose.size()} residues, {pose.num_chains()} chains")

        # Detect binder chain
        if binder_chain is None:
            binder_chain_id = detect_binder_chain(pose)
        else:
            binder_chain_id = binder_chain

        # Optionally relax
        if do_relax:
            pose = relax_structure(pr, pose)

        # Score interface
        print("  Computing interface metrics...")
        metrics = score_interface(pr, pose, binder_chain_id)

        # Secondary structure
        print("  Computing secondary structure...")
        ss_metrics = calc_ss_percentage(pr, pose, binder_chain_id)
        metrics.update(ss_metrics)

        # Clashes
        print("  Computing clashes...")
        metrics["clashes"] = calculate_clash_score(pr, pose)

        # Metadata
        metrics["design_id"] = design_id
        metrics["binder_chain"] = binder_chain_id
        metrics["relaxed"] = do_relax
        metrics["source_file"] = input_path.name

        # Write output
        output_file = output_dir / "interface_metrics.json"
        with open(output_file, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"  Written: {output_file}")

        # Summary
        print(f"  dG={metrics['interface_dG']:.1f}  sc={metrics['interface_sc']:.3f}"
              f"  dSASA={metrics['interface_dSASA']:.0f}  hbonds={metrics['interface_hbonds']}"
              f"  clashes={metrics['clashes']}")

        return metrics

    except Exception as e:
        print(f"  ERROR scoring {design_id}: {e}")
        traceback.print_exc()
        # Write error file so we know it was attempted
        error_file = output_dir / "error.json"
        with open(error_file, "w") as f:
            json.dump({"design_id": design_id, "error": str(e)}, f, indent=2)
        return None


def score_batch(pr, input_dir, output_dir, binder_chain=None, do_relax=True):
    """Score all CIF/PDB files in a directory.

    Args:
        pr: Initialised PyRosetta module.
        input_dir: Directory containing CIF/PDB files.
        output_dir: Base output directory (each design gets a subdirectory).
        binder_chain: Chain ID for binder (auto-detect if None).
        do_relax: Whether to FastRelax before scoring.

    Returns:
        list of metric dicts (None entries for failures).
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)

    files = sorted(input_dir.glob("*.cif")) + sorted(input_dir.glob("*.pdb"))
    if not files:
        print(f"No CIF/PDB files found in {input_dir}")
        return []

    print(f"Found {len(files)} structure files in {input_dir}")
    results = []

    for struct_file in files:
        design_id = struct_file.stem
        sub_output = output_dir / design_id
        metrics = score_single(pr, struct_file, sub_output,
                               binder_chain=binder_chain, do_relax=do_relax)
        results.append(metrics)

    # Summary
    successes = [r for r in results if r is not None]
    failures = len(results) - len(successes)
    print(f"\nBatch complete: {len(successes)} scored, {failures} failed")

    if successes:
        dGs = [r["interface_dG"] for r in successes]
        print(f"  dG range: {min(dGs):.1f} to {max(dGs):.1f} kcal/mol")
        scs = [r["interface_sc"] for r in successes]
        print(f"  SC range: {min(scs):.3f} to {max(scs):.3f}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PyRosetta interface scoring for protein complexes"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", help="Path to single CIF/PDB file")
    group.add_argument("--input-dir", help="Directory of CIF/PDB files (batch mode)")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR),
                        help="Output directory (default: /mnt/s3/output/pyrosetta)")
    parser.add_argument("--binder-chain", default=None,
                        help="Binder chain ID (default: auto-detect shorter chain)")
    parser.add_argument("--no-relax", action="store_true",
                        help="Skip FastRelax before scoring (faster but less accurate)")

    args = parser.parse_args()
    do_relax = not args.no_relax

    # Initialise PyRosetta
    pr = init_pyrosetta()

    if args.input:
        design_id = Path(args.input).stem
        output = Path(args.output_dir) / design_id
        score_single(pr, args.input, output,
                     binder_chain=args.binder_chain, do_relax=do_relax)
    else:
        score_batch(pr, args.input_dir, args.output_dir,
                    binder_chain=args.binder_chain, do_relax=do_relax)
