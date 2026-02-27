#!/bin/bash
set -e

# ── Persistent cache on S3 ──
CACHE_DIR="/mnt/s3/models/boltzgen"

# ── Skip setup if boltzgen is already installed (custom Docker image) ──
if command -v boltzgen &> /dev/null; then
    echo "=== BoltzGen already installed (custom image) ==="
else
    echo "=== Installing BoltzGen (generic image) ==="
    PIP_CACHE="/mnt/s3/pip_cache/boltzgen"
    REPO_CACHE="/mnt/s3/boltzgen_repo"
    BOLTZGEN_COMMIT="247b9bbd8b68a60aba854c2968d6a0cddd21ad6d"

    apt-get update -qq > /dev/null 2>&1
    apt-get install -y -qq git build-essential > /dev/null 2>&1

    if [ -d "$REPO_CACHE/src" ]; then
        echo "  Using cached repo"
        cp -r "$REPO_CACHE" /root/boltzgen
    else
        echo "  Cloning BoltzGen (will cache for next run)"
        git clone -q https://github.com/HannesStark/boltzgen /root/boltzgen
        cd /root/boltzgen && git checkout -q "$BOLTZGEN_COMMIT"
        cp -r /root/boltzgen "$REPO_CACHE"
    fi

    mkdir -p "$PIP_CACHE"
    pip install --cache-dir "$PIP_CACHE" -e /root/boltzgen 2>&1 | tail -3
fi

# ── Download models on first run ──
if [ ! -f "$CACHE_DIR/.installed" ]; then
    echo "=== Downloading BoltzGen models (first run only) ==="
    mkdir -p "$CACHE_DIR"
    boltzgen download all --cache "$CACHE_DIR"
    touch "$CACHE_DIR/.installed"
fi

# ── Copy input files to working directory ──
WORK_DIR="/root/boltzgen_work"
mkdir -p "$WORK_DIR"
cp /mnt/s3/input/boltzgen/* "$WORK_DIR/" 2>/dev/null || true
cd "$WORK_DIR"

# ── Run BoltzGen ──
echo "=== Running BoltzGen ==="
python /mnt/s3/scripts/boltzgen/lyceum_boltzgen.py "$@"
