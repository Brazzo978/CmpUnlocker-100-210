# Disclosure boundary

This public repository contains the reviewed, minimum operational path needed
to reproduce the Tensor and PCIe Gen2 results. It remains deliberately
separated from the broader private laboratory repository.

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
- hashes of the published evidence.

## Still withheld

- unpublished raw logs containing implementation-specific call paths;
- firmware, VBIOS, ROM and prebuilt kernel-module binaries;
- experimental Gen3 driver interception and unpublished offsets;
- broad diagnostic and retrain-hammer tooling not required by the supported
  Tensor or Gen2 path;
- private infrastructure, access information and machine-specific mappings;
- work-in-progress reverse-engineering material that has not had a fresh
  disclosure review.

No NVIDIA firmware blob is stored here. Supported payloads are derived locally
only after exact firmware-size and SHA-256 checks. The operational disclosure
is intentional, but it is not an invitation to add unrelated privileged
techniques without review. Submit new security-sensitive material through the
private channel described in [SECURITY.md](SECURITY.md).
