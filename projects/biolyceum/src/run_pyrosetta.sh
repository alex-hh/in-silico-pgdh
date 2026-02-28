#!/bin/bash
set -e

# ── PyRosetta Interface Scoring ──
# CPU-only — no GPU required.
# Base image: python:3.11-slim

echo "=== PyRosetta Interface Scoring ==="

# ── Persistent pip cache on S3 ──
PIP_CACHE="/mnt/s3/pip_cache/pyrosetta"
mkdir -p "$PIP_CACHE"

# ── Install system deps ──
if ! command -v gcc &> /dev/null; then
    echo "=== Installing system dependencies ==="
    apt-get update -qq > /dev/null 2>&1
    apt-get install -y -qq git gcc g++ build-essential wget > /dev/null 2>&1
fi

# ── Install Python deps ──
if ! python -c "import pyrosetta" 2>/dev/null; then
    echo "=== Installing PyRosetta (this takes a few minutes) ==="

    pip install --cache-dir "$PIP_CACHE" pyrosetta-installer biopython numpy 2>&1 | tail -5

    echo "=== Running pyrosetta_installer ==="
    python -c "import pyrosetta_installer; pyrosetta_installer.install_pyrosetta()" 2>&1 | tail -5

    echo "=== PyRosetta installed ==="
fi

# ── Get DAlphaBall binary ──
DALPHABALL_BIN="/usr/local/bin/DAlphaBall.gcc"
if [ ! -f "$DALPHABALL_BIN" ]; then
    echo "=== Getting DAlphaBall binary ==="
    BINDCRAFT_DIR="/tmp/bindcraft_deps"

    if [ -d "/mnt/s3/models/pyrosetta/DAlphaBall.gcc" ]; then
        cp /mnt/s3/models/pyrosetta/DAlphaBall.gcc "$DALPHABALL_BIN"
    else
        mkdir -p "$BINDCRAFT_DIR"
        # Clone just the binary from BindCraft (sparse checkout)
        cd "$BINDCRAFT_DIR"
        git clone --depth 1 --filter=blob:none --sparse \
            https://github.com/martinpacesa/BindCraft.git 2>&1 | tail -3
        cd BindCraft
        git sparse-checkout set functions 2>&1 | tail -3

        # DAlphaBall is typically at functions/DAlphaBall.gcc
        if [ -f "functions/DAlphaBall.gcc" ]; then
            cp functions/DAlphaBall.gcc "$DALPHABALL_BIN"
        else
            # Try finding it
            FOUND=$(find . -name "DAlphaBall*" -type f | head -1)
            if [ -n "$FOUND" ]; then
                cp "$FOUND" "$DALPHABALL_BIN"
            else
                echo "WARNING: DAlphaBall not found — packstat will be unavailable"
            fi
        fi

        cd /
        rm -rf "$BINDCRAFT_DIR"
    fi

    if [ -f "$DALPHABALL_BIN" ]; then
        chmod +x "$DALPHABALL_BIN"
        # Cache for next run
        mkdir -p /mnt/s3/models/pyrosetta
        cp "$DALPHABALL_BIN" /mnt/s3/models/pyrosetta/DAlphaBall.gcc 2>/dev/null || true
        echo "  DAlphaBall installed at $DALPHABALL_BIN"
    fi
fi

export DALPHABALL_BIN

# ── Copy input files to working directory ──
WORK_DIR="/root/pyrosetta_work"
mkdir -p "$WORK_DIR"
cp /mnt/s3/input/pyrosetta/*.cif "$WORK_DIR/" 2>/dev/null || true
cp /mnt/s3/input/pyrosetta/*.pdb "$WORK_DIR/" 2>/dev/null || true

INPUT_COUNT=$(ls "$WORK_DIR"/*.cif "$WORK_DIR"/*.pdb 2>/dev/null | wc -l)
echo "=== Found $INPUT_COUNT input structures ==="

# ── Run PyRosetta scoring ──
echo "=== Running PyRosetta scoring ==="
python /mnt/s3/scripts/pyrosetta/lyceum_pyrosetta.py "$@"
