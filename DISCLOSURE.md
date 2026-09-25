# Disclosure boundary

This public repository contains the reviewed operational Tensor/Gen2/HBM path
and the findings of the broader `1d84` investigation. The private laboratory
repository retains dangerous experimental writers and sensitive raw artifacts.

## Published

- hardware and software test environment;
- benchmark parameters and captured output;
- standard `nvidia-smi` and PCIe state;
- before/after performance measurements;
- negative results and limitations;
- read-only collection and benchmark tools;
- the exact-firmware payload builder for the supported baseline;
- the narrow Nouveau ACR kernel hook;
- fail-closed Tensor and PCIe Gen2 runtime helpers;
- installation scripts, oneshot units and one-time Debian 13 tutorials;
- the headless NVML telemetry/control source and HBM-aware terminal monitor;
- the non-mutating CUPTI capability probe and pinned optional `llama-server`
  instrumentation patch, without NVIDIA libraries or prebuilt llama binaries;
- hashes of the published evidence.
- capability and negative-result reports for topology/SM, Gen3, x16,
  ACR/PLM, Falcon, InfoROM, SPI and P2P, with their evidence boundaries;
- reviewed non-mutating analysis tools.

## Still withheld

- raw logs containing machine identities or unreviewed sensitive content;
- firmware, VBIOS, ROM and prebuilt kernel-module binaries;
- experimental driver interception, retrain-hammer and flash tools that can
  mutate or wedge a GPU;
- private infrastructure, access information and machine-specific mappings;
- private inputs, proprietary dumps and work-in-progress material that has
  not had a fresh disclosure review.
- the demonstrated `1d84` in-band SPI erase/program implementation and its
  operational material; access is limited to a private research release.

No NVIDIA firmware blob is stored here. Supported payloads are derived locally
only after exact firmware-size and SHA-256 checks. The operational disclosure
is intentional, but it is not an invitation to add unrelated privileged
techniques without review. Submit new security-sensitive material through the
private channel described in [SECURITY.md](SECURITY.md).
