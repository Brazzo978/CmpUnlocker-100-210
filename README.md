# NVIDIA CMP 100-210 volatile unlock and monitoring tools

This repository provides experimental, volatile and fail-closed tooling for a
narrowly validated NVIDIA CMP 100-210 target. It covers Tensor enablement,
PCIe Gen2 retraining, headless NVML telemetry and control, terminal monitoring,
and optional legacy-CUPTI metrics for llama.cpp.

The procedures do not flash a VBIOS or program eFuses; reset or power loss
restores stock state. Write-capable procedures are not claimed portable beyond
the exact hardware and software gates below. See [Disclosure boundary](DISCLOSURE.md).

## Hardware compatibility

| PCI device ID | Shipped identity | Known unlock or conversion | Reference |
| --- | --- | --- | --- |
| `10de:1d84` | CMP 100-210, PG500 SKU 110 | Unlocked variant: Tensor, PCIe Gen2 and HBM 877 MHz | **This repository** |
| `10de:1df4` | CMP 100-210, PG500 SKU 111 | Strap mod to Tesla V100; a complete software unlock is also documented | [duggasco/CMP100-210](https://github.com/duggasco/CMP100-210) |
| `10de:1dc1` | CMP 100-200 | Strap mod to Titan V | External hardware modification |
| `10de:1db4` | Tesla V100 PCIe 16 GB | Already a V100; no CMP restriction to lift | Reference identity |
| other | Other CMP or GV100 board | Not validated by this project | Not allow-listed |

Only the `10de:1d84` software path is implemented by this repository. The
`1df4` link points to an independent project, while both strap-mod entries are
hardware conversions rather than features of this code. Read-only telemetry
may return useful data on other NVML devices, but that does not make any write
path supported. Do not bypass model, PCI-ID, firmware, driver, VBIOS, BDF or
UUID gates.

## Deployment paths

| Deployment | Debian side | Proxmox side |
| --- | --- | --- |
| Bare metal | Install the Tensor helper, the local Gen2 helper provided by `install-pcie-guest.sh`, and the optional monitoring/HBM tools | Do not install `install-pcie-host.sh` |
| PCI passthrough VM | Install and validate the same helpers inside the Debian guest | Optionally install `install-pcie-host.sh` on the physical Proxmox node for root/endpoint verification and QEMU Guest Agent coordinated recovery |

## Start here

- [Debian 13: one-time Tensor unlock](docs/DEBIAN13-TENSOR-ONESHOT.md)
- [Debian 13: one-time PCIe Gen2 unlock](docs/DEBIAN13-PCIE-GEN2-ONESHOT.md)
- [Optional guest/Proxmox Gen2 automation](docs/PCIE-AUTOMATION.md)
- [NVML telemetry, headless clocks, and gpumon](docs/NVML-TELEMETRY-AND-CLOCKS.md)
- [Live legacy-CUPTI metrics inside Unsloth llama-server](docs/LLAMA-CUPTI-LIVE-METRICS.md)
- [Optional coordinated boot profile](docs/DEBIAN13-BOOT-PROFILE.md)

The Tensor, PCIe and HBM guides default to manual application. Their systemd
components are installed but not enabled automatically. Optional guest/Proxmox
recovery and boot automation are separate, explicitly risky procedures with a
documented console recovery path. HBM telemetry and clock controls use the
separate NVML binary, are dry-run by default, and require an explicit `--apply`
for a device change. Use the exact tested baseline stated in each guide.

## Results

| Area | Status | Tested result |
| --- | --- | --- |
| FP16 Tensor path | **Working on the tested baseline** | Two CMP100-210 GPUs reached median results of 74.179 and 75.040 TFLOPS on an `8192 x 8192` FP16 GEMM |
| PCIe Gen2 | **Working on two tested x1 paths** | Both endpoints negotiated Gen2 x1; pinned 32 MiB transfers were approximately 417/419 MB/s versus a 207/209 MB/s stock-control measurement |
| PCIe Gen3 | **Not achieved** | Making the software policy request Gen3 was insufficient: the endpoint continued to expose a 5 GT/s maximum and rejected the 8 GT/s target before any observable equalization |
| PCIe width | **Still open** | Both tested cards remained x1, including behind Gen3 x8- and Gen3 x16-capable upstream paths; no x16 result is claimed |
| NVML telemetry and HBM control | **Working on the tested baseline** | Headless NVML reads HBM temperature, 85 C threshold, clocks, power, memory and PCIe state; a UUID-targeted raw HBM offset of +138 read back as 877 MHz on the tested cards |

All published interventions are volatile. Resetting or power-cycling restores
the stock state.

## Evidence

- [Tensor result and benchmark parameters](docs/TENSOR-RESULTS.md)
- [PCIe Gen2 and width measurements](docs/PCIE-RESULTS.md)
- [Gen3 negative result](docs/GEN3-LIMIT.md)
- [NVML telemetry, headless clocks, and gpumon](docs/NVML-TELEMETRY-AND-CLOCKS.md)
- [Live CUPTI metrics and reproducible Unsloth patch](docs/LLAMA-CUPTI-LIVE-METRICS.md)
- [Test methodology and limitations](docs/METHODOLOGY.md)
- [Captured Tensor benchmark output](results/tensor-benchmark.txt)
- [Evidence checksums](results/SHA256SUMS)

Operational components:

- `install.sh` builds and installs the Tensor oneshot helper;
- `install-pcie-guest.sh` builds and installs the separate Gen2 helper;
- `tools/build_payloads.py` derives tested artifacts from exact, locally
  installed NVIDIA firmware; no NVIDIA firmware blob is distributed;
- `src/gv100_nouveau_acr_hook.c` is the narrow kernel hook used for the tested
  Nouveau ACR handoff;
- `scripts/cmp100-tensor-unlock` and `scripts/cmp100-pcie-gen2` implement the
  fail-closed, volatile operations;
- `tools/cmp100-nvml-clock-v2.rs` is the headless NVML telemetry/control
  source; controls are UUID-targeted and dry-run unless `--apply` is supplied;
- `tools/cupti_legacy_probe.c` is the non-mutating compatibility probe for a
  locally installed legacy-capable CUPTI library;
- `patches/llama.cpp/0001-server-add-optional-legacy-CUPTI-metric-collector.patch`
  is the pinned, optional Unsloth `llama-server` instrumentation patch;
- `tools/gpumon_v3_llama.c` is the terminal NVML monitor source, including
  HBM temperature/threshold, clock-event reporting and schema-3 CUPTI input;
- `install-monitoring.sh` builds and installs the Rust NVML helper and
  `gpumon`, plus a disabled HBM boot unit, without changing GPU settings;
- the Tensor, PCIe and HBM systemd units provide bounded manual execution and
  optional boot integration. No installer enables them automatically;
- the optional Proxmox coordinator verifies the physical root and endpoint
  links and invokes the guest helper through QEMU Guest Agent only when needed.

Validation components:

- `tools/benchmark_tensor.py` runs a CUDA FP16 GEMM benchmark.
- `tools/collect_state.sh` prints standard system, NVIDIA and PCIe state to
  standard output.
- `tools/check_public_boundary.py` enforces an exact public file allowlist and
  rejects secrets, firmware blobs and unreviewed laboratory files.

## Validated software baseline

- NVIDIA CMP 100-210 (`10de:1d84`);
- NVIDIA driver `550.163.01`;
- VBIOS `88.00.9D.00.00` for the named HBM profile;
- CUDA `12.4` and PyTorch `2.6.0+cu124` for the captured Tensor result;
- Debian GNU/Linux 13 for bare-metal and PCI-passthrough guest operation.

## Safety and scope

This repository performs privileged, write-capable GPU operations and
temporarily unbinds drivers. Use it only on CMP100-210 hardware you own and can
reboot, with a recovery console available. Never bypass device, firmware,
payload, module or BDF checks. A failed operation can wedge the GPU until a
guest or host reboot.

Do not interpret the tested results as a guarantee that another PCB revision,
firmware version, kernel or driver will behave identically. PCIe generation
and width are independent properties: the Gen2 result does not imply x16
support, and the negative Gen3 result does not prove the physical GV100 PHY
incapable of 8 GT/s.

Please report new security-sensitive findings privately as described in
[SECURITY.md](SECURITY.md). Do not attach NVIDIA firmware, VBIOS images,
credentials or private infrastructure data to a public issue.

## License

Code in this repository is licensed under GPL-2.0. Evidence and third-party
product names remain subject to their respective owners' rights. NVIDIA, CUDA,
Tesla and related names are trademarks of NVIDIA Corporation. This project is
independent and is not endorsed by NVIDIA.
