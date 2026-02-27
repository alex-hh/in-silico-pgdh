"""RFdiffusion3 — Lyceum version.

Atomic-level protein design using RFdiffusion3 from Baker lab.
https://github.com/RosettaCommons/foundry

Designs proteins at the atomic level: binder design, enzyme design,
motif scaffolding, symmetric oligomers, nucleic acid/small molecule binders.

Example JSON config (protein binder):
```json
{
    "my_design": {
        "dialect": 2,
        "infer_ori_strategy": "hotspots",
        "input": "path/to/target.pdb",
        "contig": "40-120,/0,A1-155",
        "select_hotspots": {
            "A64": "CD2,CZ",
            "A88": "CG,CZ"
        },
        "is_non_loopy": true
    }
}
```

This script is designed to run inside the rosettacommons/foundry Docker
container on Lyceum. Input files are read from /mnt/s3/input/rfdiffusion3/
and outputs are written to /mnt/s3/output/rfdiffusion3/.

Usage on Lyceum (via Docker execution):
    Upload JSON config + PDB files, then submit Docker job with
    rosettacommons/foundry image.
"""

import argparse
from pathlib import Path
from subprocess import run


STORAGE = Path("/mnt/s3")
INPUT_DIR = STORAGE / "input" / "rfdiffusion3"
OUTPUT_DIR = STORAGE / "output" / "rfdiffusion3"


def rfdiffusion3_run(input_json, output_dir, num_designs=8, num_batches=1,
                     num_timesteps=200, step_scale=1.5, gamma_0=0.6,
                     dump_trajectories=False, extra_args=None):
    """Run RFdiffusion3 on a JSON config specification.

    Args:
        input_json: Path to JSON design config file.
        output_dir: Directory to write results to.
        num_designs: diffusion_batch_size — number of designs per batch.
        num_batches: Number of batches to run.
        num_timesteps: Number of diffusion timesteps.
        step_scale: Diffusion step scale parameter.
        gamma_0: Diffusion gamma_0 parameter.
        dump_trajectories: Whether to dump diffusion trajectories.
        extra_args: Additional CLI arguments as string.

    Returns:
        List of output file paths.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rfd3", "design",
        f"out_dir={output_dir}",
        f"inputs={input_json}",
        f"diffusion_batch_size={num_designs}",
        f"n_batches={num_batches}",
        f"inference_sampler.num_timesteps={num_timesteps}",
        f"inference_sampler.step_scale={step_scale}",
        f"inference_sampler.gamma_0={gamma_0}",
    ]

    if dump_trajectories:
        cmd.append("dump_trajectories=true")
    if extra_args:
        cmd.extend(extra_args.split())

    print(f"Running: {' '.join(cmd)}")
    result = run(cmd)
    if result.returncode != 0:
        print(f"Warning: rfd3 exited with code {result.returncode}")

    output_files = list(output_dir.rglob("*"))
    output_files = [f for f in output_files if f.is_file()]
    print(f"Output files: {[str(f) for f in output_files]}")
    return output_files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RFdiffusion3 atomic-level protein design")
    parser.add_argument("--input-json", required=True, help="Path to JSON design config")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    parser.add_argument("--num-designs", type=int, default=8, help="Designs per batch (diffusion_batch_size)")
    parser.add_argument("--num-batches", type=int, default=1, help="Number of batches")
    parser.add_argument("--num-timesteps", type=int, default=200, help="Diffusion timesteps")
    parser.add_argument("--step-scale", type=float, default=1.5, help="Diffusion step scale")
    parser.add_argument("--gamma-0", type=float, default=0.6, help="Diffusion gamma_0")
    parser.add_argument("--dump-trajectories", action="store_true", help="Dump diffusion trajectories")
    parser.add_argument("--extra-args", default=None, help="Additional CLI arguments")
    args = parser.parse_args()

    rfdiffusion3_run(
        input_json=args.input_json,
        output_dir=args.output_dir,
        num_designs=args.num_designs,
        num_batches=args.num_batches,
        num_timesteps=args.num_timesteps,
        step_scale=args.step_scale,
        gamma_0=args.gamma_0,
        dump_trajectories=args.dump_trajectories,
        extra_args=args.extra_args,
    )
