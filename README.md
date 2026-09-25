# NVIDIA CMP 100-210 volatile unlock and monitoring tools

This repository provides experimental, volatile and fail-closed tooling for a
narrowly validated NVIDIA CMP 100-210 target. It covers Tensor enablement,
PCIe Gen2 retraining, headless NVML telemetry and control, terminal monitoring,
and optional legacy-CUPTI metrics for llama.cpp.

The supported Tensor, Gen2 and HBM procedures do not flash a VBIOS or program
eFuses; reset or power loss restores their stock state. Separate x16 research
involved persistent SPI edits and did **not** produce a usable GPU. Write-capable
procedures are not claimed portable beyond the exact hardware and software
gates below. See [Disclosure boundary](DISCLOSURE.md).

## Hardware compatibility

| PCI device ID | Shipped identity | Known unlock or conversion | Reference |
| --- | --- | --- | --- |
| `10de:1d84` | CMP 100-210, PG500 SKU 110 | Unlocked variant: Tensor, PCIe Gen2 and HBM 877 MHz | **This repository** |
| `10de:1df4` | CMP 100-210, PG500 SKU 111 | Strap mod to Tesla V100; a complete software unlock is also documented | [duggasco/CMP100-210](https://github.com/duggasco/CMP100-210) |
| `10de:1dc1` | CMP 100-200 | Strap mod to Titan V | External hardware modification |
| `10de:1db4` | Tesla V100 PCIe 16 GB | Already a V100; no CMP restriction to lift | Reference identity |
| other | Other CMP or GV100 board | Not validated by this project | Not allow-listed |


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
| FP16 Tensor path | **Working** | Two CMP100-210 GPUs reached median results of 74.179 and 75.040 TFLOPS on an `8192 x 8192` FP16 GEMM |
| PCIe Gen2 | **Working** | Both endpoints negotiated Gen2 x1; pinned 32 MiB transfers were approximately 417/419 MB/s versus a 207/209 MB/s stock-control measurement |
| PCIe Gen3 | **Most probably HW fused** | No change can be applied , everything gets reverted to g2 , probably a fuse on the chip. |
| PCIe width | **Unresolved** | Two tested IFR edits on one `1d84` card with added lane capacitors trained Gen1 x16; NVIDIA did not initialize it. No x16 CUDA result. See the negative-result report below. |
| NVML telemetry and HBM control | **Working on the tested baseline** | Custom rust binary to read telemetry and write offset to clock and stuff |

The supported Tensor, Gen2 and HBM interventions are volatile. The x16
experiments were persistent flash edits and are reported as negative results,
not as a working or supported unlock.

In-band SPI erase/program was demonstrated on one `1d84` card through our
privileged-write vulnerability path. That capability's code is available only
as a **private research release**, not in this public repository.

## Evidence

- [Tensor result and benchmark parameters](docs/TENSOR-RESULTS.md)
- [PCIe Gen2 and width measurements](docs/PCIE-RESULTS.md)
- [CMP100 `1d84` x16 experiments: physical link achieved, GPU init failed](docs/PCIE-X16-1D84-NEGATIVE-2026-09-25.md)
- [Gen3 negative result](docs/GEN3-LIMIT.md)
- [NVML telemetry, headless clocks, and gpumon](docs/NVML-TELEMETRY-AND-CLOCKS.md)
- [Live CUPTI metrics and reproducible Unsloth patch](docs/LLAMA-CUPTI-LIVE-METRICS.md)
- [Test methodology and limitations](docs/METHODOLOGY.md)
- [Captured Tensor benchmark output](results/tensor-benchmark.txt)
- [Evidence checksums](results/SHA256SUMS)

Researchers interested in continuing the unresolved `1d84` x16 investigation
can [contact me privately](SECURITY.md); I can share access to the private
research repository with interested collaborators. The public x16 report
explains what was tested and what remains unknown.

## Validated software baseline

- NVIDIA CMP 100-210 (`10de:1d84`);
- NVIDIA driver `550.163.01`(Users achieved it on newer driver too);
- VBIOS `88.00.9D.00.00` for the named HBM profile;
- CUDA `12.4` and PyTorch `2.6.0+cu124` for the captured Tensor result;
- Debian GNU/Linux 13 for bare-metal and PCI-passthrough guest operation(Users achieved on ubuntu too).

## Safety and scope

This repository performs privileged, write-capable GPU operations and
temporarily unbinds drivers. Use it only on CMP100-210 hardware you own and can
reboot, with a recovery console available. Never bypass device, firmware,
payload, module or BDF checks. A failed operation can wedge the GPU until a
guest or host reboot.

Do not interpret the tested results as a guarantee that another PCB revision,
firmware version, kernel or driver will behave identically.

Please report new security-sensitive findings privately as described in
[SECURITY.md](SECURITY.md). Do not attach NVIDIA firmware, VBIOS images,
credentials or private infrastructure data to a public issue.

## License

Code in this repository is licensed under GPL-2.0. Evidence and third-party
product names remain subject to their respective owners' rights. NVIDIA, CUDA,
Tesla and related names are trademarks of NVIDIA Corporation. This project is
independent and is not endorsed by NVIDIA.
