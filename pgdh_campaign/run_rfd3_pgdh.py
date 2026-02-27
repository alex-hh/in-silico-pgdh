"""Submit RFdiffusion3 PGDH binder design job to Lyceum."""

import sys
sys.path.insert(0, "../projects/biolyceum/src")

from pathlib import Path
from utils.client import LyceumClient


def main():
    client = LyceumClient()

    # Upload input files
    print("Uploading RFD3 inputs...")
    client.upload_file(
        "configs/rfd3_pgdh_binder.json",
        "input/rfdiffusion3/rfd3_pgdh_binder.json",
    )
    client.upload_file(
        "structures/2GDZ.pdb",
        "input/rfdiffusion3/2GDZ.pdb",
    )

    # Upload the entrypoint script
    client.upload_file(
        "../projects/biolyceum/src/lyceum_rfdiffusion3.py",
        "scripts/rfdiffusion3/lyceum_rfdiffusion3.py",
    )

    # Build command: run entrypoint inside foundry container
    cmd = [
        "python", "/mnt/s3/scripts/rfdiffusion3/lyceum_rfdiffusion3.py",
        "--input-json", "/mnt/s3/input/rfdiffusion3/rfd3_pgdh_binder.json",
        "--output-dir", "/mnt/s3/output/rfdiffusion3",
        "--num-designs", "4",
        "--num-batches", "1",
        "--step-scale", "3",
        "--gamma-0", "0.2",
    ]

    print("Submitting RFD3 Docker job...")
    exec_id, stream_url = client.submit_docker_job(
        docker_image="rosettacommons/foundry",
        command=cmd,
        execution_type="gpu.a100",
        timeout=600,
    )
    print(f"  execution_id: {exec_id}")

    # Stream output
    success, output = client.stream_output(exec_id, stream_url)
    if not success:
        print("RFD3 execution failed!")
        return

    print("\nRFD3 execution completed!")

    # Download results
    out_dir = Path("out/rfd3")
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = client.download_prefix("output/rfdiffusion3/", str(out_dir))
    print(f"Downloaded {len(downloaded)} output files to {out_dir}")


if __name__ == "__main__":
    main()
