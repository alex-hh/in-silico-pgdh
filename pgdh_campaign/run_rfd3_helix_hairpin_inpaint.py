"""Submit RFdiffusion3 helix-hairpin inpainting jobs to Lyceum."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "../projects/biolyceum/src")
from utils.client import LyceumClient


def build_command(args: argparse.Namespace) -> list[str]:
    cmd = [
        "python",
        "/mnt/s3/scripts/rfdiffusion3/lyceum_rfdiffusion3.py",
        "--input-json",
        "/mnt/s3/input/rfdiffusion3/rfd3_helix_hairpin_inpaint.json",
        "--output-dir",
        args.remote_output_dir,
        "--num-designs",
        str(args.num_designs),
        "--num-batches",
        str(args.num_batches),
        "--num-timesteps",
        str(args.num_timesteps),
        "--step-scale",
        str(args.step_scale),
        "--gamma-0",
        str(args.gamma_0),
    ]

    extra_args: list[str] = ["prevalidate_inputs=true"]
    if args.json_keys_subset:
        extra_args.append(f"json_keys_subset=[{args.json_keys_subset}]")
    if args.extra_args:
        extra_args.extend(args.extra_args)

    if args.dump_trajectories:
        cmd.append("--dump-trajectories")
    if extra_args:
        cmd.extend(["--extra-args", " ".join(extra_args)])
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Submit RFdiffusion3 inpainting job for helix_hairpin_binder."
    )
    parser.add_argument("--num-designs", type=int, default=8)
    parser.add_argument("--num-batches", type=int, default=2)
    parser.add_argument("--num-timesteps", type=int, default=200)
    parser.add_argument("--step-scale", type=float, default=3.0)
    parser.add_argument("--gamma-0", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--execution-type", default="gpu.a100")
    parser.add_argument(
        "--json-keys-subset",
        default=None,
        help="Optional comma-separated task keys (e.g. key1,key2).",
    )
    parser.add_argument(
        "--remote-output-dir",
        default="/mnt/s3/output/rfdiffusion3/helix_hairpin_inpaint",
    )
    parser.add_argument(
        "--download-prefix",
        default="output/rfdiffusion3/helix_hairpin_inpaint/",
    )
    parser.add_argument("--local-out-dir", default="out/rfd3_inpaint")
    parser.add_argument("--dump-trajectories", action="store_true")
    parser.add_argument(
        "--extra-args",
        nargs="*",
        default=[],
        help="Additional rfd3 design key=value overrides.",
    )
    args = parser.parse_args()

    client = LyceumClient()

    print("Uploading inpainting inputs...")
    client.upload_file(
        "configs/rfd3_helix_hairpin_inpaint.json",
        "input/rfdiffusion3/rfd3_helix_hairpin_inpaint.json",
    )
    client.upload_file(
        "structures/helix_hairpin_binder.pdb",
        "input/rfdiffusion3/helix_hairpin_binder.pdb",
    )
    client.upload_file(
        "../projects/biolyceum/src/lyceum_rfdiffusion3.py",
        "scripts/rfdiffusion3/lyceum_rfdiffusion3.py",
    )

    cmd = build_command(args)
    print("Submitting RFD3 inpainting Docker job...")
    exec_id, stream_url = client.submit_docker_job(
        docker_image="rosettacommons/foundry",
        command=cmd,
        execution_type=args.execution_type,
        timeout=args.timeout,
    )
    print(f"  execution_id: {exec_id}")

    success, _ = client.stream_output(exec_id, stream_url)
    if not success:
        raise SystemExit("RFdiffusion3 inpainting run failed.")

    out_dir = Path(args.local_out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = client.download_prefix(args.download_prefix, str(out_dir))
    print(f"Downloaded {len(downloaded)} files to {out_dir}")


if __name__ == "__main__":
    main()
