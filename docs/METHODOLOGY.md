# Methodology and evidence standard

## Evidence hierarchy

Claims in this repository distinguish among:

1. standard externally visible state (`lspci`, `nvidia-smi`);
2. measured transfer or compute performance;
3. controlled internal observations retained in the private laboratory archive;
4. hypotheses that have not yet been experimentally distinguished.

A software maximum alone is not treated as proof of a physical PCIe
transition. A link-generation claim requires the negotiated Link Status on both
ends and a bandwidth result consistent with that state. A Tensor claim requires
successful CUDA execution, finite output and repeated timed samples.

## Controls

- Two CMP100-210 devices were compared where possible.
- Independent upstream paths were used for the PCIe work.
- Root-port capabilities were recorded separately from endpoint capabilities.
- PCIe errors and NVIDIA Xid events were checked around intrusive private tests.
- Restoration and final-state checks were required after those tests.
- Negative observations are reported as bounded to the tested hardware,
  firmware and driver versions.

## Public evidence

The public archive intentionally contains standard-tool output, benchmark data,
summaries and hashes rather than the privileged mechanism used to establish an
experimental state. The complete raw laboratory evidence is retained privately.

Hashes in `results/SHA256SUMS` cover only the artifacts actually published in
this repository. They do not imply that withheld code can be reconstructed or
audited from the public tree.

## Limitations

- The Tensor benchmark is a focused GEMM measurement, not a complete
  application benchmark suite.
- PCIe width remained x1 on the tested cards.
- Gen3 was not achieved.
- The source of the read-only Gen2 capability image is unresolved.
- Results on another CMP100 PCB revision, VBIOS or driver may differ.
