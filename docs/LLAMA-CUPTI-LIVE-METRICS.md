# Live CUPTI metrics inside Unsloth `llama-server`

This optional patch instruments the CUDA contexts owned by `llama-server` so
legacy CUPTI Event/Metric API counters can be reported on CMP100-210. It is the
producer for the schema-3 JSON consumed by `gpumon`.

The patch is intentionally pinned to the public Unsloth release
`b10715-mix-86bd2d3`, whose tag resolves to commit
`df9d4a507d5123dfc1a6ae6e96b85af4644ef64b`. The installed bundle used for the
live validation identifies its embedded source as `92cedc867`; that object is
not itself the public release tag to check out. Apply the patch to the tag,
not to an arbitrary newer `llama.cpp` tree.

No NVIDIA library, CUDA header, model, or prebuilt `llama-server` is included
in this repository. The files added to the MIT-licensed upstream tree retain
an MIT SPDX identifier; the surrounding CMPUnlocker repository remains
GPL-2.0-only.

## What is measured

The collector publishes these legacy metrics for every CUDA device used by the
server:

| Bank | Metrics | Unit |
| --- | --- | --- |
| `core_dram` | `sm_efficiency` | percent |
| `core_dram` | `achieved_occupancy` | ratio from 0 to 1 |
| `core_dram` | `ipc` | scalar |
| `core_dram` | `dram_read_throughput` | bytes per second |
| `core_dram` | `dram_write_throughput` | bytes per second |
| `core_dram` | `dram_utilization` | level from 0 to 10 |
| `tensor` | `tensor_precision_fu_utilization` | level from 0 to 10 |

The two banks are **not simultaneous**. All six `core_dram` metrics are read
together, while the Tensor metric conflicts and uses a separate event set.
The enabled bank remains unchanged for the entire request. Rotation occurs
only after `active_requests` returns to zero, so the collector never mutates
the PMU configuration during decode.

Consequences:

- an inactive-bank value is a retained historical sample;
- values with different timestamps are not from the same measurement window;
- `gpumon` labels samples as `live`, `hold`, `stale`, or `unavailable` and
  exposes the timestamp, age, bank, enabled state, unit, and window duration;
- a Tensor level of zero can be a valid measurement and is distinct from an
  unavailable metric.

Each `devices[]` record also includes the CUDA device's stable PCI bus ID.
`gpumon` matches its NVML devices by that BDF, not by CUDA/NVML enumeration
order, so `CUDA_VISIBLE_DEVICES` cannot silently move a sample to the wrong
card. On a multi-GPU host, a missing, malformed, duplicate or unmatched BDF
leaves that CUPTI sample unavailable. The only ordinal fallback is the
unambiguous case of exactly one report device and one NVML device.

## Tested baseline

- two NVIDIA CMP100-210 GPUs (`10de:1d84`, GV100 / SM 7.0);
- NVIDIA driver `550.163.01`;
- Unsloth release `b10715-mix-86bd2d3`;
- CUDA compiler 12.4 for the clean reproduction build;
- headers in `/usr/include`, reporting `CUPTI_API_VERSION 22`;
- legacy-capable `libcupti.so.11.4` supplied by the installed Nsight Systems
  2024.6.2 package.

The `.11.4` suffix is the library SONAME. It does **not** prove that the file
came from CUDA Toolkit 11.4, and this guide does not recommend replacing the
host CUDA toolkit. The exact library/header pairing must first pass the probe
below.

## 1. Find and probe the local CUPTI library

Set paths for the library and headers already installed on the machine:

```bash
export CUPTI_INCLUDE_DIR=/usr/include
export CUPTI_LIBRARY=/path/to/legacy-capable/libcupti.so.11.4
export CUPTI_LIBRARY_DIR="$(dirname "$CUPTI_LIBRARY")"
```

Build and run the capability probe:

```bash
gcc -O2 -Wall -Wextra -Wpedantic -Werror \
  -I"$CUPTI_INCLUDE_DIR" tools/cupti_legacy_probe.c \
  -o cupti-legacy-probe "$CUPTI_LIBRARY" -lcuda

LD_LIBRARY_PATH="$CUPTI_LIBRARY_DIR" ./cupti-legacy-probe
```

The tested cards returned six event domains and 176 legacy metrics on both
devices. A non-zero probe exit status means the collector should not be built.

Confirm the file actually loaded by the probe rather than relying on its name:

```bash
LD_DEBUG=libs LD_LIBRARY_PATH="$CUPTI_LIBRARY_DIR" \
  ./cupti-legacy-probe 2>&1 | grep -F libcupti
```

## 2. Check out the pinned source and apply the patch

```bash
git clone https://github.com/unslothai/llama.cpp.git llama-cupti
cd llama-cupti
git checkout --detach b10715-mix-86bd2d3
test "$(git rev-parse HEAD)" = df9d4a507d5123dfc1a6ae6e96b85af4644ef64b

git apply --check \
  /path/to/CmpUnlocker-100-210/patches/llama.cpp/0001-server-add-optional-legacy-CUPTI-metric-collector.patch
git apply \
  /path/to/CmpUnlocker-100-210/patches/llama.cpp/0001-server-add-optional-legacy-CUPTI-metric-collector.patch
```

The patch contains only the optional CMake switch, the collector source, and
the server lifecycle/request hooks. It does not contain the unrelated local
research selectors or machine-specific paths used during development.

## 3. Build

First verify that the patched tree still builds with CUPTI disabled:

```bash
cmake -S . -B build-off \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=70-real \
  -DBUILD_SHARED_LIBS=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_UI=OFF \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build-off --target llama-server -j"$(nproc)"
```

Then build the instrumented server:

```bash
cmake -S . -B build-cupti \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=70-real \
  -DBUILD_SHARED_LIBS=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_SERVER=ON \
  -DLLAMA_BUILD_UI=OFF \
  -DLLAMA_CUPTI_LEGACY=ON \
  -DLLAMA_CUPTI_INCLUDE_DIR="$CUPTI_INCLUDE_DIR" \
  -DLLAMA_CUPTI_LIBRARY="$CUPTI_LIBRARY" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build-cupti --target llama-server -j"$(nproc)"
```

Check the resulting dependency and runpath before installation:

```bash
readelf -d build-cupti/bin/llama-server | grep -E 'NEEDED|RPATH|RUNPATH'
ldd build-cupti/bin/llama-server | grep -E 'cupti|cuda|not found'
```

Do not replace a working inference binary yet. Start the new server on a
separate port with a small test model, check `/health`, complete an inference,
and inspect the report first.

## 4. Report path and sampling interval

The collector reads two optional environment variables:

```text
LLAMA_CUPTI_REPORT_PATH=/run/llama-cupti.json
LLAMA_CUPTI_WINDOW_MS=250
```

The interval accepts 50 through 5000 ms. The default 250 ms was used for the
live validation. Faster UI refresh does not create faster CUPTI samples.

For a non-root systemd service, create a writable runtime directory rather
than writing directly under `/run`:

```ini
[Service]
RuntimeDirectory=llama-cupti
RuntimeDirectoryMode=0750
Environment=LLAMA_CUPTI_REPORT_PATH=/run/llama-cupti/report.json
Environment=LLAMA_CUPTI_WINDOW_MS=250
```

Point `gpumon` at the same file:

```bash
GPUMON_CUPTI_REPORT_PATH=/run/llama-cupti/report.json gpumon
```

Give every concurrent `llama-server` instance a distinct report path. A shared
path causes the instances to replace each other's JSON atomically.

## 5. Validation

At idle, the report is expected to contain `active_requests: 0`; retained
samples must appear as `hold` in `gpumon`, not as current activity. During a
request, verify that timestamps advance approximately every configured window:

```bash
watch -n 0.25 \
  'jq "{active_requests, devices: [.devices[] | {ordinal, active_bank, samples}]}" /run/llama-cupti/report.json'
```

Useful monitor commands:

```bash
gpumon
gpumon -j | jq '.gpus[].cupti'
```

In the TUI, press `c` to toggle the compact GPU view and the CUPTI detail page.
The JSON `samples` objects are authoritative. Legacy flat numeric fields are
`null` unless their sample is current.

Before trusting a new build, also check:

- one long request and two overlapping `--parallel` requests;
- client cancellation and an invalid request;
- clean SIGTERM;
- no bank change while `active_requests` is non-zero;
- no NVIDIA XID in the kernel log;
- output correctness and throughput against the uninstrumented build.

This repository does not claim negligible overhead without a controlled A/B
measurement on the target workload.

## Rollback

Stop the instrumented server and restore the previously saved Unsloth
`llama-server` directory or repoint `UNSLOTH_LLAMA_CPP_PATH` to it. The CUPTI
patch does not alter the model, GPU firmware, driver, or persistent system
state. Removing the report file is optional; `gpumon` will otherwise classify
an old retained sample as stale/held rather than current.
