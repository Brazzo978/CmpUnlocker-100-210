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
rustc --edition=2021 -O tools/cmp100-nvml-clock-v2.rs \
  -o cmp100-nvml-clock-v2 -l nvidia-ml
gcc -O2 -Wall -Wextra -Wpedantic -o gpumon tools/gpumon_v3_llama.c \
  -lnvidia-ml -lncursesw -lcurl -ljson-c -lsystemd -lm
```

Or build and install both tools without changing any GPU setting:

```bash
sudo ./install-monitoring.sh
```

The installer also places `cmp100-hbm-877.service`, but does not start or
enable it. Complete the manual validation here before considering the
[optional boot profile](DEBIAN13-BOOT-PROFILE.md).

The source only calls NVML. It deliberately has no Xorg/NV-CONTROL, Nouveau,
raw BAR, VBIOS, firmware, or kernel-module path.

## Optional legacy-CUPTI capability probe

Recent CUPTI versions reject CMP devices for legacy event collection. The
source `tools/cupti_legacy_probe.c` is a non-collecting compatibility probe
for an already installed legacy-capable CUPTI library. It creates no event
group and does not attach to an inference process; it reports the runtime and
compile API versions and enumerates event domains and metric names for every
CUDA device. Build it against the exact header/library pairing on the host:

```bash
gcc -O2 -Wall -Wextra -Wpedantic -Werror \
  -I/path/to/headers tools/cupti_legacy_probe.c -o cupti-legacy-probe \
  /path/to/libcupti.so.11.4 -lcuda
LD_LIBRARY_PATH=/directory/containing/library ./cupti-legacy-probe
```

On the tested CMP100/driver-550 baseline, the library shipped with Nsight
Systems 2024.6.2 returned six event domains and 176 legacy metrics on both
GPUs, including Tensor functional-unit utilization and DRAM read/write
counters. Its `.11.4` SONAME does not mean that CUDA Toolkit 11.4 must replace
the host toolkit. No NVIDIA library is distributed here.

The pinned optional `llama-server` patch instruments the workload's own CUDA
contexts and publishes those metrics for `gpumon`. See
[Live CUPTI metrics inside Unsloth llama-server](LLAMA-CUPTI-LIVE-METRICS.md).

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

`gpumon` is the lightweight terminal view. The useful NVML fields are GPU
and memory-controller utilization, VRAM, HBM clock, HBM temperature and its
85 C threshold, power, PCIe state and clock-event reasons. NVML memory
utilization is controller activity, not measured HBM GB/s. External NVML does
not provide Tensor utilization or real HBM read/write bandwidth on this CMP
configuration. The optional instrumented legacy-CUPTI path does provide the
documented counters, in two non-simultaneous banks; it is not an NVML proxy.

In `gpumon`, press `c` to switch between the compact GPU page and detailed
CUPTI state. `LIVE` is a current sample, `HOLD` is retained history while the
server or bank is idle, and `STALE` means an enabled bank has not produced a
fresh sample within the expected interval. JSON consumers should use each
sample's `state`, `current`, `bank`, `sampledAtUnixMs`, `ageMs`, and `unit`.
Per-device CUPTI data is matched to NVML by PCI bus ID; it is never silently
assigned by list position on a multi-GPU system.

## Simple settings workflow

`gpumon` deliberately stays read-only. Keeping privileged writes in the Rust
helper avoids password prompts inside ncurses and makes every requested change
auditable as a JSON transaction. The normal workflow is:

```bash
# 1. Copy the UUID shown for the intended card.
cmp100-nvml-clock list

# 2. Inspect both cards and the current effective HBM clock.
cmp100-nvml-clock status --json

# 3. Preview the validated 877 MHz HBM profile for one UUID.
cmp100-nvml-clock profile --uuid GPU-... --name hbm-877

# 4. Apply only after reviewing the preview.
sudo cmp100-nvml-clock profile --uuid GPU-... --name hbm-877 --apply
```

The `hbm-877` profile is the easy path: it changes only the HBM raw offset to
the validated `+138` value, requires the tested CMP100-210 identity, driver and
VBIOS by default, reads the offset back, and verifies an effective 877 MHz
memory clock. A failed setter, readback or clock verification returns non-zero
and triggers a best-effort rollback.

For unattended use, `cmp100-hbm-877.service` can also apply an optional power
limit from `/etc/default/cmp100-hbm-877`:

```bash
CMP100_HBM_UUIDS="GPU-... GPU-..."
# Optional; uncomment only after validating the desired value manually.
# CMP100_POWER_LIMIT_WATTS=150
```

The service uses the Rust NVML setter directly, applies the same absolute limit
to each allow-listed UUID, checks the per-device NVML range and verifies the
final value. The option is commented and inactive in a new installation.

The tested cards reported a stock 810 MHz maximum and repeatedly operated at
877 MHz with this profile. That result is evidence for those cards, not a
guarantee for every GV100 board or HBM2 device. Different memory binning, board
power and cooling remain the operator's risk; the boot unit intentionally does
not expose arbitrary offsets.

### Experimental HBM offset map

The following effective bins were observed during manual validation on the same
two cards. They are published as experimental data, not as safe
profiles or boot recommendations. The driver quantizes several raw offsets to
the same effective clock:

| Raw HBM offset | Observed effective clock |
| ---: | ---: |
| `0` | 810 MHz (stock CMP/firmware operating point) |
| `138` | 877 MHz |
| `184` | 891 MHz |
| `200..212` | 904 MHz |
| `214..240` | 918 MHz |
| `242..266` | 931 MHz |
| `268..294` | 945 MHz |
| `296..320` | 958 MHz |
| `322..348` | 972 MHz |
| `350..374` | 985 MHz |
| `376..382` | 999 MHz |

Only offset `138` / 877 MHz has a named, identity-gated profile and boot unit.
Higher bins require deliberate manual `tune`, temperature/error monitoring and
independent stability testing. A successful clock readback alone does not prove
data integrity or long-duration stability.

Advanced settings remain explicit but can be previewed together as one
transaction:

```bash
cmp100-nvml-clock tune --uuid GPU-... \
  --hbm-offset 138 --core-offset 0 --watts 120 --allow-out-of-range

sudo cmp100-nvml-clock tune --uuid GPU-... \
  --hbm-offset 138 --core-offset 0 --watts 120 \
  --allow-out-of-range --apply
```

`tune` validates every supplied value before the first write. Power is checked
against the live NVML constraints. The driver-reported clock-offset limits on
CMP100 can be unreliable, so a value outside them is rejected unless the user
also supplies the conspicuous `--allow-out-of-range` override. If a later
write fails, already changed settings are rolled back in reverse order.

Reset only the settings you select; power returns to the NVML default:

```bash
cmp100-nvml-clock reset --uuid GPU-... --hbm --core --power
sudo cmp100-nvml-clock reset --uuid GPU-... --hbm --core --power --apply
```

## Controls are dry-run first

All device changes are selected by UUID. Without `--apply`, commands only
validate and print a transaction preview:

```bash
nvidia-smi --query-gpu=index,uuid,name --format=csv
```

```bash
./cmp100-nvml-clock-v2 tune --uuid GPU-... --watts 120
./cmp100-nvml-clock-v2 tune --uuid GPU-... --core-offset 0
```

Appending `--apply` performs the validated transaction followed by exact
readback. Setters may require `sudo` or root. Use them only on hardware you
own, with recovery access, after a stable baseline. An offset is a raw NVML
VF value, not MHz. The observed
NVML offset ranges on CMP100 can be inconsistent; do not treat them as a
portable safety envelope. Power-limit validation uses the live NVML minimum
and maximum constraints.

Example deliberate change:

```bash
sudo ./cmp100-nvml-clock-v2 profile \
  --uuid GPU-... --name hbm-877 --apply
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
sudo ./cmp100-nvml-clock-v2 reset --uuid GPU-... --hbm --core --apply
```

This repository publishes source and build instructions, not prebuilt
executables. `gpumon`'s optional Ollama/llama integrations contain local
service names and paths; the terminal NVML monitor itself does not need them.
