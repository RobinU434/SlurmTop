#!/bin/bash
#SBATCH --job-name=slurmtop-build
#SBATCH --partition=cpu-galvani
#SBATCH --time=08:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --output=logs/slurmtop-build-%j.out
#SBATCH --error=logs/slurmtop-build-%j.err

# Adjust the partition/time/mem above to match your site defaults.

set -euo pipefail

module load singularity || echo "[warn] singularity module not found; assuming it is already available"

REPO_DIR=${SLURMTOP_REPO:-$PWD}
DEF_FILE=${SLURMTOP_DEF:-$REPO_DIR/container/SlurmTop.def}
SIF_FILE=${SLURMTOP_SIF:-$REPO_DIR/container/SlurmTop.sif}
LOG_DIR=${SLURMTOP_LOG_DIR:-$REPO_DIR/logs}
CARGO_CACHE_HOST=${SLURMTOP_CARGO_HOME:-$REPO_DIR/.cargo-cache}
PY_BUILD=${SLURMTOP_PY_BUILD:-0}
if [ -n "${TMPDIR:-}" ]; then
    TMPDIR_HOST="$TMPDIR"
    TMPDIR_CONT="$TMPDIR_HOST"
else
    TMPDIR_HOST="$REPO_DIR/.tmp"
    TMPDIR_CONT="/workspace/.tmp"
fi

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$SIF_FILE")"
mkdir -p "$CARGO_CACHE_HOST"
mkdir -p "$TMPDIR_HOST"

if [ ! -f "$SIF_FILE" ]; then
    echo "[info] Building Singularity image at $SIF_FILE"
    singularity build --fakeroot "$SIF_FILE" "$DEF_FILE"
else
    echo "[info] Reusing existing image $SIF_FILE"
fi

# Bind repo + Slurm config dir so squeue/sacct remain available
SINGULARITY_BIND="${SINGULARITY_BIND:-/etc/slurm:/etc/slurm}"

CARGO_CACHE_CONT="/workspace${CARGO_CACHE_HOST#$REPO_DIR}"

singularity exec \
    --bind "$REPO_DIR:/workspace" \
    --bind "$CARGO_CACHE_HOST:$CARGO_CACHE_CONT" \
    --bind "$TMPDIR_HOST:$TMPDIR_CONT" \
    --bind "$SINGULARITY_BIND" \
    --pwd /workspace \
    --env CARGO_HOME="$CARGO_CACHE_CONT" \
    --env TMPDIR="$TMPDIR_CONT" \
    "$SIF_FILE" \
    cargo build --release

if [ "$PY_BUILD" = "1" ]; then
    singularity exec \
        --bind "$REPO_DIR:/workspace" \
        --bind "$CARGO_CACHE_HOST:$CARGO_CACHE_CONT" \
        --bind "$TMPDIR_HOST:$TMPDIR_CONT" \
        --bind "$SINGULARITY_BIND" \
        --pwd /workspace \
        --env CARGO_HOME="$CARGO_CACHE_CONT" \
        --env TMPDIR="$TMPDIR_CONT" \
        "$SIF_FILE" \
        slurmtop-python-build
fi
