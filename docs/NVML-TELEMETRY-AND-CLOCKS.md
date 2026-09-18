# CMP100 NVML telemetry, clocks, and `gpumon`

This is the supported headless replacement for the former Xorg/Coolbits HBM
clock procedure. It uses NVIDIA's NVML API and does not start Xorg, create a
display, access raw registers, reload the driver, or flash firmware.

It was validated only on two CMP100-210 (GV100) cards with NVIDIA driver
`550.163.01`. Clock offsets and power limits are volatile across a driver
reload or reboot. Treat every other GPU, VBIOS and driver as unvalidated.

## Build

Install a Rust compiler plus the NVIDIA driver development files. On Debian:

```bash
sudo apt install rustc build-essential libnvidia-ml-dev libncurses-dev \
  libcurl4-openssl-dev libjson-c-dev libsystemd-dev
rustc -O tools/cmp100-nvml-clock-v2.rs -o cmp100-nvml-clock-v2 -l nvidia-ml
gcc -O2 -Wall -Wextra -Wpedantic -o gpumon tools/gpumon_v3_llama.c \
  -lnvidia-ml -lncurses -lcurl -ljson-c -lsystemd -lm
```

The source only calls NVML. It deliberately has no Xorg/NV-CONTROL, Nouveau,
raw BAR, VBIOS, firmware, or kernel-module path.

## Optional CUPTI 11.4 capability probe

Recent CUPTI versions reject CMP devices for legacy event collection. The
source `tools/cupti_legacy_probe.c` is a non-collecting compatibility probe
for an already installed CUPTI 11.4 library. It creates no event group and
does not attach to an inference process; it only enumerates event domains and
metric names. Build it against the exact CUPTI 11.4 path on the host:

```bash
gcc -O2 -I/usr/include tools/cupti_legacy_probe.c -o cupti-legacy-probe \
  /path/to/libcupti.so.11.4 -lcuda
LD_LIBRARY_PATH=/path/to /path/to/cupti-legacy-probe
```

On the tested CMP100/driver-550 baseline, this returned six event domains and
176 legacy metrics, including Tensor functional-unit utilization and DRAM
read/write counters. This does not make a system-wide monitor: legacy CUPTI
collects counters for its own CUDA context. Integrating live inference metrics
needs instrumentation of that workload and should be tested separately.

## Read-only telemetry

```bash
./cmp100-nvml-clock-v2 telemetry --json
./cmp100-nvml-clock-v2 fields
./cmp100-nvml-clock-v2 samples
./cmp100-nvml-clock-v2 processes
./gpumon
```

`telemetry` is the concise operational snapshot. `fields` preserves the
result of all NVML field IDs 1..199, including unsupported/unreturned fields;
it never silently maps a failed field to zero. `samples` returns NVML historic
sample streams. `processes` returns compute/graphics PIDs and NVML-attributed
VRAM usage.

`gpumon` is the lightweight terminal view. The useful CMP100 fields are GPU
and memory-controller utilization, VRAM, HBM clock, HBM temperature and its
85 C threshold, power, PCIe state and clock-event reasons. NVML memory
utilization is controller activity, not measured HBM GB/s. NVML/CUPTI on this
CMP configuration does not provide Tensor Core utilization or real DRAM
read/write bandwidth; do not label a proxy as either.

## Controls are dry-run first

All device changes are selected by UUID. Without `--apply`, commands only
validate and print a transaction preview:

```bash
nvidia-smi --query-gpu=index,uuid,name --format=csv
```

```bash
./cmp100-nvml-clock-v2 set-power-limit \
  --uuid GPU-... --watts 120
./cmp100-nvml-clock-v2 set-hbm-offset \
  --uuid GPU-... --raw-offset 138
./cmp100-nvml-clock-v2 set-core-offset \
  --uuid GPU-... --raw-offset 0
```

Appending `--apply` performs exactly one NVML setter call followed by a
readback. Setters may require `sudo` or root. Use them only on hardware you
own, with recovery access, after a stable baseline. An offset is a raw NVML
VF value, not MHz. The observed
NVML offset ranges on CMP100 can be inconsistent; do not treat them as a
portable safety envelope. Power-limit validation uses the live NVML minimum
and maximum constraints.

Example deliberate change:

```bash
./cmp100-nvml-clock-v2 set-hbm-offset \
  --uuid GPU-... --raw-offset 138 --apply
```

Verify after every change:

```bash
./cmp100-nvml-clock-v2 telemetry --json
nvidia-smi --query-gpu=index,clocks.current.memory,temperature.memory,power.limit \
  --format=csv
```

Do not automatically reapply a setting after a driver lifecycle event.
Re-read the UUID, raw offset, effective memory clock, temperature and active
workload first.

To undo an offset, deliberately set its raw value to zero:

```bash
sudo ./cmp100-nvml-clock-v2 set-hbm-offset --uuid GPU-... --raw-offset 0 --apply
sudo ./cmp100-nvml-clock-v2 set-core-offset --uuid GPU-... --raw-offset 0 --apply
```

This repository publishes source and build instructions, not prebuilt
executables. `gpumon`'s optional Ollama/llama integrations contain local
service names and paths; the terminal NVML monitor itself does not need them.
