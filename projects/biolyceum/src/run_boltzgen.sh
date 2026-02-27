#!/bin/bash
set -e

# Cache directory on persistent storage
CACHE_DIR="/mnt/s3/models/boltzgen"
BOLTZGEN_DIR="/root/boltzgen"
BOLTZGEN_COMMIT="247b9bbd8b68a60aba854c2968d6a0cddd21ad6d"

# Install boltzgen if not cached
if [ ! -f "$CACHE_DIR/.installed" ]; then
    echo "=== Installing BoltzGen ==="
    apt-get update && apt-get install -y git build-essential
    git clone https://github.com/HannesStark/boltzgen "$BOLTZGEN_DIR"
    cd "$BOLTZGEN_DIR"
    git checkout "$BOLTZGEN_COMMIT"
    pip install -e .
    mkdir -p "$CACHE_DIR"

    echo "=== Downloading BoltzGen models ==="
    boltzgen download all --cache "$CACHE_DIR"
    touch "$CACHE_DIR/.installed"
    echo "=== Installation complete ==="
else
    echo "=== Using cached BoltzGen installation ==="
    apt-get update && apt-get install -y git build-essential
    git clone https://github.com/HannesStark/boltzgen "$BOLTZGEN_DIR"
    cd "$BOLTZGEN_DIR"
    git checkout "$BOLTZGEN_COMMIT"
    pip install -e .
fi

# Run the actual script
echo "=== Running BoltzGen ==="
python /mnt/s3/scripts/boltzgen/lyceum_boltzgen.py "$@"
