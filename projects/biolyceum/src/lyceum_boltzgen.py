"""BoltzGen — Lyceum version.

All-atom protein design using the BoltzGen diffusion model.
https://github.com/HannesStark/boltzgen

Example yaml file:
```yaml
entities:
  - protein:
      id: B
      sequence: 80..140
  - file:
      path: 6m1u.cif
      include:
        - chain:
            id: A
```

Available protocols: protein-anything, peptide-anything, protein-small_molecule, nanobody-anything

This script is designed to run inside a Docker execution on Lyceum with
boltzgen pre-installed. Input files are read from /mnt/s3/input/boltzgen/
and outputs are written to /mnt/s3/output/boltzgen/.

Usage on Lyceum (via Docker execution):
    See client.py submit_boltzgen_job() for the Docker submission pattern.
"""

import argparse
import re
from pathlib import Path
from subprocess import run


STORAGE = Path("/mnt/s3")
INPUT_DIR = STORAGE / "input" / "boltzgen"
OUTPUT_DIR = STORAGE / "output" / "boltzgen"


def boltzgen_run(yaml_path, output_dir, protocol="protein-anything",
                 num_designs=10, steps=None, cache=None, devices=None,
                 extra_args=None):
    """Run BoltzGen on a yaml specification.

    Args:
        yaml_path: Path to YAML design specification.
        output_dir: Directory to write results to.
        protocol: Design protocol.
        num_designs: Number of designs to generate.
        steps: Specific pipeline steps to run (e.g. "design inverse_folding").
        cache: Custom cache directory path.
        devices: Number of GPUs to use.
        extra_args: Additional CLI arguments as string.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "boltzgen",
        "run",
        str(yaml_path),
        "--output",
        str(output_dir),
        "--protocol",
        protocol,
        "--num_designs",
        str(num_designs),
    ]

    if steps:
        cmd.extend(["--steps"] + steps.split())
    if cache:
        cmd.extend(["--cache", cache])
    if devices:
        cmd.extend(["--devices", str(devices)])
    if extra_args:
        cmd.extend(extra_args.split())

    print(f"Running: {' '.join(cmd)}")
    result = run(cmd)
    if result.returncode != 0:
        print(f"Warning: boltzgen exited with code {result.returncode}")

    output_files = list(output_dir.rglob("*"))
    output_files = [f for f in output_files if f.is_file()]
    print(f"Output files: {[str(f) for f in output_files]}")
    return output_files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BoltzGen all-atom protein design")
    parser.add_argument("--input-yaml", required=True, help="Path to YAML design spec")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory")
    parser.add_argument("--protocol", default="protein-anything",
                        help="Design protocol (protein-anything, peptide-anything, protein-small_molecule, nanobody-anything)")
    parser.add_argument("--num-designs", type=int, default=10, help="Number of designs")
    parser.add_argument("--steps", default=None, help="Pipeline steps (e.g. 'design inverse_folding folding')")
    parser.add_argument("--cache", default=None, help="Custom cache directory")
    parser.add_argument("--devices", type=int, default=None, help="Number of GPUs")
    parser.add_argument("--extra-args", default=None, help="Additional CLI arguments")
    args = parser.parse_args()

    boltzgen_run(
        yaml_path=args.input_yaml,
        output_dir=args.output_dir,
        protocol=args.protocol,
        num_designs=args.num_designs,
        steps=args.steps,
        cache=args.cache,
        devices=args.devices,
        extra_args=args.extra_args,
    )
