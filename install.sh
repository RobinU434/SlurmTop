#!/bin/bash

# Inside the repo on the cluster
mkdir -p .cargo
singularity exec \
  --bind $(pwd):/workspace \
  --pwd /workspace \
  --env CARGO_HOME=/workspace/.cargo \
  container/SlurmTop.sif \
  cargo build --release
rm -rf .cargo

# Rebuild the Python wheel if you distribute via pip
CONDA_ROOT=/mnt/lustre/work/martius/mot824/.conda
PY_ENV=$CONDA_ROOT/envs/slurmtop
singularity exec \
  --bind $(pwd):/workspace \
  --bind ${CONDA_ROOT}:${CONDA_ROOT} \
  --pwd /workspace \
  --env CARGO_HOME=/workspace/.cargo \
  container/SlurmTop.sif \
  maturin build --release --features python \
    --interpreter ${PY_ENV}/bin/python \
    --compatibility manylinux_2_28
${PY_ENV}/bin/pip install --force-reinstall target/wheels/slurmtop-0.1.0-cp312-cp312-manylinux_2_28_x86_64.whl