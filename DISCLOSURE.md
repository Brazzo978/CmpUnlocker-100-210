# Disclosure boundary

This public repository is a research-results and validation archive. It is
deliberately separated from the private laboratory repository.

## Published

- hardware and software test environment;
- benchmark parameters and captured output;
- standard `nvidia-smi` and PCIe state;
- before/after performance measurements;
- negative results and limitations;
- read-only collection and benchmark tools;
- hashes of the published evidence.

## Temporarily withheld

- the privileged GPU firmware write primitive;
- construction or modification of firmware payloads;
- code that intercepts or changes driver/firmware execution;
- exact proprietary write sequences used during the experiments;
- automation that performs an unlock, driver handoff or PCIe retrain;
- unpublished raw logs containing implementation-specific call paths;
- installable services, kernel modules and binary artifacts.

The omission is intentional while the security impact is evaluated. The public
claims are limited to measurements that can be checked from standard GPU/PCIe
state or reproduced after a researcher independently reaches the same state.

Publishing a result here must never be interpreted as permission to add an
operational bypass. Submit security-sensitive technical material through a
private channel described in [SECURITY.md](SECURITY.md).
