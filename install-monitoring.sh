#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX=${PREFIX:-/usr/local}
CC=${CC:-cc}
RUSTC=${RUSTC:-rustc}

for command_name in "$CC" "$RUSTC" install mktemp; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "missing required command: $command_name" >&2
        exit 1
    fi
done

BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/cmp100-monitoring.XXXXXX")
cleanup() {
    rm -rf -- "$BUILD_DIR"
}
trap cleanup EXIT HUP INT TERM

"$RUSTC" --edition=2021 -O "$ROOT_DIR/tools/cmp100-nvml-clock-v2.rs" \
    -o "$BUILD_DIR/cmp100-nvml-clock" -l nvidia-ml

"$CC" -O2 -std=c11 -Wall -Wextra -Wpedantic -Werror \
    "$ROOT_DIR/tools/gpumon_v3_llama.c" \
    -o "$BUILD_DIR/gpumon" \
    -lnvidia-ml -lncursesw -lcurl -ljson-c -lsystemd -lm

install -d -m 0755 "$PREFIX/bin"
install -m 0755 "$BUILD_DIR/cmp100-nvml-clock" "$PREFIX/bin/cmp100-nvml-clock"
install -m 0755 "$BUILD_DIR/gpumon" "$PREFIX/bin/gpumon"

echo "installed: $PREFIX/bin/cmp100-nvml-clock"
echo "installed: $PREFIX/bin/gpumon"
echo "no GPU setting was changed"
