# NVIDIA CMP100-210 unlock and research

Open-source experimental tooling and independently measured results for NVIDIA
CMP100-210 (`10de:1d84`, GV100), focused on Tensor throughput and PCI Express
link behavior.

The supported procedures are volatile: they do not flash the VBIOS or program
eFuses, and a reset or power cycle restores stock state. The repository now
publishes the minimum operational implementation needed to reproduce the
validated Tensor and PCIe Gen2 results. See [Disclosure boundary](DISCLOSURE.md).

## Start here

- [Debian 13: one-time Tensor unlock](docs/DEBIAN13-TENSOR-ONESHOT.md)
- [Debian 13: one-time PCIe Gen2 unlock](docs/DEBIAN13-PCIE-GEN2-ONESHOT.md)

Both guides default to a single manual application. They install systemd
oneshot units but do not enable them at boot. Use the exact Debian 13,
NVIDIA `550.163.01` and GV100 firmware baseline documented in the guides.

## Results

| Area | Status | Tested result |
| --- | --- | --- |
| FP16 Tensor path | **Working on the tested baseline** | Two CMP100-210 GPUs reached median results of 74.179 and 75.040 TFLOPS on an `8192 x 8192` FP16 GEMM |
| PCIe Gen2 | **Working on two tested x1 paths** | Both endpoints negotiated Gen2 x1; pinned 32 MiB transfers were approximately 417/419 MB/s versus a 207/209 MB/s stock-control measurement |
| PCIe Gen3 | **Not achieved** | Making the software policy request Gen3 was insufficient: the endpoint continued to expose a 5 GT/s maximum and rejected the 8 GT/s target before any observable equalization |
| PCIe width | **Still open** | Both tested cards remained x1, including behind Gen3 x8- and Gen3 x16-capable upstream paths; no x16 result is claimed |

The interventions used during the private experiment were volatile. Resetting
or power-cycling restored the stock state.

## Evidence

- [Tensor result and benchmark parameters](docs/TENSOR-RESULTS.md)
- [PCIe Gen2 and width measurements](docs/PCIE-RESULTS.md)
- [Gen3 negative result](docs/GEN3-LIMIT.md)
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
- the two systemd units provide bounded manual execution and optional boot
  integration. The tutorials do not enable them automatically.

Validation components:

- `tools/benchmark_tensor.py` runs a CUDA FP16 GEMM benchmark.
- `tools/collect_state.sh` prints standard system, NVIDIA and PCIe state to
  standard output.
- `tools/check_public_boundary.py` enforces an exact public file allowlist and
  rejects secrets, firmware blobs and unreviewed laboratory files.

## Tested environment

- two NVIDIA CMP100-210 GPUs (`10de:1d84`);
- NVIDIA driver `550.163.01`;
- CUDA `12.4` and PyTorch `2.6.0+cu124` for the captured Tensor run;
- Debian GNU/Linux 13 for the final bare-metal/guest validation;
- HPE ProLiant DL380 Gen9 test platform;
- independent upstream paths capable of Gen3 x8 and Gen3 x16.

The platform also operated an NVMe device at Gen3 x4 on the relevant riser, so
the common CMP link limit was not attributed to a server-wide Gen1/Gen2 cap.

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
