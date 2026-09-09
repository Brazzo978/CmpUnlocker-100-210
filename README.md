# NVIDIA CMP100-210 research results

Independent experimental results for NVIDIA CMP100-210 (`10de:1d84`, GV100),
focused on Tensor throughput and PCI Express link behavior.

This repository publishes measurements, limitations and **read-only**
verification tools. It intentionally does not contain the privileged firmware
write primitive, payload-construction code, driver hooks, installation scripts
or an operational unlock procedure. See [Disclosure boundary](DISCLOSURE.md).

## Results

| Area | Status | Tested result |
| --- | --- | --- |
| FP16 Tensor path | **Validated after a volatile research intervention** | Two CMP100-210 GPUs reached median results of 74.179 and 75.040 TFLOPS on an `8192 x 8192` FP16 GEMM |
| PCIe Gen2 | **Validated on two cards** | Both endpoints negotiated Gen2 x1; pinned 32 MiB transfers were approximately 417/419 MB/s versus a 207/209 MB/s stock-control measurement |
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

The included tools cannot modify GPU firmware, PCI configuration space, BARs,
drivers or services:

- `tools/benchmark_tensor.py` runs a CUDA FP16 GEMM benchmark.
- `tools/collect_state.sh` prints standard system, NVIDIA and PCIe state to
  standard output.
- `tools/check_public_boundary.py` enforces the publication allowlist and
  rejects write-capable or binary artifacts.

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

The repository does not enable an unlock. Do not interpret the published
measurements as a guarantee that another board, firmware version or driver will
behave identically. PCIe generation and link width are independent properties;
the Gen2 result does not imply x16 support, and the negative Gen3 result does
not prove the physical GV100 PHY incapable of 8 GT/s.

Please report security-sensitive findings privately as described in
[SECURITY.md](SECURITY.md), rather than opening a public issue with operational
firmware or privileged-write details.

## License

Code in this repository is licensed under GPL-2.0. Evidence and third-party
product names remain subject to their respective owners' rights. NVIDIA, CUDA,
Tesla and related names are trademarks of NVIDIA Corporation. This project is
independent and is not endorsed by NVIDIA.
